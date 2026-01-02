"""Quality-control reporting for decoded disks.

This module inspects reconstructed sectors across every track/head pair and
produces both structured and human-readable summaries. Future iterations can
augment the analysis with additional metrics such as flux jitter, index
variance, or PLL stability across revolutions.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List

from ..decoding import Decoder
from ..exceptions import FluxDecodeError
from ..models import LayoutDescriptor, SCPImage
from ..sector.models import Sector
from ..sector.reconstruct import build_track_sectors

# Sectors with confidence lower than this threshold are treated as "weak" in the
# QC report. This can be surfaced in future UI work alongside jitter and
# drop-out metrics.
WEAK_CONFIDENCE_THRESHOLD = 0.7


@dataclass
class TrackQC:
    """Per-track quality metrics for a decoded disk."""

    track: int
    head: int
    total_sectors: int
    good_sectors: int
    weak_sectors: int
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

    def to_dict(self) -> dict:
        """Return the QC report as a JSON-friendly dictionary."""

        return {
            "tracks": [track.to_dict() for track in self.tracks],
            "overall_confidence": self.overall_confidence,
            "missing_tracks": self.missing_tracks,
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
    for track_flux in image.tracks:
        logical_track = track_flux.track // max(track_step, 1)
        if layout and logical_track >= layout.tracks * layout.sides:
            continue
        try:
            if not track_flux.revolutions:
                raise FluxDecodeError("No revolutions present for track")
            bitstream = decoder.decode_revolution(track_flux.revolutions[0])
            if layout and layout.encoding == "gcr":
                expected = layout.expected_sectors_for_track(logical_track)
                good = 0
                weak = 0
                bad = 0
                crc_errors = 0
                confidence = bitstream.metrics.confidence or 0.0
            else:
                track_sectors = build_track_sectors(
                    track_flux.revolutions[0],
                    decoder,
                    cylinder=track_flux.track,
                    head=track_flux.side,
                    expected_sectors=layout.expected_sectors_for_track(logical_track) if layout else expected_hint or None,
                )
                expected = _infer_expected_sector_count(track_sectors.sectors) or expected_hint
                decoded_ids = {sector.sector_id for sector in track_sectors.sectors if sector.data}
                missing = max(expected - len(decoded_ids), 0)
                crc_errors = len([sector for sector in track_sectors.sectors if sector.data and not sector.crc_ok])
                weak = len(
                    [
                        sector
                        for sector in track_sectors.sectors
                        if sector.data and sector.confidence < WEAK_CONFIDENCE_THRESHOLD
                    ]
                )
                good = len([sector for sector in track_sectors.sectors if sector.data and sector.crc_ok])
                bad = missing + len([sector for sector in track_sectors.sectors if not sector.data])
                confidence = (
                    sum(sector.confidence for sector in track_sectors.sectors) / len(track_sectors.sectors)
                    if track_sectors.sectors
                    else 0.0
                )
        except Exception:
            expected = layout.expected_sectors_for_track(logical_track) if layout else expected_hint
            good = 0
            weak = 0
            bad = expected or 1
            crc_errors = bad
            confidence = 0.0

        track_reports.append(
            TrackQC(
                track=track_flux.track,
                head=track_flux.side,
                total_sectors=expected,
                good_sectors=good,
                weak_sectors=weak,
                bad_sectors=bad,
                crc_errors=crc_errors,
                confidence=confidence,
            )
        )

    overall_confidence = (
        sum(track.confidence for track in track_reports) / len(track_reports) if track_reports else 0.0
    )
    missing_tracks = _compute_missing_tracks(image, layout, track_step)
    return DiskQCReport(tracks=track_reports, overall_confidence=overall_confidence, missing_tracks=missing_tracks)


def write_qc_report_text(report: DiskQCReport, path: Path, layout: LayoutDescriptor | None = None) -> None:
    """Write a human-readable QC report to ``path``."""

    lines = [
        "Fluxctl QC Report",
        f"Tracks analysed: {len(report.tracks)}",
        f"Overall confidence: {report.overall_confidence:.2f}",
        f"Missing tracks: {report.missing_tracks}",
    ]
    if layout:
        lines.append(f"Layout: {layout.layout_id}")
        if layout.encoding == "gcr":
            lines.append("Note: GCR sector parsing is limited; totals reflect expected geometry.")
    lines.extend(["", "Per-track breakdown:"])
    for track in sorted(report.tracks, key=lambda t: (t.track, t.head)):
        lines.append(
            " "
            f"Track {track.track:02d} Head {track.head}: "
            f"total={track.total_sectors} good={track.good_sectors} weak={track.weak_sectors} "
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
