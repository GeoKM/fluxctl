"""LDOS 5.x / TRSDOS 6.x directory reader for TRS-80/Tandy media."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from ..exceptions import FilesystemError
from . import FileEntry, Filesystem, SectorImage


LDOS_DIRECTORY_TRACK = 20
LDOS_SECTORS_PER_TRACK = 18
LDOS_SECTOR_SIZE = 256
LDOS_GRANULE_SECTORS = 6
LDOS_GRANULES_PER_TRACK = LDOS_SECTORS_PER_TRACK // LDOS_GRANULE_SECTORS
LDOS_DIRECTORY_ENTRY_SIZE = 32
LDOS_ALLOWED_NAME = set(b"ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789$%'-_@~`!#()^")


@dataclass(slots=True)
class LDOSExtent:
    track: int
    start_granule: int
    granules: int


@dataclass(slots=True)
class LDOSDirectoryEntry:
    name: str
    native_name: str
    sectors: int
    eof_byte: int
    record_length: int
    attributes: int
    extents: list[LDOSExtent]


def _clean_field(raw: bytes) -> str:
    return bytes(byte & 0x7F for byte in raw).decode("ascii", errors="ignore").rstrip()


def _looks_like_name(raw: bytes) -> bool:
    cleaned = bytes(byte & 0x7F for byte in raw).rstrip(b" ")
    return bool(cleaned) and all(byte in LDOS_ALLOWED_NAME for byte in cleaned)


def _decode_extent(track: int, encoded: int) -> LDOSExtent | None:
    if track == 0xFF or encoded == 0xFF:
        return None
    if track >= 96:
        return None
    start_granule = (encoded >> 5) & 0x07
    granules = (encoded & 0x1F) + 1
    if start_granule >= LDOS_GRANULES_PER_TRACK:
        return None
    return LDOSExtent(track=track, start_granule=start_granule, granules=granules)


def _decode_entry(raw: bytes) -> LDOSDirectoryEntry | None:
    if len(raw) != LDOS_DIRECTORY_ENTRY_SIZE:
        return None
    attributes = raw[0]
    if attributes in {0x00, 0xFF} or not (attributes & 0x10) or attributes & 0x80:
        return None
    if not _looks_like_name(raw[5:13]) or not _looks_like_name(raw[13:16]):
        return None
    stem = _clean_field(raw[5:13])
    suffix = _clean_field(raw[13:16])
    if not stem:
        return None
    extents: list[LDOSExtent] = []
    for offset in range(0x16, LDOS_DIRECTORY_ENTRY_SIZE, 2):
        extent = _decode_extent(raw[offset], raw[offset + 1])
        if extent is not None:
            extents.append(extent)
    if not extents:
        return None
    native_name = f"{stem}/{suffix}" if suffix else stem
    display_name = f"{stem}.{suffix}" if suffix else stem
    return LDOSDirectoryEntry(
        name=display_name,
        native_name=native_name,
        sectors=int.from_bytes(raw[0x14:0x16], "little"),
        eof_byte=raw[0x03],
        record_length=raw[0x04] or LDOS_SECTOR_SIZE,
        attributes=attributes,
        extents=extents,
    )


class LDOSTRSDOS6Filesystem(Filesystem):
    """Read LDOS 5.x and TRSDOS/LS-DOS 6.x root directory entries."""

    def __init__(self) -> None:
        self._image: SectorImage | None = None
        self._entries: list[LDOSDirectoryEntry] = []
        self._label = ""
        self._date = ""

    def probe(self, image: SectorImage) -> bool:
        if getattr(image, "bytes_per_sector", LDOS_SECTOR_SIZE) != LDOS_SECTOR_SIZE:
            return False
        try:
            gat = self._read_chs(image, LDOS_DIRECTORY_TRACK, 0)
            hit = self._read_chs(image, LDOS_DIRECTORY_TRACK, 1)
            entries = self._read_directory_entries(image)
        except Exception:
            return False
        if self._valid_gat(gat) and self._valid_hit(hit) and entries:
            self._image = image
            self._entries = entries
            self._label = _clean_field(gat[0xD0:0xD8])
            self._date = _clean_field(gat[0xD8:0xE0])
            return True
        return False

    def _read_chs(self, image: SectorImage, track: int, sector_id: int) -> bytes:
        return image.read_sector(track * LDOS_SECTORS_PER_TRACK + sector_id)

    def _valid_gat(self, data: bytes) -> bool:
        if len(data) != LDOS_SECTOR_SIZE:
            return False
        gat = data[:96]
        lockout = data[0x60:0xC0]
        return any(byte for byte in gat) and any(byte not in {0x00, 0xFF} for byte in lockout)

    def _valid_hit(self, data: bytes) -> bool:
        if len(data) != LDOS_SECTOR_SIZE:
            return False
        hit = data[:128]
        return sum(1 for byte in hit if byte not in {0x00, 0xFF}) >= 4

    def _read_directory_entries(self, image: SectorImage) -> list[LDOSDirectoryEntry]:
        entries: list[LDOSDirectoryEntry] = []
        for sector_id in range(2, LDOS_SECTORS_PER_TRACK):
            data = self._read_chs(image, LDOS_DIRECTORY_TRACK, sector_id)
            for start in range(0, len(data), LDOS_DIRECTORY_ENTRY_SIZE):
                entry = _decode_entry(data[start : start + LDOS_DIRECTORY_ENTRY_SIZE])
                if entry is not None:
                    entries.append(entry)
        return entries

    def list_directory(self, path: str = "/") -> List[FileEntry]:
        if path not in {"", "/"}:
            raise FilesystemError("LDOS/TRSDOS 6 reader only supports the root directory")
        return [
            FileEntry(
                name=entry.name,
                is_dir=False,
                size=self._entry_size(entry),
                cluster_start=entry.extents[0].track * LDOS_GRANULES_PER_TRACK + entry.extents[0].start_granule,
                attributes=entry.attributes,
            )
            for entry in sorted(self._entries, key=lambda item: item.name)
        ]

    def _entry_size(self, entry: LDOSDirectoryEntry) -> int:
        extent_sectors = sum(extent.granules for extent in entry.extents) * LDOS_GRANULE_SECTORS
        sectors = entry.sectors or extent_sectors
        if entry.sectors and entry.eof_byte:
            return max(0, (entry.sectors - 1) * LDOS_SECTOR_SIZE + entry.eof_byte)
        return sectors * LDOS_SECTOR_SIZE

    def extract_file(self, path: str) -> bytes:
        if self._image is None:
            raise FilesystemError("LDOS/TRSDOS 6 filesystem has not been probed")
        normalized = path.strip("/").upper().replace("/", ".")
        entry = next((item for item in self._entries if item.name.upper() == normalized), None)
        if entry is None:
            raise FilesystemError(f"LDOS/TRSDOS 6 file not found: {path}")
        data = bytearray()
        for extent in entry.extents:
            for granule_offset in range(extent.granules):
                absolute_granule = extent.start_granule + granule_offset
                track = extent.track + absolute_granule // LDOS_GRANULES_PER_TRACK
                first_sector = (absolute_granule % LDOS_GRANULES_PER_TRACK) * LDOS_GRANULE_SECTORS
                for sector_offset in range(LDOS_GRANULE_SECTORS):
                    data.extend(self._read_chs(self._image, track, first_sector + sector_offset))
        return bytes(data[: self._entry_size(entry)])

    def file_sector_addresses(self, path: str) -> set[tuple[int, int, int]]:
        normalized = path.strip("/").upper().replace("/", ".")
        entry = next((item for item in self._entries if item.name.upper() == normalized), None)
        if entry is None:
            raise FilesystemError(f"LDOS/TRSDOS 6 file not found: {path}")
        addresses: set[tuple[int, int, int]] = set()
        for extent in entry.extents:
            for granule_offset in range(extent.granules):
                absolute_granule = extent.start_granule + granule_offset
                track = extent.track + absolute_granule // LDOS_GRANULES_PER_TRACK
                first_sector = (absolute_granule % LDOS_GRANULES_PER_TRACK) * LDOS_GRANULE_SECTORS
                for sector_offset in range(LDOS_GRANULE_SECTORS):
                    addresses.add((track, 0, first_sector + sector_offset))
        return addresses

    def metadata(self) -> Dict[str, Any]:
        return {
            "filesystem": "ldos_trsdos6",
            "label": self._label,
            "date": self._date,
            "entries": len(self._entries),
        }


__all__ = ["LDOSTRSDOS6Filesystem"]
