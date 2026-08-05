"""NEWDOS/80 directory reader for TRS-80 Model I/III-style disks."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from ..exceptions import FilesystemError
from . import FileEntry, Filesystem, SectorImage


NEWDOS80_SECTOR_SIZE = 256
NEWDOS80_SECTORS_PER_TRACK = 18
NEWDOS80_SECTORS_PER_GRANULE = 5
NEWDOS80_GRANULES_PER_LUMP = 2
NEWDOS80_SECTORS_PER_LUMP = NEWDOS80_SECTORS_PER_GRANULE * NEWDOS80_GRANULES_PER_LUMP
NEWDOS80_FDE_SIZE = 32
NEWDOS80_ALLOWED_NAME = set(b"ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789$%'-_@~`!#()^")


@dataclass(slots=True)
class NEWDOS80Extent:
    lump: int
    start_granule: int
    granules: int


@dataclass(slots=True)
class NEWDOS80DirectoryEntry:
    name: str
    native_name: str
    sectors: int
    eof_byte: int
    attributes: int
    extents: list[NEWDOS80Extent]


def _clean_field(raw: bytes) -> str:
    return bytes(byte & 0x7F for byte in raw).decode("ascii", errors="ignore").rstrip()


def _looks_like_name(raw: bytes) -> bool:
    cleaned = bytes(byte & 0x7F for byte in raw).rstrip(b" ")
    return bool(cleaned) and all(byte in NEWDOS80_ALLOWED_NAME for byte in cleaned)


def _decode_extent(lump: int, encoded: int) -> NEWDOS80Extent | None:
    if lump == 0xFF or encoded == 0xFF:
        return None
    start_granule = (encoded >> 5) & 0x07
    granules = (encoded & 0x1F) + 1
    if start_granule >= NEWDOS80_GRANULES_PER_LUMP or granules <= 0:
        return None
    return NEWDOS80Extent(lump=lump, start_granule=start_granule, granules=granules)


def _decode_fde(raw: bytes) -> NEWDOS80DirectoryEntry | None:
    if len(raw) != NEWDOS80_FDE_SIZE:
        return None
    attributes = raw[0]
    if attributes in {0x00, 0xFF} or not (attributes & 0x10) or (attributes & 0x80):
        return None
    if not _looks_like_name(raw[5:13]) or not _looks_like_name(raw[13:16]):
        return None
    stem = _clean_field(raw[5:13])
    suffix = _clean_field(raw[13:16])
    if not stem:
        return None
    extents: list[NEWDOS80Extent] = []
    for offset in range(0x16, NEWDOS80_FDE_SIZE, 2):
        extent = _decode_extent(raw[offset], raw[offset + 1])
        if extent is not None:
            extents.append(extent)
    if not extents:
        return None
    native_name = f"{stem}/{suffix}" if suffix else stem
    display_name = f"{stem}.{suffix}" if suffix else stem
    return NEWDOS80DirectoryEntry(
        name=display_name,
        native_name=native_name,
        sectors=int.from_bytes(raw[0x14:0x16], "little"),
        eof_byte=raw[0x03],
        attributes=attributes,
        extents=extents,
    )


class NEWDOS80Filesystem(Filesystem):
    """Read NEWDOS/80 root directory entries and file extents."""

    def __init__(self) -> None:
        self._image: SectorImage | None = None
        self._entries: list[NEWDOS80DirectoryEntry] = []
        self._label = ""
        self._date = ""
        self._directory_lump = 0

    def probe(self, image: SectorImage) -> bool:
        if getattr(image, "bytes_per_sector", NEWDOS80_SECTOR_SIZE) != NEWDOS80_SECTOR_SIZE:
            return False
        try:
            boot = image.read_sector(0)
        except Exception:
            return False
        if len(boot) != NEWDOS80_SECTOR_SIZE:
            return False
        directory_lump = boot[2]
        if directory_lump == 0 or directory_lump > 0x7F:
            return False
        directory_lba = directory_lump * NEWDOS80_SECTORS_PER_LUMP
        try:
            gat = image.read_sector(directory_lba)
            hit = image.read_sector(directory_lba + 1)
        except Exception:
            return False
        if not self._valid_gat(gat) or not self._valid_hit(hit):
            return False
        extra_fde_sectors = hit[31]
        if extra_fde_sectors not in {0, 5, 10, 15, 20}:
            return False
        fde_sectors = 8 + extra_fde_sectors
        entries = self._read_directory_entries(image, directory_lba + 2, fde_sectors)
        if not entries:
            return False
        self._image = image
        self._entries = entries
        self._directory_lump = directory_lump
        self._label = _clean_field(gat[0xD0:0xD8])
        self._date = _clean_field(gat[0xD8:0xE0])
        return True

    def _valid_gat(self, data: bytes) -> bool:
        if len(data) != NEWDOS80_SECTOR_SIZE:
            return False
        return any(byte != 0x00 for byte in data[:0xC0]) and b"BOOT" in data[0xC0:0xE8]

    def _valid_hit(self, data: bytes) -> bool:
        if len(data) != NEWDOS80_SECTOR_SIZE:
            return False
        return sum(1 for byte in data[:0x80] if byte not in {0x00, 0xFF}) >= 4

    def _read_directory_entries(self, image: SectorImage, first_lba: int, sector_count: int) -> list[NEWDOS80DirectoryEntry]:
        entries: list[NEWDOS80DirectoryEntry] = []
        for offset in range(sector_count):
            try:
                data = image.read_sector(first_lba + offset)
            except Exception:
                break
            for start in range(0, len(data), NEWDOS80_FDE_SIZE):
                entry = _decode_fde(data[start : start + NEWDOS80_FDE_SIZE])
                if entry is not None:
                    entries.append(entry)
        return entries

    def list_directory(self, path: str = "/") -> List[FileEntry]:
        if path not in {"", "/"}:
            raise FilesystemError("NEWDOS/80 reader only supports the root directory")
        return [
            FileEntry(
                name=entry.name,
                is_dir=False,
                size=self._entry_size(entry),
                cluster_start=entry.extents[0].lump * NEWDOS80_GRANULES_PER_LUMP + entry.extents[0].start_granule,
                attributes=entry.attributes,
            )
            for entry in sorted(self._entries, key=lambda item: item.name)
        ]

    def _entry_size(self, entry: NEWDOS80DirectoryEntry) -> int:
        extent_sectors = sum(extent.granules for extent in entry.extents) * NEWDOS80_SECTORS_PER_GRANULE
        sectors = entry.sectors or extent_sectors
        if entry.sectors and entry.eof_byte:
            return max(0, (entry.sectors - 1) * NEWDOS80_SECTOR_SIZE + entry.eof_byte)
        return sectors * NEWDOS80_SECTOR_SIZE

    def extract_file(self, path: str) -> bytes:
        if self._image is None:
            raise FilesystemError("NEWDOS/80 filesystem has not been probed")
        normalized = path.strip("/").upper().replace("/", ".")
        entry = next((item for item in self._entries if item.name.upper() == normalized), None)
        if entry is None:
            raise FilesystemError(f"NEWDOS/80 file not found: {path}")
        data = bytearray()
        for extent in entry.extents:
            start_lba = (
                extent.lump * NEWDOS80_SECTORS_PER_LUMP
                + extent.start_granule * NEWDOS80_SECTORS_PER_GRANULE
            )
            for sector_offset in range(extent.granules * NEWDOS80_SECTORS_PER_GRANULE):
                data.extend(self._image.read_sector(start_lba + sector_offset))
        return bytes(data[: self._entry_size(entry)])

    def metadata(self) -> Dict[str, Any]:
        return {
            "filesystem": "newdos80",
            "label": self._label,
            "date": self._date,
            "directory_lump": self._directory_lump,
            "entries": len(self._entries),
        }


__all__ = ["NEWDOS80Filesystem"]
