"""Quality-control reporting for decoded disks.

This module inspects reconstructed sectors across every track/head pair and
produces both structured and human-readable summaries. Future iterations can
augment the analysis with additional metrics such as flux jitter, index
variance, or PLL stability across revolutions.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import List

from ..decoding import Decoder
from ..exceptions import FluxDecodeError
from ..models import LayoutDescriptor, SCPImage
from ..sector.models import Sector, TrackSectors
from ..sector.reconstruct import build_track_sectors

# Sectors with confidence lower than this threshold are treated as "weak" in the
# QC report. This can be surfaced in future UI work alongside jitter and
# drop-out metrics.
WEAK_CONFIDENCE_THRESHOLD = 0.7


@dataclass
class TrackQC:
    """Per-track quality metrics for a decoded disk.

    ``bad_sectors`` counts sectors with missing or invalid data (CRC failures
    or empty data). Missing sectors are tracked separately.
    """

    track: int
    head: int
    total_sectors: int
    good_sectors: int
    weak_sectors: int
    missing_sectors: int
    no_data_sectors: int
    bad_sectors: int
    crc_errors: int
    confidence: float

    def to_dict(self) -> dict:
        """Return a dictionary representation suitable for JSON serialisation."""

        return asdict(self)


@dataclass
class DiskQCReport:
    """Aggregate QC results across all decoded tracks."""

    tracks: List[TrackQC]
    overall_confidence: float
    missing_tracks: int
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Return the QC report as a JSON-friendly dictionary."""

        return {
            "tracks": [track.to_dict() for track in self.tracks],
            "overall_confidence": self.overall_confidence,
            "missing_tracks": self.missing_tracks,
            "notes": self.notes,
        }

    def to_json(self) -> str:
        """Serialise the QC report to a formatted JSON string."""

        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_json(cls, payload: str) -> "DiskQCReport":
        """Create a QC report from a JSON string produced by :meth:`to_json`."""

        data = json.loads(payload)
        tracks = [TrackQC(**track) for track in data.get("tracks", [])]
        return cls(
            tracks=tracks,
            overall_confidence=data.get("overall_confidence", 0.0),
            missing_tracks=data.get("missing_tracks", 0),
            notes=data.get("notes", []),
        )


def _infer_expected_sector_count(track_sectors: List[Sector]) -> int:
    """Infer how many sectors should exist on a track.

    The heuristic uses the largest sector ID as the expected count when sector
    IDs are present, otherwise falls back to the number of decoded sectors. A
    future version may incorporate layout metadata or interleave rules for
    non-IBM encodings (e.g., GCR, FM, hard-sectored media).
    """

    if not track_sectors:
        return 0
    sector_ids = [sector.sector_id for sector in track_sectors if sector.sector_id is not None]
    if sector_ids:
        return max(sector_ids)
    return len(track_sectors)


