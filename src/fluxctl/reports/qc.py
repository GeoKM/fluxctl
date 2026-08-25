"""Quality-control reporting for decoded disks.

This module inspects reconstructed sectors across every track/head pair and
produces both structured and human-readable summaries. Future iterations can
augment the analysis with additional metrics such as flux jitter, index
variance, or PLL stability across revolutions.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import List

from ..decoding import Decoder
from ..cbm_dos_errors import cbm_dos_error_for_sector, is_cbm_dos_layout
from ..exceptions import FluxDecodeError
from ..models import LayoutDescriptor, SCPImage, TrackFlux
from ..output import atomic_write_text
from ..sector.models import Sector, TrackSectors
from ..sector.reconstruct import build_track_sectors_from_revolutions

# Sectors with confidence lower than this threshold are treated as "weak" in the
# QC report. Lowered to 0.5 to avoid flagging otherwise clean captures as
# suspect while still surfacing genuinely noisy decodes.
WEAK_CONFIDENCE_THRESHOLD = 0.5


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
    cbm_dos_errors: dict[str, int] = field(default_factory=dict)

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
    status: str = "unknown"
    suspect_sectors: int = 0
    total_sectors: int = 0
    total_good_sectors: int = 0
    total_bad_sectors: int = 0
    total_weak_sectors: int = 0
    total_missing_sectors: int = 0
    cbm_dos_errors: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Return the QC report as a JSON-friendly dictionary."""

        return {
            "tracks": [track.to_dict() for track in self.tracks],
            "overall_confidence": self.overall_confidence,
            "missing_tracks": self.missing_tracks,
            "notes": self.notes,
            "status": self.status,
            "suspect_sectors": self.suspect_sectors,
            "total_sectors": self.total_sectors,
            "total_good_sectors": self.total_good_sectors,
            "total_bad_sectors": self.total_bad_sectors,
            "total_weak_sectors": self.total_weak_sectors,
            "total_missing_sectors": self.total_missing_sectors,
            "cbm_dos_errors": self.cbm_dos_errors,
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
            status=data.get("status", "unknown"),
            suspect_sectors=data.get("suspect_sectors", 0),
            total_sectors=data.get("total_sectors", 0),
            total_good_sectors=data.get("total_good_sectors", 0),
            total_bad_sectors=data.get("total_bad_sectors", 0),
            total_weak_sectors=data.get("total_weak_sectors", 0),
            total_missing_sectors=data.get("total_missing_sectors", 0),
            cbm_dos_errors=data.get("cbm_dos_errors", {}),
        )


def _summarize_disk(tracks: List[TrackQC], missing_tracks: int, *, trim_trailing_empty: bool = True) -> dict:
    """Aggregate per-track QC into disk-level counters and status."""

    # Ignore trailing tracks that decoded nothing but bad sectors; these are
    # often empty over-captures beyond the real cylinder range (common on
    # 40-track media imaged as 42 tracks). Trim only trailing all-bad tracks.
    if trim_trailing_empty:
        last_useful = None
        for idx, track in enumerate(tracks):
            if track.good_sectors or track.weak_sectors:
                last_useful = idx
        trimmed = tracks if last_useful is None else tracks[: last_useful + 1]
    else:
        trimmed = tracks

    total_sectors = sum(track.total_sectors for track in trimmed)
    total_good = sum(track.good_sectors for track in trimmed)
    total_bad = sum(track.bad_sectors for track in trimmed)
    total_weak = sum(track.weak_sectors for track in trimmed)
    total_missing = sum(track.missing_sectors for track in trimmed)
    suspect = total_bad + total_missing + total_weak
    status = "good" if suspect == 0 and missing_tracks == 0 else "suspect"

    return {
        "total_sectors": total_sectors,
        "total_good": total_good,
        "total_bad": total_bad,
        "total_weak": total_weak,
        "total_missing": total_missing,
        "suspect": suspect,
        "status": status,
    }


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
        expected_entries = layout.tracks * layout.sides
        present = 0
        for track in image.tracks:
            logical_track = track.track // max(track_step, 1)
            if logical_track < layout.tracks:
                present += 1
        missing = expected_entries - present
        return max(missing, 0)
    expected_tracks = track_ids[-1] - track_ids[0] + 1
    missing = expected_tracks - len(track_ids)
    return max(missing, 0)


def _summarize_track_sectors(
    track_sectors: TrackSectors,
    missing: int,
    *,
    cbm_dos: bool = False,
) -> dict:
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
    cbm_errors: dict[str, int] = {}
    if cbm_dos:
        for sector in sectors:
            error = cbm_dos_error_for_sector(sector)
            if error:
                cbm_errors[str(error.code)] = cbm_errors.get(str(error.code), 0) + 1
        if missing:
            cbm_errors["22"] = cbm_errors.get("22", 0) + missing

    return {
        "good": good,
        "weak": weak,
        "no_data": no_data,
        "crc_errors": crc_errors,
        "bad": bad,
        "confidence": confidence,
        "missing": missing,
        "cbm_dos_errors": cbm_errors,
    }


