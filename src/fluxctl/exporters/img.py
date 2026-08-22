"""Raw IMG exporter."""
from __future__ import annotations

import json
from pathlib import Path
from typing import List

from ..models import LayoutDescriptor
from ..output import atomic_write_bytes, atomic_write_text, validate_output_path
from ..sector.models import Sector, TrackSectors
from ..scp import sha256_file


def export_img(
    track_data: List[TrackSectors],
    layout: LayoutDescriptor,
    out_path: Path,
    provenance: dict,
    *,
    overwrite: bool = False,
) -> None:
    sectors_flat: List[Sector] = sorted(
        [s for ts in track_data for s in ts.sectors], key=lambda s: (s.track, s.side, s.sector_id)
    )
    provenance_path = out_path.with_suffix(out_path.suffix + ".provenance.json")
    validate_output_path(out_path, overwrite=overwrite)
    validate_output_path(provenance_path, overwrite=overwrite)
    payload = b"".join(
        sector.data if sector.data else bytes([0x00]) * layout.sector_size
        for sector in sectors_flat
    )
    atomic_write_bytes(out_path, payload, overwrite=overwrite)
    provenance.update(
        {
            "output_sha256": sha256_file(out_path),
            "layout_id": layout.layout_id,
            "sectors_written": len(sectors_flat),
        }
    )
    atomic_write_text(provenance_path, json.dumps(provenance, indent=2), overwrite=overwrite)