def _compute_missing_tracks(image: SCPImage, layout: LayoutDescriptor | None, track_step: int) -> int:
    """Estimate how many tracks are absent within the observed range."""

    if not image.tracks:
        return 0
    track_ids = sorted({track.track for track in image.tracks})
    if not track_ids:
        return 0
    if layout:
        expected_tracks = layout.tracks * layout.sides
        logical_ids = {track_id // max(track_step, 1) for track_id in track_ids}
        present = len([tid for tid in logical_ids if tid < expected_tracks])
        missing = expected_tracks - present
        return max(missing, 0)
    expected_tracks = track_ids[-1] - track_ids[0] + 1
    missing = expected_tracks - len(track_ids)
    return max(missing, 0)


def _estimate_sectors_per_track(image: SCPImage) -> int:
    """Estimate expected sectors per track using filename hints and track counts."""

    track_total = len(image.tracks)
    if track_total == 0:
        return 0
    match = re.search(r"(\d+(?:\.\d+)?)\s*([KkMm])", image.path.name)
    if match:
        value = float(match.group(1))
        unit = match.group(2).lower()
        capacity_bytes = value * (1024 ** (1 if unit == "k" else 2))
        estimated = int(round(capacity_bytes / (track_total * 512)))
        if estimated > 0:
            return estimated
    return 9


def _summarize_track_sectors(track_sectors: TrackSectors, missing: int) -> dict:
    """Compute per-track QC counts from reconstructed sectors."""

    sectors = track_sectors.sectors
    data_sectors = [sector for sector in sectors if sector.data]
    no_data = len([sector for sector in sectors if not sector.data])
    crc_errors = len([sector for sector in data_sectors if not sector.crc_ok])
    good = len([sector for sector in data_sectors if sector.crc_ok])
    weak = len(
        [sector for sector in data_sectors if sector.confidence < WEAK_CONFIDENCE_THRESHOLD]
    )
    bad = no_data + crc_errors
    confidence = sum(sector.confidence for sector in data_sectors) / len(data_sectors) if data_sectors else 0.0

    return {
        "good": good,
        "weak": weak,
        "no_data": no_data,
        "crc_errors": crc_errors,
        "bad": bad,
        "confidence": confidence,
        "missing": missing,
    }


def _resolve_expected_and_missing(
    track_sectors: TrackSectors,
    layout: LayoutDescriptor | None,
    logical_track: int,
    expected_hint: int,
) -> tuple[int, int]:
    decoded_ids = {sector.sector_id for sector in track_sectors.sectors if sector.data}
    expected_layout = layout.expected_sectors_for_track(logical_track) if layout else None
    inferred = _infer_expected_sector_count(track_sectors.sectors)
    expected = expected_layout or (inferred or expected_hint or 0)
    missing = max(expected - len(decoded_ids), 0) if expected else 0
    if expected_layout and track_sectors.missing:
        if track_sectors.missing == missing:
            missing = track_sectors.missing
    return expected, missing


def build_qc_report(
    image: SCPImage,
    decoder: Decoder,
    layout: LayoutDescriptor | None = None,
    track_step: int = 1,
) -> DiskQCReport:
    """Analyse an image and build a QC report.

    Each track/head pair is decoded using the supplied ``decoder`` and the first
    available revolution. Sectors are reconstructed and tallied, with CRC
    failures and low-confidence decodes highlighted. Tracks that fail to decode
    are represented with zero confidence and a single bad sector placeholder so
    that the CLI and report writers can flag the issue clearly.
    """

    track_reports: List[TrackQC] = []
    expected_hint = _estimate_sectors_per_track(image)
    encoding = layout.encoding if layout else getattr(decoder, "encoding", None)
    for track_flux in image.tracks:
        logical_track = track_flux.track // max(track_step, 1)
        if layout and logical_track >= layout.tracks * layout.sides:
            continue
        try:
            if not track_flux.revolutions:
                raise FluxDecodeError("No revolutions present for track")
            track_sectors = build_track_sectors(
                track_flux.revolutions[0],
                decoder,
                cylinder=track_flux.track,
                head=track_flux.side,
                expected_sectors=layout.expected_sectors_for_track(logical_track) if layout else expected_hint or None,
                encoding=encoding,
            )
            expected, missing = _resolve_expected_and_missing(
                track_sectors,
                layout,
                logical_track,
                expected_hint,
            )
            summary = _summarize_track_sectors(track_sectors, missing)
            good = summary["good"]
            weak = summary["weak"]
            bad = summary["bad"]
            crc_errors = summary["crc_errors"]
            no_data = summary["no_data"]
            confidence = summary["confidence"]
        except Exception:
            expected = layout.expected_sectors_for_track(logical_track) if layout else expected_hint
            good = 0
            weak = 0
            bad = expected or 1
            crc_errors = bad
            missing = 0
            no_data = 0
            confidence = 0.0

        track_reports.append(
            TrackQC(
                track=track_flux.track,
                head=track_flux.side,
                total_sectors=expected,
                good_sectors=good,
                weak_sectors=weak,
                missing_sectors=missing,
                no_data_sectors=no_data,
                bad_sectors=bad,
                crc_errors=crc_errors,
                confidence=confidence,
            )
        )

    overall_confidence = (
        sum(track.confidence for track in track_reports) / len(track_reports) if track_reports else 0.0
    )
    missing_tracks = _compute_missing_tracks(image, layout, track_step)
    notes = ["bad_sectors includes no_data + crc_errors"]
    return DiskQCReport(
        tracks=track_reports,
        overall_confidence=overall_confidence,
        missing_tracks=missing_tracks,
        notes=notes,
    )


def write_qc_report_text(report: DiskQCReport, path: Path, layout: LayoutDescriptor | None = None) -> None:
    """Write a human-readable QC report to ``path``."""

    lines = [
        "Fluxctl QC Report",
        f"Tracks analysed: {len(report.tracks)}",
        f"Overall confidence: {report.overall_confidence:.2f}",
        f"Missing tracks: {report.missing_tracks}",
    ]
    if report.notes:
        lines.append("Notes:")
        lines.extend([f"- {note}" for note in report.notes])
    if layout:
        lines.append(f"Layout: {layout.layout_id}")
        if layout.encoding == "gcr":
            lines.append("Note: Commodore GCR checksums are validated when present.")
    lines.extend(["", "Per-track breakdown:"])
    for track in sorted(report.tracks, key=lambda t: (t.track, t.head)):
        lines.append(
            " "
            f"Track {track.track:02d} Head {track.head}: "
            f"total={track.total_sectors} good={track.good_sectors} weak={track.weak_sectors} "
            f"missing={track.missing_sectors} no_data={track.no_data_sectors} "
            f"bad={track.bad_sectors} crc_errors={track.crc_errors} conf={track.confidence:.2f}"
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def write_qc_report_json(report: DiskQCReport, path: Path) -> None:
    """Write a machine-readable QC report to ``path``."""

    path.write_text(report.to_json(), encoding="utf-8")


__all__ = [
    "DiskQCReport",
    "TrackQC",
    "WEAK_CONFIDENCE_THRESHOLD",
    "build_qc_report",
    "write_qc_report_json",
    "write_qc_report_text",
]
