"""D71 exporter for Commodore 1571 flat sector images."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..exceptions import ExportError
from ..filesystems import RawSectorImage, TrackSectorImage
from ..sector.models import Sector
from . import Exporter
from .d64 import DEFAULT_SECTORS_PER_TRACK, SECTOR_SIZE, _sectors_for_track

D71_SIDES = 2


class D71Exporter(Exporter):
    """Write reconstructed 1571 sectors into a flat D71 image."""

    extensions = (".d71",)

    def supports(self, image) -> bool:
        if isinstance(image, RawSectorImage):
            return image.bytes_per_sector == SECTOR_SIZE
        if isinstance(image, TrackSectorImage):
            return image.bytes_per_sector == SECTOR_SIZE
        return False

    def _sector_lookup(self, image: TrackSectorImage) -> Dict[tuple[int, int, int], Sector]:
        mapping: Dict[tuple[int, int, int], Sector] = {}
        for ts in image.tracks:
            for sector in ts.sectors:
                mapping[(ts.track, ts.head, sector.sector_id)] = sector
        return mapping

    def export(self, image) -> bytes:
        if not self.supports(image):
            raise ExportError("D71 exporter requires 256-byte sectors")
        if isinstance(image, RawSectorImage):
            return bytes(image.data)

        layout = getattr(image, "layout", None)
        layout_tracks = getattr(layout, "tracks", None)
        layout_track_sectors: Optional[List[int]] = getattr(layout, "track_sectors", None)
        total_tracks = layout_tracks or len(DEFAULT_SECTORS_PER_TRACK)
        total_sides = getattr(layout, "sides", D71_SIDES) or D71_SIDES
        if total_sides != D71_SIDES:
            raise ExportError("D71 exporter requires a two-sided Commodore 1571 layout")
        sector_base = int(getattr(layout, "id_rules", {}).get("sector_number_base", 0)) if layout else 0
        sector_lookup = self._sector_lookup(image)

        payload = bytearray()
        padded_missing = False
        # D71 stores all side 0 tracks first, then all side 1 tracks.
        for head in range(D71_SIDES):
            for track_index in range(total_tracks):
                sectors_this_track = _sectors_for_track(track_index, layout_track_sectors)
                for sector_offset in range(sectors_this_track):
                    sector = sector_lookup.get((track_index, head, sector_base + sector_offset))
                    if sector is None:
                        padded_missing = True
                        payload.extend(b"\x00" * SECTOR_SIZE)
                    else:
                        payload.extend(sector.data[:SECTOR_SIZE].ljust(SECTOR_SIZE, b"\x00"))

        self._metadata = {"padded_missing": padded_missing, "tracks": total_tracks, "sides": D71_SIDES}
        return bytes(payload)

    def metadata(self) -> Dict[str, Any]:
        return {"name": "D71 exporter", "version": "0.1", **getattr(self, "_metadata", {})}
