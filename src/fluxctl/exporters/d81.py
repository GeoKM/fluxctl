"""D81 exporter for Commodore 1581 flat sector images."""
from __future__ import annotations

from typing import Any, Dict

from ..exceptions import ExportError
from ..filesystems import RawSectorImage, TrackSectorImage
from ..sector.models import Sector, TrackSectors
from . import Exporter

D81_TRACKS = 80
D81_HEADS = 2
D81_SECTORS_PER_TRACK = 10
D81_SECTOR_SIZE = 512
D81_LOGICAL_SECTORS_PER_TRACK = 40
D81_LOGICAL_SECTOR_SIZE = 256
D81_SIZE = D81_TRACKS * D81_LOGICAL_SECTORS_PER_TRACK * D81_LOGICAL_SECTOR_SIZE
DEFAULT_PHYSICAL_HEAD_ORDER = (1, 0)


def physical_1581_to_d81_bytes(image) -> bytes:
    """Convert 1581 physical 512-byte sectors to D81 logical 256-byte sectors."""

    if isinstance(image, RawSectorImage) and image.bytes_per_sector == D81_LOGICAL_SECTOR_SIZE:
        return bytes(image.data[:D81_SIZE]).ljust(D81_SIZE, b"\x00")

    order = _detect_physical_head_order(image)
    payload = bytearray()
    for track in range(1, D81_TRACKS + 1):
        for logical_sector in range(D81_LOGICAL_SECTORS_PER_TRACK):
            physical_head = order[logical_sector // 20]
            physical_sector = (logical_sector % 20) // 2 + 1
            half_index = logical_sector % 2
            sector = _read_physical_sector(image, track - 1, physical_head, physical_sector)
            start = half_index * D81_LOGICAL_SECTOR_SIZE
            payload.extend(sector[start : start + D81_LOGICAL_SECTOR_SIZE])
    return bytes(payload)


def d81_bytes_to_physical_tracks(data: bytes) -> list[TrackSectors]:
    """Convert D81 logical 256-byte sectors to 1581 physical 512-byte tracks."""

    data = data[:D81_SIZE].ljust(D81_SIZE, b"\x00")
    tracks: list[TrackSectors] = []
    for track in range(D81_TRACKS):
        for head in range(D81_HEADS):
            sectors: list[Sector] = []
            for physical_sector in range(1, D81_SECTORS_PER_TRACK + 1):
                group = DEFAULT_PHYSICAL_HEAD_ORDER.index(head)
                first_logical = group * 20 + (physical_sector - 1) * 2
                first = _read_logical_d81_sector(data, track, first_logical)
                second = _read_logical_d81_sector(data, track, first_logical + 1)
                sectors.append(
                    Sector(
                        cylinder=track,
                        head=head,
                        sector_id=physical_sector,
                        size_code=2,
                        data=first + second,
                        crc_ok=True,
                        confidence=1.0,
                    )
                )
            tracks.append(TrackSectors(track=track, head=head, sectors=sectors))
    return tracks


def _read_logical_d81_sector(data: bytes, track: int, sector: int) -> bytes:
    offset = (track * D81_LOGICAL_SECTORS_PER_TRACK + sector) * D81_LOGICAL_SECTOR_SIZE
    return data[offset : offset + D81_LOGICAL_SECTOR_SIZE]


def _detect_physical_head_order(image) -> tuple[int, int]:
    try:
        from ..filesystems.cbm_dos_1581 import CBMDOS1581

        filesystem = CBMDOS1581()
        if filesystem.probe(image):
            return tuple(filesystem._logical_head_order)  # type: ignore[return-value]
    except Exception:
        pass
    return DEFAULT_PHYSICAL_HEAD_ORDER


def _read_physical_sector(image, track: int, head: int, sector_id: int) -> bytes:
    if isinstance(image, TrackSectorImage):
        data = image._sector_lookup.get((track, head, sector_id))
        if data is None:
            raise ExportError(f"1581 physical sector {(track, head, sector_id)} not available")
        return data[:D81_SECTOR_SIZE].ljust(D81_SECTOR_SIZE, b"\x00")
    if isinstance(image, RawSectorImage) and image.bytes_per_sector == D81_SECTOR_SIZE:
        lba = (track * D81_HEADS + head) * D81_SECTORS_PER_TRACK + (sector_id - 1)
        return image.read_sector(lba, 1)
    raise ExportError("D81 exporter requires 1581 physical sectors or D81 logical sectors")


class D81Exporter(Exporter):
    """Write Commodore 1581 images as flat D81 sector dumps."""

    extensions = (".d81",)

    def __init__(self) -> None:
        self._metadata: dict[str, Any] = {}

    def supports(self, image) -> bool:
        if isinstance(image, RawSectorImage):
            return image.bytes_per_sector in {D81_LOGICAL_SECTOR_SIZE, D81_SECTOR_SIZE} and len(image.data) <= D81_SIZE
        if isinstance(image, TrackSectorImage):
            return image.bytes_per_sector == D81_SECTOR_SIZE
        return False

    def export(self, image) -> bytes:
        if not self.supports(image):
            raise ExportError("D81 exporter requires Commodore 1581 512-byte sectors")

        payload = physical_1581_to_d81_bytes(image)
        padded_missing = len(payload) < D81_SIZE
        self._metadata = {"padded_missing": padded_missing, "d81_size": D81_SIZE}
        return payload

    def metadata(self) -> Dict[str, Any]:
        return {"name": "D81 exporter", "version": "0.1", **self._metadata}


__all__ = ["D81Exporter", "D81_SIZE", "d81_bytes_to_physical_tracks", "physical_1581_to_d81_bytes"]
