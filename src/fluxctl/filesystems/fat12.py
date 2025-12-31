"""Simplified FAT12 reader suitable for placeholder inspection."""
from __future__ import annotations

import os
from pathlib import Path
from typing import List

from ..models import TrackSectors


class FAT12Extractor:
    def __init__(self, layout_sector_size: int):
        self.sector_size = layout_sector_size

    def extract(self, track_data: List[TrackSectors], out_dir: Path) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        # This placeholder writes each sector to an individual file to keep behaviour simple
        for ts in sorted(track_data, key=lambda t: (t.track, t.side)):
            for sector in sorted(ts.sectors, key=lambda s: s.sector_id):
                name = f"track{ts.track:02d}_side{ts.side}_sector{sector.sector_id:02d}.bin"
                (out_dir / name).write_bytes(sector.data)


def extract_fat12(track_data: List[TrackSectors], sector_size: int, out_dir: Path) -> None:
    extractor = FAT12Extractor(sector_size)
    extractor.extract(track_data, out_dir)