def _resolve_expected_and_missing(
    track_sectors: TrackSectors,
    layout: LayoutDescriptor | None,
    logical_track: int,
    expected_hint: int,
) -> tuple[int, int]:
    decoded_ids = {sector.sector_id for sector in track_sectors.sectors if sector.data}
    layout_id = getattr(layout, "layout_id", "")
    if layout_id.startswith("amiga_"):
        expected = layout.sectors_per_track
        missing = max(expected - len(track_sectors.sectors), 0)
    else:
        expected_layout = layout.expected_sectors_for_track(logical_track, track_sectors.head) if layout else None
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
    operation=None,
) -> DiskQCReport:
    """Analyse an image and build a QC report.

    Each track/head pair is decoded using the supplied ``decoder`` across all
    available revolutions. Sectors are reconstructed and tallied, with CRC
    failures and low-confidence decodes highlighted. Tracks that fail to decode
    are represented with zero confidence and a single bad sector placeholder so
    that the CLI and report writers can flag the issue clearly.
    """

    track_reports: List[TrackQC] = []
    # With no selected layout, expected sector counts come only from decoded
    # sector IDs. Unknown geometry must not be inferred from the source name.
    expected_hint = 0
    encoding = layout.encoding if layout else getattr(decoder, "encoding", None)
    cbm_dos = is_cbm_dos_layout(getattr(layout, "layout_id", None))
    track_total = len(image.tracks)
    for track_index, track_flux in enumerate(image.tracks, start=1):
        if operation is not None:
            operation.checkpoint("track", track_index, track_total)
        logical_track = track_flux.track // max(track_step, 1)
        if layout and (logical_track >= layout.tracks or track_flux.side >= layout.sides):
            continue
        try:
            if not track_flux.revolutions:
                raise FluxDecodeError("No revolutions present for track")
            if layout and layout.layout_id.startswith("amiga_"):
                from ..sector.reconstruct_amiga import (
                    reconstruct_amiga_track,
                    reconstruct_amiga_greaseweazle,
                    reconstruct_amiga_with_pll,
                )

                if operation is not None:
                    operation.checkpoint("candidate decoder", track_index, track_total)
                candidate = reconstruct_amiga_greaseweazle(
                    track_flux.revolutions, track_flux.track, track_flux.side, timebase_ns=image.timebase_ns
                )
                if candidate is None:
                    if operation is not None:
                        operation.checkpoint("candidate decoder fallback", track_index, track_total)
                    candidate = reconstruct_amiga_with_pll(
                        track_flux.revolutions, track_flux.track, track_flux.side, timebase_ns=image.timebase_ns
                    )
                track_sectors = candidate
            else:
                track_sectors = build_track_sectors_from_revolutions(
                    track_flux.revolutions,
                    decoder,
                    cylinder=track_flux.track,
                    head=track_flux.side,
                    expected_sectors=layout.expected_sectors_for_track(logical_track, track_flux.side)
                    if layout
                    else expected_hint or None,
                    encoding=encoding,
                    timebase_ns=image.timebase_ns,
                    operation=operation,
                )
            expected, missing = _resolve_expected_and_missing(
                track_sectors,
                layout,
                logical_track,
                expected_hint,
            )
            summary = _summarize_track_sectors(track_sectors, missing, cbm_dos=cbm_dos)
            good = summary["good"]
            weak = summary["weak"]
            bad = summary["bad"]
            crc_errors = summary["crc_errors"]
            no_data = summary["no_data"]
            confidence = summary["confidence"]
            cbm_errors = summary["cbm_dos_errors"]
        except Exception:
            if operation is not None:
                operation.checkpoint("cancelled")
            expected = layout.expected_sectors_for_track(logical_track, track_flux.side) if layout else expected_hint
            good = 0
            weak = 0
            bad = expected or 1
            crc_errors = bad
            missing = 0
            no_data = 0
            confidence = 0.0
            cbm_errors = {"22": expected or 1} if cbm_dos else {}

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
                cbm_dos_errors=cbm_errors,
            )
        )

    overall_confidence = (
        sum(track.confidence for track in track_reports) / len(track_reports) if track_reports else 0.0
    )
    missing_tracks = _compute_missing_tracks(image, layout, track_step)
    notes = ["bad_sectors includes no_data + crc_errors"]
    cbm_errors: dict[str, int] = {}
    for track in track_reports:
        for code, count in track.cbm_dos_errors.items():
            cbm_errors[code] = cbm_errors.get(code, 0) + count
    if cbm_errors:
        notes.append("CBM DOS errors are inferred from decoded sector evidence; controller-only errors are not guessed")

    disk_summary = _summarize_disk(track_reports, missing_tracks, trim_trailing_empty=layout is None)
    return DiskQCReport(
        tracks=track_reports,
        overall_confidence=overall_confidence,
        missing_tracks=missing_tracks,
        notes=notes,
        status=disk_summary["status"],
        suspect_sectors=disk_summary["suspect"],
        total_sectors=disk_summary["total_sectors"],
        total_good_sectors=disk_summary["total_good"],
        total_bad_sectors=disk_summary["total_bad"],
        total_weak_sectors=disk_summary["total_weak"],
        total_missing_sectors=disk_summary["total_missing"],
        cbm_dos_errors=cbm_errors,
    )


