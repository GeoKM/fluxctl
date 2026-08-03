"""IMD exporter supporting a subset of ImageDisk v1 format."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Sequence

from ..exceptions import ExportError
from ..filesystems import TrackSectorImage
from ..sector.models import Sector, TrackSectors


class IMDExporter:
    """Export reconstructed tracks as ImageDisk (``.imd``) images.

    The implementation targets standard soft-sectored MFM disks and encodes each
    track using the simplified IMD layout: a short header, sector numbering map,
    a sector-type map, and per-sector payloads. Missing or suspect sectors are
    emitted using compressed fill bytes (0xF6) so that consumers can distinguish
    reconstructed gaps from valid data.
    """

    extensions = ("imd",)

    def __init__(self) -> None:
        self._padded_missing = False

    def supports(self, image) -> bool:
        try:
            tracks = self._extract_tracks(image)
        except ExportError:
            return False
        sizes = {sector.size for ts in tracks for sector in ts.sectors}
        return bool(sizes) and all(size in {128, 256, 512, 1024, 2048, 4096} for size in sizes)

    def export(self, image) -> bytes:
        tracks = self._extract_tracks(image)
        if not tracks:
            raise ExportError("No track data available for IMD export")
        if not self.supports(image):
            raise ExportError("Image geometry not compatible with IMD")

        header = self._build_header()
        payload = bytearray(header)
        self._padded_missing = False

        for ts in sorted(tracks, key=lambda t: (t.track, t.head)):
            # Ensure consistent sector sizes on this track; IMD does not support mixed sizes.
            unique_sizes = {sector.size for sector in ts.sectors if sector.data}
            if len(unique_sizes) != 1:
                raise ExportError(
                    f"Mixed sector sizes {sorted(unique_sizes)} on track {ts.track} are not supported for IMD export"
                )
            sector_size = self._sector_size(ts.sectors)
            size_code = self._size_code(sector_size)
            sector_ids = self._sector_ids(ts.sectors)
            sector_types = []
            sector_payloads = []
            sector_map = {sector.sector_id: sector for sector in ts.sectors}

            for sector_id in sector_ids:
                sector = sector_map.get(sector_id)
                stype, encoded = self._encode_sector(sector, sector_size)
                if stype in (0x02, 0x04, 0x06, 0x08):
                    self._padded_missing = self._padded_missing or sector is None or not sector.data
                sector_types.append(stype)
                sector_payloads.append(encoded)

            payload.extend(bytes([self._mode_for_track(ts), ts.track, ts.head, len(sector_ids), size_code]))
            payload.extend(bytes(sector_ids))
            for stype, encoded in zip(sector_types, sector_payloads):
                payload.append(stype)
                if stype in (0x02, 0x04, 0x06, 0x08):
                    payload.extend(encoded[:1])
                elif stype == 0x00:
                    payload.extend(b"")
                else:
                    payload.extend(encoded)

        return bytes(payload)

    def metadata(self) -> Dict[str, Any]:
        return {
            "format": "imd", 
            "name": "ImageDisk", 
            "version": "0.1", 
            "extensions": list(self.extensions),
            "padded_missing": self._padded_missing,
        }

    def _extract_tracks(self, image) -> Sequence[TrackSectors]:
        if isinstance(image, TrackSectorImage):
            return image.tracks
        tracks = getattr(image, "tracks", None)
        if tracks is None:
            raise ExportError("IMD exporter requires track-based image input")
        return tracks

    def _build_header(self) -> bytes:
        now = datetime.now(timezone.utc)
        header_lines = [
            "IMD 1.18: fluxctl",
            f"UTC: {now.strftime('%Y-%m-%d %H:%M:%S')}",
            "Converted by fluxctl",
        ]
        return ("\r\n".join(header_lines) + "\r\n\x1a").encode("ascii", errors="replace")

    def _sector_ids(self, sectors: Iterable[Sector]) -> list[int]:
        ids = sorted({sector.sector_id for sector in sectors})
        if not ids:
            return []
        start = 0 if ids[0] == 0 else 1
        return list(range(start, ids[-1] + 1))

    def _sector_size(self, sectors: Iterable[Sector]) -> int:
        for sector in sectors:
            return sector.size
        raise ExportError("Unable to determine sector size for IMD export")

    def _size_code(self, sector_size: int) -> int:
        size_map = {128: 0, 256: 1, 512: 2, 1024: 3, 2048: 4, 4096: 5}
        try:
            return size_map[sector_size]
        except KeyError as exc:
            raise ExportError(f"Unsupported IMD sector size {sector_size}") from exc

    def _mode_for_track(self, track: TrackSectors) -> int:
        sector_count = len(track.sectors)
        return 0x04 if sector_count >= 18 else 0x03

    def _encode_sector(self, sector: Sector | None, sector_size: int) -> tuple[int, bytes]:
        if sector is None or not sector.data:
            return (0x06, bytes([0xF6]))
        padded = sector.data
        if len(padded) < sector_size:
            padded = padded.ljust(sector_size, b"\x00")
        if len(set(padded)) == 1:
            fill = padded[:1]
            if not sector.crc_ok:
                return (0x08 if sector.deleted else 0x06, fill)
            return (0x04 if sector.deleted else 0x02, fill)
        if not sector.crc_ok:
            return (0x07 if sector.deleted else 0x05, padded)
        if sector.deleted:
            return (0x03, padded)
        return (0x01, padded)


__all__ = ["IMDExporter"]
