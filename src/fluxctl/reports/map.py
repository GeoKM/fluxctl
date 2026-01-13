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
from ..models import SCPImage
from ..sector.models import TrackSectors
from ..sector.models import Sector
from ..sector.reconstruct import build_track_sectors

WEAK_CONFIDENCE_THRESHOLD = 0.8

STATE_TO_GLYPH = {"good": "■", "weak": "□", "bad": "×"}
STATE_TO_COLOR = {"good": "#2ecc71", "weak": "#f1c40f", "bad": "#e74c3c"}


@dataclass
class DiskMap:
    """Simple classification grid for sector health across a disk.

    Attributes
    ----------
    tracks:
        Outer list is ordered by the track/head pairs encountered in the image.
        Each inner list contains the per-sector state labels (``good``/``weak``
        /``bad``) for that track. Tracks with fewer sectors are padded with
        ``"bad"`` to match ``max_sectors_per_track``.
    total_tracks:
        Total number of track/head entries represented.
    max_sectors_per_track:
        The maximum sector count observed (or inferred) across all tracks.
    track_ids:
        Optional list of ``(track, head)`` tuples mirroring ``tracks`` order.
    track_confidence:
        Optional list of average confidence values per track to surface in
        future renderings or legends.
    """

    tracks: List[List[str]]
    total_tracks: int
    max_sectors_per_track: int
    track_ids: List[Tuple[int, int]] = field(default_factory=list)
    track_confidence: List[float] = field(default_factory=list)


def _estimate_sectors_per_track(image: SCPImage) -> int:
    """Estimate expected sectors per track using filename hints and counts.

    The heuristic mirrors the QC module: if the filename contains a capacity
    hint like ``180K`` or ``1.44M`` we convert that to an approximate sectors
    per track based on the number of decoded tracks. When no hint is available
    the mapper falls back to a conservative value of 9.
    """

    if not image.tracks:
        return 0
    track_total = len(image.tracks)
    # Lightweight capacity hint: look for ``180K`` or ``1.44M`` style markers.
    for token in image.path.name.replace("-", " ").split():
        if token.endswith("K") or token.endswith("M"):
            numeric = token[:-1]
            unit = token[-1].lower()
            try:
                value = float(numeric)
            except ValueError:
                continue
            capacity_bytes = value * (1024 ** (1 if unit == "k" else 2))
            estimated = int(round(capacity_bytes / (track_total * 512)))
            if estimated > 0:
                return estimated
    return 9


def _classify_sector(sector: Sector) -> str:
    """Classify a sector into good/weak/bad buckets."""

    if not sector.data or not sector.crc_ok:
        return "bad"
    if sector.confidence < WEAK_CONFIDENCE_THRESHOLD:
        return "weak"
    return "good"


def build_disk_map(image: SCPImage, decoder: Decoder) -> DiskMap:
    """Decode an image and produce a :class:`DiskMap`.

    The mapper walks every track/head pair present in the image, decodes the
    first revolution, reconstructs sectors, and classifies each sector using
    CRC status and decoder confidence:

    * ``good``: ``crc_ok`` and confidence >= ``0.8``
    * ``weak``: ``crc_ok`` but confidence < ``0.8``
    * ``bad``: missing data or CRC failure

    Tracks with fewer sectors than the maximum observed are padded with
    ``"bad"`` entries so that renderers can draw consistent rows/rings.
    """

    expected_sectors = _estimate_sectors_per_track(image)
    track_states: List[List[str]] = []
    track_ids: List[Tuple[int, int]] = []
    track_confidence: List[float] = []
    max_sectors = 0

    for track_flux in sorted(image.tracks, key=lambda t: (t.track, t.side)):
        sectors: List[str] = []
        confidence = 0.0
        if not track_flux.revolutions:
            sectors = ["bad"] * expected_sectors
        else:
            try:
                track_data = build_track_sectors(
                    track_flux.revolutions[0],
                    decoder,
                    cylinder=track_flux.track,
                    head=track_flux.side,
                    expected_sectors=expected_sectors or None,
                )
                confidence = (
                    sum(sec.confidence for sec in track_data.sectors) / len(track_data.sectors)
                    if track_data.sectors
                    else 0.0
                )
                sectors = [_classify_sector(sec) for sec in sorted(track_data.sectors, key=lambda s: s.sector_id)]
                if expected_sectors and len(sectors) < expected_sectors:
                    sectors.extend(["bad"] * (expected_sectors - len(sectors)))
            except (FluxDecodeError, Exception):
                # Future refinement: capture decoder metrics so we can visualise
                # why a track failed, or try secondary revolutions for multi-pass
                # recovery.
                sectors = ["bad"] * expected_sectors
                confidence = 0.0
        max_sectors = max(max_sectors, len(sectors))
        track_states.append(sectors)
        track_ids.append((track_flux.track, track_flux.side))
        track_confidence.append(confidence)

    for idx, sectors in enumerate(track_states):
        if len(sectors) < max_sectors:
            sectors.extend(["bad"] * (max_sectors - len(sectors)))
            track_states[idx] = sectors

    return DiskMap(
        tracks=track_states,
        total_tracks=len(track_states),
        max_sectors_per_track=max_sectors,
        track_ids=track_ids,
        track_confidence=track_confidence,
    )


def build_disk_map_from_tracksectors(tracks: list[TrackSectors]) -> DiskMap:
    """Build a DiskMap from already reconstructed TrackSectors (flat images)."""

    if not tracks:
        return DiskMap([], 0, 0)

    max_sectors = max(len(ts.sectors) for ts in tracks)
    track_states: list[list[str]] = []
    track_ids: list[tuple[int, int]] = []
    track_confidence: list[float] = []

    for ts in sorted(tracks, key=lambda t: (t.track, t.head)):
        sectors = [_classify_sector(sec) for sec in sorted(ts.sectors, key=lambda s: s.sector_id)]
        if len(sectors) < max_sectors:
            sectors.extend(["bad"] * (max_sectors - len(sectors)))
        track_states.append(sectors)
        track_ids.append((ts.track, ts.head))
        confidence = (
            sum(sec.confidence for sec in ts.sectors) / len(ts.sectors) if ts.sectors else 0.0
        )
        track_confidence.append(confidence)

    return DiskMap(
        tracks=track_states,
        total_tracks=len(track_states),
        max_sectors_per_track=max_sectors,
        track_ids=track_ids,
        track_confidence=track_confidence,
    )


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
        padded = glyphs.ljust(disk_map.max_sectors_per_track, STATE_TO_GLYPH["bad"])
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
        for sector_idx, state in enumerate(sectors):
            start_angle = 2 * pi * (sector_idx / disk_map.max_sectors_per_track)
            end_angle = 2 * pi * ((sector_idx + 1) / disk_map.max_sectors_per_track)
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
    "build_disk_map",
    "build_disk_map_from_tracksectors",
    "render_ascii",
    "render_svg",
]
