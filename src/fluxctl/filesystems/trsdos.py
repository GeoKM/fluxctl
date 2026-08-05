"""TRSDOS 1.3 directory reader for TRS-80 Model III/4 media."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from ..exceptions import FilesystemError
from . import FileEntry, Filesystem, SectorImage


TRSDOS_DIRECTORY_TRACK = 17
TRSDOS_SECTORS_PER_TRACK = 18
TRSDOS_SECTOR_SIZE = 256
TRSDOS_GRANULE_SECTORS = 3
TRSDOS_DIRECTORY_ENTRIES_PER_SECTOR = 5
TRSDOS_DIRECTORY_ENTRY_SIZE = 48
TRSDOS_ALLOWED_NAME = set(b"ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789$%'-_@~`!#()^")


@dataclass(slots=True)
class TRSDOSExtent:
    track: int
    start_granule: int
    granules: int


@dataclass(slots=True)
class TRSDOSDirectoryEntry:
    name: str
    native_name: str
    sectors: int
    eof_byte: int
    record_length: int
    attributes: int
    month: int
    year: int
    extents: list[TRSDOSExtent]


def _clean_field(raw: bytes) -> str:
    return bytes(byte & 0x7F for byte in raw).decode("ascii", errors="ignore").rstrip()


def _looks_like_name(raw: bytes) -> bool:
    cleaned = bytes(byte & 0x7F for byte in raw).rstrip(b" ")
    return bool(cleaned) and all(byte in TRSDOS_ALLOWED_NAME for byte in cleaned)


def _decode_extent(track: int, encoded: int) -> TRSDOSExtent | None:
    if track == 0xFF or encoded == 0xFF:
        return None
    if track >= 96:
        return None
    start_granule = (encoded >> 5) & 0x07
    granules = encoded & 0x1F
    if start_granule >= 6 or granules == 0:
        return None
    return TRSDOSExtent(track=track, start_granule=start_granule, granules=granules)


def _decode_entry(raw: bytes) -> TRSDOSDirectoryEntry | None:
    if len(raw) != TRSDOS_DIRECTORY_ENTRY_SIZE:
        return None
    attributes = raw[0]
    if attributes in {0x00, 0xFF} or not (attributes & 0x10):
        return None
    if not _looks_like_name(raw[5:13]) or not _looks_like_name(raw[13:16]):
        return None
    stem = _clean_field(raw[5:13])
    suffix = _clean_field(raw[13:16])
    if not stem:
        return None
    extents: list[TRSDOSExtent] = []
    for offset in range(0x16, 0x30, 2):
        extent = _decode_extent(raw[offset], raw[offset + 1])
        if extent is not None:
            extents.append(extent)
    if not extents:
        return None
    native_name = f"{stem}/{suffix}" if suffix else stem
    display_name = f"{stem}.{suffix}" if suffix else stem
    return TRSDOSDirectoryEntry(
        name=display_name,
        native_name=native_name,
        sectors=int.from_bytes(raw[0x14:0x16], "little"),
        eof_byte=raw[0x03],
        record_length=raw[0x04] or TRSDOS_SECTOR_SIZE,
        attributes=attributes,
        month=raw[0x01],
        year=raw[0x02],
        extents=extents,
    )


class TRSDOS13Filesystem(Filesystem):
    """Read TRSDOS 1.3 directory entries from Model III/4 disks."""

    def __init__(self) -> None:
        self._image: SectorImage | None = None
        self._sector_base = 1
        self._entries: list[TRSDOSDirectoryEntry] = []
        self._label = ""
        self._date = ""

    def probe(self, image: SectorImage) -> bool:
        if getattr(image, "bytes_per_sector", TRSDOS_SECTOR_SIZE) != TRSDOS_SECTOR_SIZE:
            return False
        for sector_base in (1, 0):
            try:
                gat = self._read_chs(image, TRSDOS_DIRECTORY_TRACK, sector_base, sector_base)
                hit = self._read_chs(image, TRSDOS_DIRECTORY_TRACK, sector_base, sector_base + 1)
                entries = self._read_directory_entries(image, sector_base)
            except Exception:
                continue
            if self._valid_gat(gat) and self._valid_hit(hit) and entries:
                self._image = image
                self._sector_base = sector_base
                self._entries = entries
                self._label = _clean_field(gat[0xD0:0xD8])
                self._date = _clean_field(gat[0xD8:0xE0])
                return True
        return False

    def _read_chs(self, image: SectorImage, track: int, sector_base: int, sector_id: int) -> bytes:
        lba = track * TRSDOS_SECTORS_PER_TRACK + (sector_id - sector_base)
        return image.read_sector(lba)

    def _valid_gat(self, data: bytes) -> bool:
        if len(data) != TRSDOS_SECTOR_SIZE:
            return False
        gat = data[:40]
        return any(byte for byte in gat) and all((byte & 0xC0) == 0 for byte in gat)

    def _valid_hit(self, data: bytes) -> bool:
        if len(data) != TRSDOS_SECTOR_SIZE:
            return False
        hit = data[:80]
        return sum(1 for byte in hit if byte not in {0x00, 0xFF}) >= 2

    def _read_directory_entries(self, image: SectorImage, sector_base: int) -> list[TRSDOSDirectoryEntry]:
        entries: list[TRSDOSDirectoryEntry] = []
        for sector_id in range(sector_base + 2, sector_base + TRSDOS_SECTORS_PER_TRACK):
            data = self._read_chs(image, TRSDOS_DIRECTORY_TRACK, sector_base, sector_id)
            for index in range(TRSDOS_DIRECTORY_ENTRIES_PER_SECTOR):
                start = index * TRSDOS_DIRECTORY_ENTRY_SIZE
                entry = _decode_entry(data[start : start + TRSDOS_DIRECTORY_ENTRY_SIZE])
                if entry is not None:
                    entries.append(entry)
        return entries

    def list_directory(self, path: str = "/") -> List[FileEntry]:
        if path not in {"", "/"}:
            raise FilesystemError("TRSDOS reader only supports the root directory")
        return [
            FileEntry(
                name=entry.name,
                is_dir=False,
                size=self._entry_size(entry),
                cluster_start=entry.extents[0].track * 6 + entry.extents[0].start_granule,
                attributes=entry.attributes,
            )
            for entry in sorted(self._entries, key=lambda item: item.name)
        ]

    def _entry_size(self, entry: TRSDOSDirectoryEntry) -> int:
        extent_sectors = sum(extent.granules for extent in entry.extents) * TRSDOS_GRANULE_SECTORS
        sectors = entry.sectors or extent_sectors
        if entry.sectors and entry.eof_byte:
            return max(0, (entry.sectors - 1) * TRSDOS_SECTOR_SIZE + entry.eof_byte)
        return sectors * TRSDOS_SECTOR_SIZE

    def extract_file(self, path: str) -> bytes:
        if self._image is None:
            raise FilesystemError("TRSDOS filesystem has not been probed")
        normalized = path.strip("/").upper().replace("/", ".")
        entry = next((item for item in self._entries if item.name.upper() == normalized), None)
        if entry is None:
            raise FilesystemError(f"TRSDOS file not found: {path}")
        data = bytearray()
        for extent in entry.extents:
            for granule_offset in range(extent.granules):
                first_sector = (extent.start_granule + granule_offset) * TRSDOS_GRANULE_SECTORS
                for sector_offset in range(TRSDOS_GRANULE_SECTORS):
                    sector_id = self._sector_base + first_sector + sector_offset
                    data.extend(self._read_chs(self._image, extent.track, self._sector_base, sector_id))
        return bytes(data[: self._entry_size(entry)])

    def metadata(self) -> Dict[str, Any]:
        return {
            "filesystem": "trsdos_1_3",
            "label": self._label,
            "date": self._date,
            "entries": len(self._entries),
        }


__all__ = ["TRSDOS13Filesystem"]
