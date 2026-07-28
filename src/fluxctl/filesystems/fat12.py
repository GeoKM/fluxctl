"""FAT12 filesystem plugin.

This implementation focuses on classic MS-DOS FAT12 disks used on PCs and does
not yet support long file names or advanced attributes. Directory parsing is
limited to 8.3 entries and assumes standard media geometry derived from the
boot sector.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from typing import Dict, Iterable, List, Optional, Tuple

from ..exceptions import FilesystemError
from . import FileEntry, Filesystem, SectorImage, TrackSectorImage

END_OF_CLUSTER = 0xFF8


@dataclass(slots=True)
class DirectoryEntry:
    name: str
    extension: str
    attributes: int
    start_cluster: int
    size: int
    raw: bytes

    @property
    def is_dir(self) -> bool:
        return bool(self.attributes & 0x10)

    @property
    def full_name(self) -> str:
        base = self.name.strip()
        ext = self.extension.strip()
        if ext:
            return f"{base}.{ext}"
        return base


class FAT12(Filesystem):
    """Minimal FAT12 reader that supports listing and file extraction."""

    def __init__(self) -> None:
        self.image: Optional[SectorImage] = None
        self.boot_sector: bytes = b""
        self.bytes_per_sector: int = 0
        self.sectors_per_cluster: int = 0
        self.reserved_sectors: int = 0
        self.fat_count: int = 0
        self.root_entry_count: int = 0
        self.sectors_per_fat: int = 0
        self.sectors_per_track: int = 0
        self.heads: int = 0
        self.total_sectors: int = 0
        self.root_dir_sectors: int = 0
        self.data_region_start: int = 0
        self.total_clusters: int = 0
        self.fat: bytes = b""
        self.root_dir_data: bytes = b""

    def _reset(self) -> None:
        self.__init__()

    def probe(self, image: SectorImage) -> bool:
        """Inspect the boot sector and load FAT metadata."""

        self._reset()
        self.image = image
        try:
            boot = image.read_sector(0)
        except FilesystemError:
            return False
        if len(boot) < 512:
            return False

        has_signature = boot[510:512] == b"\x55\xAA"
        try:
            self._parse_boot_sector(boot)
        except FilesystemError:
            return False
        if not has_signature and self.total_clusters <= 0:
            return False

        try:
            self._load_fat()
            self._load_root_directory()
        except FilesystemError:
            return False
        return True

    def list_directory(self, path: str = "/") -> List[FileEntry]:
        entries = self._entries_for_path(path)
        return [self._to_file_entry(e) for e in entries]

    def extract_file(self, path: str) -> bytes:
        entry = self._entry_for_file(path)
        data = self._read_cluster_chain(entry.start_cluster)
        return data[: entry.size]

    def replace_file_same_size(self, image_bytes: bytes, path: str, replacement: bytes) -> bytes:
        """Return a copy of ``image_bytes`` with one file's cluster data replaced.

        This intentionally supports only same-size replacement. No FAT entries,
        directory sizes, timestamps, or allocation structures are modified.
        """

        entry = self._entry_for_file(path)
        if len(replacement) != entry.size:
            raise FilesystemError(
                f"Replacement must be exactly {entry.size:,} bytes for same-size FAT12 replacement"
            )
        if not replacement:
            return bytes(image_bytes)

        clusters = self._cluster_chain(entry.start_cluster)
        capacity = len(clusters) * self.sectors_per_cluster * self.bytes_per_sector
        if len(replacement) > capacity:
            raise FilesystemError("FAT chain is too short for the file size")

        patched = bytearray(image_bytes)
        written = 0
        cluster_size = self.sectors_per_cluster * self.bytes_per_sector
        for cluster in clusters:
            if written >= len(replacement):
                break
            offset = self._cluster_to_lba(cluster) * self.bytes_per_sector
            chunk = replacement[written : written + cluster_size]
            end = offset + len(chunk)
            if end > len(patched):
                raise FilesystemError("Cluster data exceeds image size")
            patched[offset:end] = chunk
            written += len(chunk)
        return bytes(patched)

    def metadata(self) -> Dict[str, int]:
        return {
            "bytes_per_sector": self.bytes_per_sector,
            "sectors_per_cluster": self.sectors_per_cluster,
            "reserved_sectors": self.reserved_sectors,
            "fat_count": self.fat_count,
            "sectors_per_fat": self.sectors_per_fat,
            "root_dir_sectors": self.root_dir_sectors,
            "data_region_start": self.data_region_start,
            "total_clusters": self.total_clusters,
            "total_sectors": self.total_sectors,
        }

    def _parse_boot_sector(self, boot: bytes) -> None:
        self.boot_sector = boot
        self.bytes_per_sector = int.from_bytes(boot[11:13], "little")
        self.sectors_per_cluster = boot[13]
        self.reserved_sectors = int.from_bytes(boot[14:16], "little")
        self.fat_count = boot[16]
        self.root_entry_count = int.from_bytes(boot[17:19], "little")
        total_sectors_16 = int.from_bytes(boot[19:21], "little")
        total_sectors_32 = int.from_bytes(boot[32:36], "little")
        self.total_sectors = total_sectors_16 or total_sectors_32
        self.sectors_per_fat = int.from_bytes(boot[22:24], "little")
        self.sectors_per_track = int.from_bytes(boot[24:26], "little")
        self.heads = int.from_bytes(boot[26:28], "little")

        if self.bytes_per_sector not in {512}:
            raise FilesystemError("Unsupported bytes per sector for FAT12")
        if not self.sectors_per_cluster or self.sectors_per_cluster > 8:
            raise FilesystemError("Invalid sectors per cluster for FAT12")
        if self.fat_count < 1:
            raise FilesystemError("No FATs present")
        if self.sectors_per_fat == 0:
            raise FilesystemError("Missing sectors per FAT")

        self.root_dir_sectors = (self.root_entry_count * 32 + self.bytes_per_sector - 1) // self.bytes_per_sector
        self.data_region_start = self.reserved_sectors + self.fat_count * self.sectors_per_fat + self.root_dir_sectors
        if self.total_sectors <= self.data_region_start:
            raise FilesystemError("Total sectors too small for FAT12 image")
        self.total_clusters = (self.total_sectors - self.data_region_start) // self.sectors_per_cluster

        if isinstance(self.image, TrackSectorImage):
            self.image.set_geometry(self.sectors_per_track, self.heads)

    def _load_fat(self) -> None:
        if self.image is None:
            raise FilesystemError("Filesystem not initialised")
        self.fat = self.image.read_sector(self.reserved_sectors, self.sectors_per_fat)

    def _load_root_directory(self) -> None:
        if self.image is None:
            raise FilesystemError("Filesystem not initialised")
        self.root_dir_data = self.image.read_sector(
            self.reserved_sectors + self.fat_count * self.sectors_per_fat,
            self.root_dir_sectors,
        )

    def _read_cluster_chain(self, start_cluster: int) -> bytes:
        data_segments = [self._read_cluster(cluster) for cluster in self._cluster_chain(start_cluster)]
        return b"".join(data_segments)

    def _cluster_chain(self, start_cluster: int) -> List[int]:
        clusters: List[int] = []
        current = start_cluster
        visited = set()
        while 2 <= current < END_OF_CLUSTER:
            if current in visited:
                raise FilesystemError("Cycle detected in FAT chain")
            visited.add(current)
            clusters.append(current)
            current = self._fat_entry(current)
        return clusters

    def _entry_for_file(self, path: str) -> DirectoryEntry:
        if self.image is None:
            raise FilesystemError("Filesystem not initialised")

        parts = [p for p in path.strip("/").split("/") if p]
        if not parts:
            raise FilesystemError("Path must reference a file")
        filename = parts[-1]
        directory_path = "/" + "/".join(parts[:-1]) if len(parts) > 1 else "/"
        directory_entries = self._entries_for_path(directory_path)
        entry = self._find_entry(directory_entries, filename)
        if entry is None:
            raise FilesystemError(f"File '{path}' not found")
        if entry.is_dir:
            raise FilesystemError("Cannot extract a directory entry")
        return entry

    def _fat_entry(self, cluster: int) -> int:
        offset = cluster + cluster // 2
        try:
            first, second = self.fat[offset : offset + 2]
        except ValueError as exc:
            raise FilesystemError("FAT entry out of range") from exc
        if cluster % 2 == 0:
            value = first | ((second & 0x0F) << 8)
        else:
            value = ((first & 0xF0) >> 4) | (second << 4)
        return value & 0xFFF

    def _cluster_to_lba(self, cluster: int) -> int:
        return self.data_region_start + (cluster - 2) * self.sectors_per_cluster

    def _read_cluster(self, cluster: int) -> bytes:
        if self.image is None:
            raise FilesystemError("Filesystem not initialised")
        lba = self._cluster_to_lba(cluster)
        return self.image.read_sector(lba, self.sectors_per_cluster)

    def _entries_for_path(self, path: str) -> List[DirectoryEntry]:
        if self.image is None:
            raise FilesystemError("Filesystem not initialised")
        parts = [p for p in path.strip("/").split("/") if p]
        entries = self._parse_directory(self.root_dir_data)
        if not parts:
            return entries
        for part in parts:
            match = self._find_entry(entries, part)
            if match is None:
                raise FilesystemError(f"Directory '{path}' not found")
            if not match.is_dir:
                raise FilesystemError(f"'{part}' is not a directory")
            dir_bytes = self._read_cluster_chain(match.start_cluster)
            entries = self._parse_directory(dir_bytes)
        return entries

    def _parse_directory(self, data: bytes) -> List[DirectoryEntry]:
        entries: List[DirectoryEntry] = []
        for idx in range(0, len(data), 32):
            entry = data[idx : idx + 32]
            if len(entry) < 32:
                break
            if entry[0] == 0x00:
                break
            if entry[0] == 0xE5:
                continue
            attributes = entry[11]
            if attributes & 0x08:
                continue
            name = entry[0:8].decode("ascii", errors="replace").rstrip()
            ext = entry[8:11].decode("ascii", errors="replace").rstrip()
            start_cluster = int.from_bytes(entry[26:28], "little")
            size = int.from_bytes(entry[28:32], "little")
            if start_cluster == 0:
                continue
            entries.append(
                DirectoryEntry(
                    name=name,
                    extension=ext,
                    attributes=attributes,
                    start_cluster=start_cluster,
                    size=size,
                    raw=entry,
                )
            )
        return entries

    def _find_entry(self, entries: Iterable[DirectoryEntry], name: str) -> Optional[DirectoryEntry]:
        target = name.upper()
        for entry in entries:
            if entry.full_name.upper() == target:
                return entry
        return None

    def _decode_timestamp(self, entry: DirectoryEntry) -> Optional[str]:
        mod_time = int.from_bytes(entry.raw[22:24], "little")
        mod_date = int.from_bytes(entry.raw[24:26], "little")
        if mod_date == 0:
            return None
        try:
            dt = date(
                year=((mod_date >> 9) & 0x7F) + 1980,
                month=(mod_date >> 5) & 0x0F,
                day=mod_date & 0x1F,
            )
            tm = time(
                hour=(mod_time >> 11) & 0x1F,
                minute=(mod_time >> 5) & 0x3F,
                second=(mod_time & 0x1F) * 2,
            )
            return f"{dt.isoformat()}T{tm.isoformat()}"
        except ValueError:
            return None

    def _to_file_entry(self, entry: DirectoryEntry) -> FileEntry:
        return FileEntry(
            name=entry.full_name,
            is_dir=entry.is_dir,
            size=entry.size,
            cluster_start=entry.start_cluster,
            modified=self._decode_timestamp(entry),
            attributes=entry.attributes,
        )