def build_qc_report_from_tracks(
    tracks: List[TrackSectors],
    layout: LayoutDescriptor | None = None,
    track_step: int = 1,
) -> DiskQCReport:
    """QC report builder for already-decoded track/sector images.

    This variant is used for flat images (e.g., IMG/ADF/D81) that have been
    reconstructed into TrackSectors without needing flux decoding.
    """

    image = SCPImage(path=Path(""), version=0, revolutions_per_track=0, timebase_ns=0.0, tracks=[])
    image.tracks = [TrackFlux(track=ts.track, side=ts.head, revolutions=[]) for ts in tracks]
    track_reports: List[TrackQC] = []
    expected_hint = 0
    for ts in tracks:
        logical_track = ts.track // max(track_step, 1)
        expected, missing = _resolve_expected_and_missing(ts, layout, logical_track, expected_hint)
        cbm_dos = is_cbm_dos_layout(getattr(layout, "layout_id", None))
        summary = _summarize_track_sectors(ts, missing, cbm_dos=cbm_dos)
        track_reports.append(
            TrackQC(
                track=ts.track,
                head=ts.head,
                total_sectors=expected,
                good_sectors=summary["good"],
                weak_sectors=summary["weak"],
                missing_sectors=missing,
                no_data_sectors=summary["no_data"],
                bad_sectors=summary["bad"],
                crc_errors=summary["crc_errors"],
                confidence=summary["confidence"],
                cbm_dos_errors=summary["cbm_dos_errors"],
            )
        )

    overall_confidence = (
        sum(track.confidence for track in track_reports) / len(track_reports) if track_reports else 0.0
    )
    # Flat images are already materialised as complete track/sector rows. When a
    # shorter concrete format (for example a standard 35-track D64) is matched
    # against a superset preservation layout, trailing layout-only tracks should
    # not make an otherwise clean image suspect.
    missing_tracks = _compute_missing_tracks(image, None, track_step)
    disk_summary = _summarize_disk(track_reports, missing_tracks, trim_trailing_empty=layout is None)
    cbm_errors: dict[str, int] = {}
    for track in track_reports:
        for code, count in track.cbm_dos_errors.items():
            cbm_errors[code] = cbm_errors.get(code, 0) + count
    return DiskQCReport(
        tracks=track_reports,
        overall_confidence=overall_confidence,
        missing_tracks=missing_tracks,
        notes=["bad_sectors includes no_data + crc_errors"],
        status=disk_summary["status"],
        suspect_sectors=disk_summary["suspect"],
        total_sectors=disk_summary["total_sectors"],
        total_good_sectors=disk_summary["total_good"],
        total_bad_sectors=disk_summary["total_bad"],
        total_weak_sectors=disk_summary["total_weak"],
        total_missing_sectors=disk_summary["total_missing"],
        cbm_dos_errors=cbm_errors,
    )


def write_qc_report_text(
    report: DiskQCReport,
    path: Path,
    layout: LayoutDescriptor | None = None,
    *,
    overwrite: bool = False,
) -> None:
    """Write a human-readable QC report to ``path``."""

    lines = [
        "Fluxctl QC Report",
        f"Tracks analysed: {len(report.tracks)}",
        f"Overall confidence: {report.overall_confidence:.2f}",
        f"Missing tracks: {report.missing_tracks}",
        f"Status: {report.status}",
        f"Sectors: total={report.total_sectors} good={report.total_good_sectors} "
        f"weak={report.total_weak_sectors} missing={report.total_missing_sectors} "
        f"bad={report.total_bad_sectors} suspect={report.suspect_sectors}",
    ]
    if report.notes:
        lines.append("Notes:")
        lines.extend([f"- {note}" for note in report.notes])
    if report.cbm_dos_errors:
        lines.append("CBM DOS error codes (inferred):")
        for code, count in sorted(report.cbm_dos_errors.items(), key=lambda item: int(item[0])):
            from ..cbm_dos_errors import CBM_DOS_ERROR_MESSAGES

            lines.append(f"- {code}: {CBM_DOS_ERROR_MESSAGES.get(int(code), 'Unknown')} ({count})")
    if layout:
        lines.append(f"Layout: {layout.layout_id}")
        lines.append(f"Cylinders: {layout.tracks}")
        lines.append(f"Heads: {layout.sides}")
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
        if track.cbm_dos_errors:
            lines.append(f"  CBM DOS errors: {track.cbm_dos_errors}")
    atomic_write_text(path, "\n".join(lines), overwrite=overwrite)


def write_qc_report_json(report: DiskQCReport, path: Path, *, overwrite: bool = False) -> None:
    """Write a machine-readable QC report to ``path``."""

    atomic_write_text(path, report.to_json(), overwrite=overwrite)


__all__ = [
    "DiskQCReport",
    "TrackQC",
    "WEAK_CONFIDENCE_THRESHOLD",
    "build_qc_report",
    "build_qc_report_from_tracks",
    "write_qc_report_json",
    "write_qc_report_text",
]
