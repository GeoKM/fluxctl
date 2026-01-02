"""D64 exporter for Commodore 1541-style images."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..exceptions import ExportError
from ..filesystems import RawSectorImage, TrackSectorImage
from ..sector.models import TrackSectors
from . import Exporter

# Standard 35-track geometry for D64 images
DEFAULT_SECTORS_PER_TRACK: List[int] = (
    [21] * 17
    + [19] * 7
    + [18] * 6
    + [17] * 5
)

SECTOR_SIZE = 256


def _sectors_for_track(track_index: int, layout_track_sectors: Optional[List[int]]) -> int:
    if layout_track_sectors:
        if 0 <= track_index < len(layout_track_sectors):
            return layout_track_sectors[track_index]
        return layout_track_sectors[-1]
    if track_index < len(DEFAULT_SECTORS_PER_TRACK):
        return DEFAULT_SECTORS_PER_TRACK[track_index]
    return DEFAULT_SECTORS_PER_TRACK[-1]


class D64Exporter(Exporter):
    """Write reconstructed sectors into a flat D64 image."""

    extensions = (".d64",)

    def supports(self, image) -> bool:
        if isinstance(image, RawSectorImage):
            return image.bytes_per_sector == SECTOR_SIZE
        if isinstance(image, TrackSectorImage):
            return image.bytes_per_sector == SECTOR_SIZE
        return False

    def _gather_tracks(self, image: TrackSectorImage) -> Dict[int, TrackSectors]:
        mapping: Dict[int, TrackSectors] = {}
        for ts in image.tracks:
            mapping[ts.track] = ts
        return mapping

    def export(self, image) -> bytes:
        if not self.supports(image):
            raise ExportError("D64 exporter requires 256-byte sectors")
        if isinstance(image, RawSectorImage):
            return bytes(image.data)

        layout = getattr(image, "layout", None)
        layout_tracks = getattr(layout, "tracks", None)
        layout_track_sectors = getattr(layout, "track_sectors", None)
        total_tracks = layout_tracks or len(DEFAULT_SECTORS_PER_TRACK)
        track_map = self._gather_tracks(image)

        payload = bytearray()
        padded_missing = False
        for track_index in range(total_tracks):
            sectors_this_track = _sectors_for_track(track_index, layout_track_sectors)
            ts = track_map.get(track_index)
            sector_lookup = {sector.sector_id: sector for sector in ts.sectors} if ts else {}
            for sector_id in range(sectors_this_track):
                sector = sector_lookup.get(sector_id)
                if sector is None:
                    padded_missing = True
                    payload.extend(b"\x00" * SECTOR_SIZE)
                else:
                    data = sector.data[:SECTOR_SIZE].ljust(SECTOR_SIZE, b"\x00")
                    payload.extend(data)

        self._metadata = {"padded_missing": padded_missing, "tracks": total_tracks}
        return bytes(payload)

    def metadata(self) -> Dict[str, Any]:
        return {"name": "D64 exporter", "version": "0.1", **getattr(self, "_metadata", {})}
