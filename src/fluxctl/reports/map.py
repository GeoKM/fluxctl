"""Disk map generation and rendering helpers.

The visualizer is intentionally lightweight: it classifies sectors into good,
weak, or bad buckets, then emits either a plain ASCII map or a concentric-ring
SVG. Multi-head visualisation and richer legends can be added later when the
decoder gains stronger support for exotic layouts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import cos, pi, sin
from typing import List, Tuple

from ..decoding import Decoder
from ..exceptions import FluxDecodeError
from ..models import LayoutDescriptor, SCPImage
from ..sector.models import TrackSectors
from ..sector.models import Sector
from ..sector.reconstruct import build_track_sectors_from_revolutions

# Slightly lower than QC to avoid over-reporting weak sectors in visuals.
WEAK_CONFIDENCE_THRESHOLD = 0.7

STATE_TO_GLYPH = {
    "good": "■",
    "weak": "□",
    "bad": "×",
    "unused": "·",
    "bam_file": "F",
    "bam_system": "S",
    "bam_used": "U",
    "bam_free": ".",
}
STATE_TO_COLOR = {
    "good": "#2ecc71",
    "weak": "#f1c40f",
    "bad": "#e74c3c",
    "unused": "#566173",
    "bam_file": "#35d07f",
    "bam_system": "#4aa3ff",
    "bam_used": "#f2c94c",
    "bam_free": "#4f5b6f",
}


@dataclass
class SectorMapEntry:
    """Per-sector metadata retained for interactive map views."""

    sector_id: int
    state: str
    size: int
    crc_ok: bool
    confidence: float
    deleted: bool = False
    has_data: bool = True


@dataclass
class DiskMap:
    """Simple classification grid for sector health across a disk.

    Attributes
    ----------
    tracks:
        Outer list is ordered by the track/head pairs encountered in the image.
        Each inner list contains only the physically expected per-sector state
        labels (``good``/``weak``/``bad``) for that track/head row. Tracks are
        not padded to the disk maximum, so zoned GCR layouts can retain their
        real sector counts.
    total_tracks:
        Total number of track/head entries represented.
    max_sectors_per_track:
        The maximum sector count observed (or inferred) across all tracks.
    track_ids:
        Optional list of ``(track, head)`` tuples mirroring ``tracks`` order.
    track_confidence:
        Optional list of average confidence values per track to surface in
        future renderings or legends.
    address_style:
        Address convention used by ``track_ids``. ``physical`` uses internal
        zero-based track rows; ``cbm_logical`` uses Commodore one-based logical
        track numbers with zero-based sectors.
    highlighted_sectors:
        Optional ``(track, head, sector_id)`` addresses to outline as the active
        file/selection overlay without changing the underlying sector state.
    """

    tracks: List[List[str]]
    total_tracks: int
    max_sectors_per_track: int
    render_style: str = "radial"
    track_ids: List[Tuple[int, int]] = field(default_factory=list)
    track_confidence: List[float] = field(default_factory=list)
    sector_details: List[List[SectorMapEntry]] = field(default_factory=list)
    address_style: str = "physical"
    highlighted_sectors: set[Tuple[int, int, int]] = field(default_factory=set)


def _sector_entry(sector: Sector) -> SectorMapEntry:
    state = _classify_sector(sector)
    return SectorMapEntry(
        sector_id=sector.sector_id,
        state=state,
        size=sector.size,
        crc_ok=sector.crc_ok,
        confidence=sector.confidence,
        deleted=sector.deleted,
        has_data=bool(sector.data),
    )


def _missing_sector_entry(sector_id: int, sector_size: int) -> SectorMapEntry:
    return SectorMapEntry(
        sector_id=sector_id,
        state="bad",
        size=sector_size,
        crc_ok=False,
        confidence=0.0,
        has_data=False,
    )


def _sector_id_base(layout: LayoutDescriptor | None, decoded: list[Sector]) -> int:
    if decoded:
        return min(sec.sector_id for sec in decoded)
    if layout:
        return int(layout.id_rules.get("sector_number_base", 1))
    return 1


def _classify_sector(sector: Sector) -> str:
    """Classify a sector into good/weak/bad buckets."""

    if not sector.data or not sector.crc_ok:
        return "bad"
    if sector.confidence < WEAK_CONFIDENCE_THRESHOLD:
        return "weak"
    return "good"


def build_disk_map(image: SCPImage, decoder: Decoder, layout: LayoutDescriptor | None = None, operation=None) -> DiskMap:
    """Decode an image and produce a :class:`DiskMap`.

    The mapper walks every track/head pair present in the image, decodes the
    first revolution, reconstructs sectors, and classifies each sector using
    CRC status and decoder confidence:

    * ``good``: ``crc_ok`` and confidence >= ``0.7``
    * ``weak``: ``crc_ok`` but confidence < ``0.7``
    * ``bad``: missing data or CRC failure

    With a selected layout, missing expected sector IDs are represented as
    ``"bad"`` entries. Without one, each row contains only structurally
    decoded sectors, preserving zoned and otherwise unusual track geometry.
    """

    # Without a selected layout, render only sectors supported by decoded
    # structure. Guessing an expected count would turn unknown sectors into
    # false errors on unusual or renamed media.
    expected_sectors = layout.sectors_per_track if layout else 0
    track_states: List[List[str]] = []
    track_ids: List[Tuple[int, int]] = []
    track_confidence: List[float] = []
    sector_details: List[List[SectorMapEntry]] = []
    max_sectors = 0

    ordered_tracks = sorted(image.tracks, key=lambda t: (t.track, t.side))
    track_total = len(ordered_tracks)
    for track_index, track_flux in enumerate(ordered_tracks, start=1):
        if operation is not None:
            operation.checkpoint("track", track_index, track_total)
        if layout and (track_flux.track >= layout.tracks or track_flux.side >= layout.sides):
            continue
        expected_this = expected_sectors
        if layout and hasattr(layout, "expected_sectors_for_track"):
            try:
                expected_this = layout.expected_sectors_for_track(track_flux.track, track_flux.side)
            except Exception:
                expected_this = expected_sectors
        sectors: List[str] = []
        details: List[SectorMapEntry] = []
        confidence = 0.0
        if not track_flux.revolutions:
            sector_base = _sector_id_base(layout, [])
            details = [
                _missing_sector_entry(sector_id, layout.sector_size if layout else 0)
                for sector_id in range(sector_base, sector_base + expected_this)
            ]
            sectors = [entry.state for entry in details]
        else:
            try:
                if hasattr(decoder, "set_track"):
                    try:
                        decoder.set_track(track_flux.track)
                    except Exception:
                        pass
                if layout and layout.layout_id.startswith("amiga_"):
                    from ..sector.reconstruct_amiga import (
                        reconstruct_amiga_greaseweazle,
                        reconstruct_amiga_with_pll,
                    )

                    if operation is not None:
                        operation.checkpoint("candidate decoder", track_index, track_total)
                    candidate = reconstruct_amiga_greaseweazle(
                        track_flux.revolutions,
                        track_flux.track,
                        track_flux.side,
                        timebase_ns=image.timebase_ns,
                    )
                    if candidate is None:
                        if operation is not None:
                            operation.checkpoint("candidate decoder fallback", track_index, track_total)
                        candidate = reconstruct_amiga_with_pll(
                            track_flux.revolutions,
                            track_flux.track,
                            track_flux.side,
                            timebase_ns=image.timebase_ns,
                        )
                    track_data = candidate
                else:
                    track_data = build_track_sectors_from_revolutions(
                        track_flux.revolutions,
                        decoder,
                        cylinder=track_flux.track,
                        head=track_flux.side,
                        expected_sectors=expected_this or None,
                        encoding=layout.encoding if layout else getattr(decoder, "encoding", None),
                        timebase_ns=image.timebase_ns,
                        operation=operation,
                    )
                confidence = (
                    sum(sec.confidence for sec in track_data.sectors) / len(track_data.sectors)
                    if track_data.sectors
                    else 0.0
                )
                decoded = sorted(track_data.sectors, key=lambda s: s.sector_id)
                details = [_sector_entry(sec) for sec in decoded]
                if expected_this and len(details) < expected_this:
                    present_ids = {entry.sector_id for entry in details}
                    sector_size = layout.sector_size if layout else (decoded[0].size if decoded else 0)
                    sector_base = _sector_id_base(layout, decoded)
                    for sector_id in range(sector_base, sector_base + expected_this):
                        if sector_id not in present_ids:
                            details.append(_missing_sector_entry(sector_id, sector_size))
                    details.sort(key=lambda entry: entry.sector_id)
                sectors = [entry.state for entry in details]
            except (FluxDecodeError, Exception):
                if operation is not None:
                    operation.checkpoint("cancelled")
                # Future refinement: capture decoder metrics so we can visualise
                # why a track failed, or try secondary revolutions for multi-pass
                # recovery.
                sector_base = _sector_id_base(layout, [])
                details = [
                    _missing_sector_entry(sector_id, layout.sector_size if layout else 0)
                    for sector_id in range(sector_base, sector_base + expected_this)
                ]
                sectors = [entry.state for entry in details]
                confidence = 0.0
        max_sectors = max(max_sectors, len(sectors))
        track_states.append(sectors)
        track_ids.append((track_flux.track, track_flux.side))
        track_confidence.append(confidence)
        sector_details.append(details)

    return DiskMap(
        tracks=track_states,
        total_tracks=len(track_states),
        max_sectors_per_track=max_sectors,
        track_ids=track_ids,
        track_confidence=track_confidence,
        sector_details=sector_details,
    )


def build_disk_map_from_tracksectors(tracks: list[TrackSectors]) -> DiskMap:
    """Build a DiskMap from already reconstructed TrackSectors (flat images)."""

    if not tracks:
        return DiskMap([], 0, 0)

    max_sectors = max(len(ts.sectors) for ts in tracks)
    track_states: list[list[str]] = []
    track_ids: list[tuple[int, int]] = []
    track_confidence: list[float] = []
    sector_details: list[list[SectorMapEntry]] = []

    for ts in sorted(tracks, key=lambda t: (t.track, t.head)):
        details = [_sector_entry(sec) for sec in sorted(ts.sectors, key=lambda s: s.sector_id)]
        sectors = [entry.state for entry in details]
        track_states.append(sectors)
        track_ids.append((ts.track, ts.head))
        confidence = (
            sum(sec.confidence for sec in ts.sectors) / len(ts.sectors) if ts.sectors else 0.0
        )
        track_confidence.append(confidence)
        sector_details.append(details)

    return DiskMap(
        tracks=track_states,
        total_tracks=len(track_states),
        max_sectors_per_track=max_sectors,
        render_style="radial",
        track_ids=track_ids,
        track_confidence=track_confidence,
        sector_details=sector_details,
    )


def build_cbm_bam_block_map(blocks: list[tuple[int, int, int, str]]) -> DiskMap:
    """Build a square-per-block BAM display from ``(track, sector, state)`` rows."""

    if not blocks:
        return DiskMap([], 0, 0, render_style="grid")

    by_track: dict[tuple[int, int], list[tuple[int, str]]] = {}
    for track, head, sector, state in blocks:
        by_track.setdefault((track, head), []).append((sector, state))

    track_states: list[list[str]] = []
    track_ids: list[tuple[int, int]] = []
    sector_details: list[list[SectorMapEntry]] = []
    for track, head in sorted(by_track, key=lambda item: (item[1], item[0])):
        entries = sorted(by_track[(track, head)])
        states = [state for _sector, state in entries]
        details = [
            SectorMapEntry(
                sector_id=sector,
                state=state,
                size=256,
                crc_ok=True,
                confidence=1.0,
                has_data=state != "bam_free",
            )
            for sector, state in entries
        ]
        track_states.append(states)
        track_ids.append((track, head))
        sector_details.append(details)

    return DiskMap(
        tracks=track_states,
        total_tracks=len(track_states),
        max_sectors_per_track=max(len(row) for row in track_states),
        render_style="grid",
        track_ids=track_ids,
        track_confidence=[1.0] * len(track_states),
        sector_details=sector_details,
        address_style="cbm_logical",
    )


def _c64_cpm_logical_block(track: int, sector_id: int) -> int | None:
    if track < 2 or track == 17 or track > 34 or sector_id >= 17:
        return None
    logical_track = track - 2 if track < 17 else 15 + (track - 18)
    logical_sector = logical_track * 17 + sector_id
    return logical_sector // 4


def apply_c64_cpm_2_2_logical_overlay(
    disk_map: DiskMap,
    allocated_blocks: set[int] | None = None,
) -> DiskMap:
    """Mark sectors outside the C64 CP/M 2.2 logical disk as unused.

    C64 CP/M 2.2 is stored on normal 1541 GCR media, but its CP/M DPB exposes
    17 logical 256-byte sectors per track. Wider 1541 zone sectors are unused
    by CP/M, and physical track 18 remains the CBM DOS reserve/directory track.
    The overlay preserves the physical ring geometry while preventing these
    intentionally unused areas from looking like filesystem damage.
    """

    for row_index, states in enumerate(disk_map.tracks):
        track = disk_map.track_ids[row_index][0] if row_index < len(disk_map.track_ids) else row_index
        details = disk_map.sector_details[row_index] if row_index < len(disk_map.sector_details) else []
        for sector_index, state in enumerate(list(states)):
            detail = details[sector_index] if sector_index < len(details) else None
            sector_id = detail.sector_id if detail is not None else sector_index
            logical_block = _c64_cpm_logical_block(track, sector_id)
            if logical_block is None or (allocated_blocks is not None and logical_block not in allocated_blocks):
                states[sector_index] = "unused"
                if detail is not None:
                    detail.state = "unused"
    return disk_map


def render_ascii(disk_map: DiskMap) -> str:
    """Render a disk map as ASCII art suitable for terminal inspection.

    Tracks are grouped by head (side) so all H0 rows appear first, followed by
    H1, which better matches common mental models of two-sided media. A short
    legend is prepended to clarify glyph meanings.
    """

    lines: List[str] = [
        "Legend: ",
        f"  {STATE_TO_GLYPH['good']} good  {STATE_TO_GLYPH['weak']} weak  {STATE_TO_GLYPH['bad']} bad",
    ]

    # Reorder tracks by head then track number when IDs are present; otherwise
    # retain original order.
    ordering = list(range(len(disk_map.tracks)))
    if disk_map.track_ids:
        ordering = sorted(range(len(disk_map.tracks)), key=lambda i: (disk_map.track_ids[i][1], disk_map.track_ids[i][0]))

    for idx in ordering:
        sectors = disk_map.tracks[idx]
        track_label = f"Track {idx:02d}"
        if disk_map.track_ids:
            track, head = disk_map.track_ids[idx]
            suffix = f"H{head}"
            track_label = f"Track {track:02d}{suffix}"
        glyphs = "".join(STATE_TO_GLYPH.get(state, "?") for state in sectors)
        padded = glyphs.ljust(disk_map.max_sectors_per_track)
        lines.append(f"{track_label}: [{padded}]")
    return "\n".join(lines)


def _polar_to_cartesian(radius: float, angle: float, cx: float, cy: float) -> tuple[float, float]:
    return cx + radius * cos(angle), cy + radius * sin(angle)


def render_svg(disk_map: DiskMap) -> str:
    """Render a disk map as an inline SVG string with concentric rings.

    Tracks are drawn as rings expanding outward from the centre. Each ring is
    sliced into equal angular segments based on ``max_sectors_per_track`` and
    filled according to the sector state. A minimal legend is placed beneath
    the rings. The output is self-contained and avoids external assets so it
    can be embedded in reports or static HTML without additional tooling.
    """

    if disk_map.total_tracks == 0 or disk_map.max_sectors_per_track == 0:
        return "<svg xmlns='http://www.w3.org/2000/svg'></svg>"

    ring_width = 14
    gap = 2
    radius = 20
    total_radius = radius + (ring_width + gap) * disk_map.total_tracks
    size = (total_radius + ring_width + gap) * 2
    cx = cy = size / 2
    segments: List[str] = []

    for track_idx, sectors in enumerate(disk_map.tracks):
        inner_r = radius + track_idx * (ring_width + gap)
        outer_r = inner_r + ring_width
        sector_count = max(len(sectors), 1)
        for sector_idx, state in enumerate(sectors):
            start_angle = 2 * pi * (sector_idx / sector_count)
            end_angle = 2 * pi * ((sector_idx + 1) / sector_count)
            large_arc = 1 if end_angle - start_angle > pi else 0

            x1, y1 = _polar_to_cartesian(outer_r, start_angle, cx, cy)
            x2, y2 = _polar_to_cartesian(outer_r, end_angle, cx, cy)
            x3, y3 = _polar_to_cartesian(inner_r, end_angle, cx, cy)
            x4, y4 = _polar_to_cartesian(inner_r, start_angle, cx, cy)

            color = STATE_TO_COLOR.get(state, "#7f8c8d")
            segments.append(
                " ".join(
                    [
                        f"<path d='M {x1:.2f},{y1:.2f} ",
                        f"A {outer_r:.2f},{outer_r:.2f} 0 {large_arc} 1 {x2:.2f},{y2:.2f} ",
                        f"L {x3:.2f},{y3:.2f} ",
                        f"A {inner_r:.2f},{inner_r:.2f} 0 {large_arc} 0 {x4:.2f},{y4:.2f} Z' ",
                        f"fill='{color}' stroke='#2c3e50' stroke-width='0.6' />",
                    ]
                )
            )

    legend_items = []
    legend_y = size - 20
    legend_x = 20
    for label, color in STATE_TO_COLOR.items():
        legend_items.append(
            f"<rect x='{legend_x}' y='{legend_y}' width='12' height='12' fill='{color}' stroke='#2c3e50' stroke-width='0.5' />"
        )
        legend_items.append(
            f"<text x='{legend_x + 18}' y='{legend_y + 10}' font-size='12' fill='#2c3e50'>{label.title()}</text>"
        )
        legend_x += 90

    return "".join(
        [
            f"<svg xmlns='http://www.w3.org/2000/svg' width='{size:.0f}' height='{size:.0f}' viewBox='0 0 {size:.0f} {size:.0f}'>",
            *segments,
            *legend_items,
            "</svg>",
        ]
    )


__all__ = [
    "DiskMap",
    "SectorMapEntry",
    "build_disk_map",
    "build_cbm_bam_block_map",
    "build_disk_map_from_tracksectors",
    "apply_c64_cpm_2_2_logical_overlay",
    "render_ascii",
    "render_svg",
]
