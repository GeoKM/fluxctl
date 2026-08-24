"""Read-only preservation diagnostics for an individual decoded sector."""
from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean, median
from typing import Any, Iterable, Optional, Sequence

from ..models import RevolutionFlux, SCPImage
from ..sector.models import Sector, TrackSectors
from ..sector.reconstruct import build_track_sectors_from_revolutions


def _differences(reference: bytes, candidate: bytes) -> list[dict[str, object]]:
    """Return every differing byte, including length-only differences."""

    differences: list[dict[str, object]] = []
    for offset in range(max(len(reference), len(candidate))):
        expected = reference[offset] if offset < len(reference) else None
        actual = candidate[offset] if offset < len(candidate) else None
        if expected != actual:
            differences.append(
                {
                    "offset": offset,
                    "selected": f"{expected:02x}" if expected is not None else None,
                    "candidate": f"{actual:02x}" if actual is not None else None,
                }
            )
    return differences


def _timing(revolution: RevolutionFlux) -> dict[str, object]:
    intervals = [int(value) for value in revolution.interval_ns if value > 0]
    if not intervals:
        return {"interval_count": 0}
    return {
        "interval_count": len(intervals),
        "min_ns": min(intervals),
        "max_ns": max(intervals),
        "mean_ns": round(mean(intervals), 2),
        "median_ns": median(intervals),
        "index_time_ns": revolution.index_time_ns,
    }


@dataclass(frozen=True)
class SectorCandidateDiagnostic:
    revolution: Optional[int]
    found: bool
    sector_id: int
    size: int = 0
    crc_ok: Optional[bool] = None
    deleted: Optional[bool] = None
    confidence: Optional[float] = None
    source_revolutions: tuple[int, ...] = ()
    pll: dict[str, object] = field(default_factory=dict)
    timing: dict[str, object] = field(default_factory=dict)
    differences: tuple[dict[str, object], ...] = ()
    error: str = ""


@dataclass(frozen=True)
class SectorDiagnostic:
    track: int
    head: int
    sector_id: int
    selected: Optional[SectorCandidateDiagnostic]
    candidates: tuple[SectorCandidateDiagnostic, ...]
    source_kind: str
    notes: tuple[str, ...] = ()

    def to_text(self) -> str:
        lines = [
            f"Preservation diagnostics: Track {self.track}, Head {self.head}, Sector {self.sector_id}",
            f"Source: {self.source_kind}",
            f"Candidates: {len(self.candidates)}",
        ]
        if self.selected is None:
            lines.append("Selected result: none")
        else:
            selected = self.selected
            lines.append(
                "Selected result: "
                f"size={selected.size}, crc={'ok' if selected.crc_ok else 'bad'}, "
                f"confidence={_format_float(selected.confidence)}, "
                f"source_revolutions={_format_revolutions(selected.source_revolutions)}"
            )
        for note in self.notes:
            lines.append(f"Note: {note}")
        for index, candidate in enumerate(self.candidates, start=1):
            revolution = "flat image" if candidate.revolution is None else f"revolution {candidate.revolution}"
            lines.extend(
                [
                    "",
                    f"Candidate {index} ({revolution}):",
                    f"  found={candidate.found} size={candidate.size} crc={_format_bool(candidate.crc_ok)} "
                    f"deleted={_format_bool(candidate.deleted)} confidence={_format_float(candidate.confidence)}",
                    f"  source_revolutions={_format_revolutions(candidate.source_revolutions)}",
                ]
            )
            if candidate.pll:
                lines.append("  PLL: " + _format_mapping(candidate.pll))
            if candidate.timing:
                lines.append("  Timing: " + _format_mapping(candidate.timing))
            lines.append(f"  differing_bytes={len(candidate.differences)}")
            for difference in candidate.differences:
                lines.append(
                    f"    offset {difference['offset']}: "
                    f"selected={difference['selected'] or '--'} candidate={difference['candidate'] or '--'}"
                )
            if candidate.error:
                lines.append(f"  error={candidate.error}")
        return "\n".join(lines)


def _format_bool(value: Optional[bool]) -> str:
    return "n/a" if value is None else ("ok" if value else "bad")


