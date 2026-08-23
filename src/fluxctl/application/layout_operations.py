"""Layout metadata operations shared by Fluxctl frontends."""
from __future__ import annotations


def load_layout_options() -> list[dict[str, object]]:
    from ..layouts.loader import load_builtin_layouts

    layouts = load_builtin_layouts()
    return [
        {
            "layout_id": layout.layout_id,
            "name": layout.name,
            "encoding": layout.encoding,
            "tracks": layout.tracks,
            "sides": layout.sides,
            "sectors_per_track": layout.sectors_per_track,
            "sector_size": layout.sector_size,
        }
        for layout in sorted(layouts, key=lambda item: item.layout_id)
    ]
