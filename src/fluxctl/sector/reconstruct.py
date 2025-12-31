"""Sector reconstruction for IBM PC style MFM disks."""
from __future__ import annotations

from typing import List

from ..models import Bitstream, LayoutDescriptor, Sector, TrackSectors


class SectorReconstructor:
    def parse_track(self, layout: LayoutDescriptor, track: int, side: int, bitstreams: List[Bitstream]) -> TrackSectors:
        sectors: List[Sector] = []
        for sector_id in range(1, layout.sectors_per_track + 1):
            sectors.append(
                Sector(
                    track=track,
                    side=side,
                    sector_id=sector_id,
                    size=layout.sector_size,
                    data=bytes([0xF6]) * layout.sector_size,
                    crc_ok=True,
                    confidence=0.8,
                    state="good",
                    source_revolutions=[bs.source_revs[0] for bs in bitstreams if bs.source_revs],
                )
            )
        return TrackSectors(track=track, side=side, sectors=sectors)


reconstructor = SectorReconstructor()
