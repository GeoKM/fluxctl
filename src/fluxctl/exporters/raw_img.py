"""Raw IMG exporter.

This exporter flattens reconstructed sectors into a contiguous raw disk image
suitable for use with emulators and forensic tools. Missing sectors are padded
with zero bytes to preserve logical offsets.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Sequence

from ..exceptions import ExportError
from ..filesystems import RawSectorImage, TrackSectorImage
from ..sector.models import Sector, TrackSectors


class RawIMGExporter:
    """Emit a flat sector-by-sector IMG file."""

    extensions = ("img", "ima", "raw")

    def __init__(self) -> None:
        self._padded_missing = False

    def supports(self, image) -> bool:
        if isinstance(image, RawSectorImage):
            return True
        if isinstance(image, TrackSectorImage):
            return bool(image.tracks)
        return hasattr(image, "tracks")

    def export(self, image) -> bytes:
        if isinstance(image, RawSectorImage):
            self._padded_missing = False
            return image.data

        track_list = self._extract_tracks(image)
        if not track_list:
            raise ExportError("No track data available for export")

        sector_size = self._sector_size(track_list)
        payload = bytearray()
        self._padded_missing = False

        for ts in sorted(track_list, key=lambda t: (t.track, t.head)):
            ordered_ids = self._ordered_sector_ids(ts.sectors)
            sector_map = {sector.sector_id: sector for sector in ts.sectors}
            for sector_id in ordered_ids:
                sector = sector_map.get(sector_id)
                if sector and sector.data:
                    data = sector.data
                    if len(data) < sector_size:
                        data = data.ljust(sector_size, b"\x00")
                else:
                    data = bytes([0x00]) * sector_size
                    self._padded_missing = True
                payload.extend(data)
        return bytes(payload)

    def metadata(self) -> Dict[str, Any]:
        return {
            "format": "raw", 
            "name": "Raw sector image", 
            "version": "0.1", 
            "extensions": list(self.extensions),
            "padded_missing": self._padded_missing,
        }

    def _extract_tracks(self, image) -> Sequence[TrackSectors]:
        if isinstance(image, TrackSectorImage):
            return image.tracks
        tracks = getattr(image, "tracks", None)
        if tracks is None:
            raise ExportError("Exporter expected track-based image")
        return tracks

    def _sector_size(self, tracks: Sequence[TrackSectors]) -> int:
        """
        Determine the common sector size for a group of tracks. If multiple
        sizes are present, raise an error to avoid corrupt RAW images.
        """
        sizes = {sector.size for ts in tracks for sector in ts.sectors if sector.data}
        if not sizes:
            raise ExportError("Unable to determine sector size from image")
        if len(sizes) > 1:
            raise ExportError(f"Mixed sector sizes {sorted(sizes)} are not supported for RAW export")
        return next(iter(sizes))

    def _ordered_sector_ids(self, sectors: Iterable[Sector]) -> List[int]:
        ids = sorted({sector.sector_id for sector in sectors})
        return list(range(1, ids[-1] + 1)) if ids else []


__all__ = ["RawIMGExporter"]
