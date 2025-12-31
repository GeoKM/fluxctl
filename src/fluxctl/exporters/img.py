"""Raw IMG exporter."""
from __future__ import annotations

import json
from pathlib import Path
from typing import List

from ..models import LayoutDescriptor, Sector, TrackSectors
from ..scp import sha256_file


def export_img(track_data: List[TrackSectors], layout: LayoutDescriptor, out_path: Path, provenance: dict) -> None:
    sectors_flat: List[Sector] = sorted(
        [s for ts in track_data for s in ts.sectors], key=lambda s: (s.track, s.side, s.sector_id)
    )
    with out_path.open("wb") as fh:
        for sector in sectors_flat:
            fh.write(sector.data if sector.data else bytes([0x00]) * layout.sector_size)
    provenance.update(
        {
            "output_sha256": sha256_file(out_path),
            "layout_id": layout.layout_id,
            "sectors_written": len(sectors_flat),
        }
    )
    out_path.with_suffix(out_path.suffix + ".provenance.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
