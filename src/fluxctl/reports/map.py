"""Disk map generation (JSON, ASCII, SVG)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import List

from ..models import LayoutDescriptor
from ..sector.models import TrackSectors

LEGEND = {"good": "G", "weak": "W", "bad": "B", "missing": "M", "unknown": "?"}


def build_map_json(layout: LayoutDescriptor | None, track_sector_data: List[TrackSectors]) -> dict:
    return {
        "schema_version": "map.v1",
        "layout": {
            "layout_id": layout.layout_id if layout else None,
            "tracks": layout.tracks if layout else None,
            "sides": layout.sides if layout else None,
            "sectors_per_track": layout.sectors_per_track if layout else None,
        },
        "legend": {k: v for k, v in LEGEND.items()},
        "tracks": [
            {
                "track": ts.track,
                "side": ts.side,
                "sectors": [
                    {
                        "sector_id": s.sector_id,
                        "state": s.state,
                        "confidence": s.confidence,
                        "crc_ok": s.crc_ok,
                    }
                    for s in ts.sectors
                ],
            }
            for ts in track_sector_data
        ],
    }


def render_ascii(map_json: dict) -> str:
    lines = []
    for track in sorted(map_json["tracks"], key=lambda t: (t["track"], t["side"])):
        states = "".join(LEGEND.get(sec["state"], "?") for sec in sorted(track["sectors"], key=lambda s: s["sector_id"]))
        lines.append(f"T{track['track']:02d}S{track['side']}: {states}")
    return "\n".join(lines)


def render_svg(map_json: dict, box_size: int = 12) -> str:
    rows = []
    y = 0
    for track in sorted(map_json["tracks"], key=lambda t: (t["track"], t["side"])):
        x = 0
        for sec in sorted(track["sectors"], key=lambda s: s["sector_id"]):
            color = {
                "good": "#2ecc71",
                "weak": "#f1c40f",
                "bad": "#e74c3c",
                "missing": "#95a5a6",
            }.get(sec["state"], "#7f8c8d")
            rows.append(
                f'<rect x="{x}" y="{y}" width="{box_size}" height="{box_size}" fill="{color}" stroke="#2c3e50" stroke-width="0.5" />'
            )
            x += box_size + 2
        y += box_size + 2
    width = max((box_size + 2) * len(track.get("sectors", [])) for track in map_json["tracks"]) if map_json["tracks"] else 0
    height = y
    return "".join(
        [
            f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}' viewBox='0 0 {width} {height}'>",
            *rows,
            "</svg>",
        ]
    )


def write_map_outputs(map_json: dict, ascii_out: bool, json_path: Path | None, svg_path: Path | None) -> str:
    ascii_text = render_ascii(map_json)
    if ascii_out:
        print(ascii_text)
    if json_path:
        json_path.write_text(json.dumps(map_json, indent=2), encoding="utf-8")
    if svg_path:
        svg_path.write_text(render_svg(map_json), encoding="utf-8")
    return ascii_text