def _format_float(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def _format_revolutions(values: Sequence[int]) -> str:
    return ",".join(str(value) for value in values) if values else "none"


def _format_mapping(values: dict[str, object]) -> str:
    return ", ".join(f"{key}={value}" for key, value in values.items())


def _expected_sectors(layout: Any, track: int) -> Optional[int]:
    if layout is None:
        return None
    track_sectors = getattr(layout, "track_sectors", None)
    if track_sectors and 0 <= track < len(track_sectors):
        return int(track_sectors[track])
    value = getattr(layout, "sectors_per_track", None)
    return int(value) if value else None


def _candidate_from_sector(
    sector: Optional[Sector],
    requested_sector: int,
    *,
    revolution: Optional[int],
    timing: Optional[dict[str, object]] = None,
    pll: Optional[dict[str, object]] = None,
    reference: bytes = b"",
    error: str = "",
) -> SectorCandidateDiagnostic:
    if sector is None:
        return SectorCandidateDiagnostic(
            revolution=revolution,
            found=False,
            sector_id=requested_sector,
            timing=timing or {},
            pll=pll or {},
            error=error,
        )
    return SectorCandidateDiagnostic(
        revolution=revolution,
        found=bool(sector.data),
        sector_id=sector.sector_id,
        size=len(sector.data),
        crc_ok=sector.crc_ok,
        deleted=sector.deleted,
        confidence=sector.confidence,
        source_revolutions=tuple(sector.source_revolutions),
        timing=timing or {},
        pll=pll or {},
        differences=tuple(_differences(reference, sector.data)) if reference else (),
        error=error,
    )


def _metrics(decoder: Any, revolution: RevolutionFlux) -> dict[str, object]:
    """Read decoder metrics without making diagnostics depend on one decoder."""

    try:
        stream = decoder.decode_revolution(revolution)
    except Exception:
        return {}
    metrics = getattr(stream, "metrics", None)
    if metrics is None:
        return {}
    return {
        key: value
        for key in ("pll_lock_score", "rpm_estimate", "confidence")
        if (value := getattr(metrics, key, None)) is not None
    }


def build_sector_diagnostic(
    image: SCPImage,
    decoder: Any,
    layout: Any,
    track: int,
    head: int,
    sector_id: int,
) -> SectorDiagnostic:
    """Decode each available revolution and compare it with the merged result."""

    track_flux = next((item for item in image.tracks if item.track == track and item.side == head), None)
    if track_flux is None:
        return SectorDiagnostic(track, head, sector_id, None, (), "scp", ("Track/head was not present in the SCP.",))
    revolutions = tuple(revolution for revolution in track_flux.revolutions if revolution.interval_ns)
    expected = _expected_sectors(layout, track)
    try:
        merged = build_track_sectors_from_revolutions(
            revolutions,
            decoder,
            cylinder=track,
            head=head,
            expected_sectors=expected,
            encoding=getattr(layout, "encoding", None),
            timebase_ns=image.timebase_ns,
        )
        selected_sector = next((sector for sector in merged.sectors if sector.sector_id == sector_id), None)
    except Exception as exc:
        return SectorDiagnostic(track, head, sector_id, None, (), "scp", (f"Merged reconstruction failed: {exc}",))

    reference = selected_sector.data if selected_sector is not None else b""
    selected = _candidate_from_sector(
        selected_sector,
        sector_id,
        revolution=None,
        reference=b"",
        error="merged best candidate" if selected_sector is not None else "sector was not recovered",
    ) if selected_sector is not None else None
    candidates: list[SectorCandidateDiagnostic] = []
    for revolution in revolutions:
        try:
            track_result = build_track_sectors_from_revolutions(
                [revolution],
                decoder,
                cylinder=track,
                head=head,
                expected_sectors=expected,
                encoding=getattr(layout, "encoding", None),
                timebase_ns=image.timebase_ns,
            )
            candidate_sector = next((sector for sector in track_result.sectors if sector.sector_id == sector_id), None)
            candidates.append(
                _candidate_from_sector(
                    candidate_sector,
                    sector_id,
                    revolution=revolution.index,
                    timing=_timing(revolution),
                    pll=_metrics(decoder, revolution),
                    reference=reference,
                )
            )
        except Exception as exc:
            candidates.append(
                _candidate_from_sector(
                    None,
                    sector_id,
                    revolution=revolution.index,
                    timing=_timing(revolution),
                    pll=_metrics(decoder, revolution),
                    error=str(exc),
                )
            )
    return SectorDiagnostic(
        track,
        head,
        sector_id,
        selected,
        tuple(candidates),
        "scp",
        (
            "The selected result is the normal best-effort merged sector.",
            "Candidates are independent per-revolution decodes; decoder-internal PLL alternatives are not retained by the current pipeline.",
        ),
    )


def build_flat_sector_diagnostic(
    tracks: Iterable[TrackSectors],
    track: int,
    head: int,
    sector_id: int,
) -> SectorDiagnostic:
    """Build diagnostics for containers without per-revolution provenance."""

    track_result = next((item for item in tracks if item.track == track and item.head == head), None)
    sector = next((item for item in track_result.sectors if item.sector_id == sector_id), None) if track_result else None
    candidate = _candidate_from_sector(
        sector,
        sector_id,
        revolution=None,
        error="decoded track image; per-revolution timing is not available",
    ) if sector is not None else None
    return SectorDiagnostic(
        track,
        head,
        sector_id,
        candidate,
        (candidate,) if candidate is not None else (),
        "flat sector image",
        ("This container does not retain individual SCP revolutions or raw PLL timing.",),
    )


__all__ = ["SectorCandidateDiagnostic", "SectorDiagnostic", "build_sector_diagnostic", "build_flat_sector_diagnostic"]
