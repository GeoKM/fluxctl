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
FAT12_FREE = 0x000


@dataclass(slots=True)
class DirectorySlot:
    image_offset: int


@dataclass(slots=True)
class DirectoryEntry:
    name: str
    extension: str
    attributes: int
    start_cluster: int
    size: int
    raw: bytes
    image_offset: int

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
        return self._replace_file_with_existing_allocation(image_bytes, entry, replacement, update_size=False)

    def replace_file_with_existing_allocation(self, image_bytes: bytes, path: str, replacement: bytes) -> bytes:
        """Return a copy with one file replaced inside its existing FAT chain.

        The FAT chain is not extended or shortened. The directory size field is
        updated in the returned bytes, and any unused tail of the final cluster
        is left as-is.
        """

        entry = self._entry_for_file(path)
        return self._replace_file_with_existing_allocation(image_bytes, entry, replacement, update_size=True)

    def replace_file_allocating_clusters(self, image_bytes: bytes, path: str, replacement: bytes) -> bytes:
        """Return a copy with one file replaced, extending its FAT chain if needed."""

        entry = self._entry_for_file(path)
        existing_clusters = self._cluster_chain(entry.start_cluster)
        cluster_size = self.sectors_per_cluster * self.bytes_per_sector
        required_clusters = (len(replacement) + cluster_size - 1) // cluster_size if replacement else 0
        if required_clusters <= len(existing_clusters):
            return self._replace_file_with_existing_allocation(image_bytes, entry, replacement, update_size=True)
        if not existing_clusters:
            raise FilesystemError("Cannot grow a file without a starting cluster")

        needed = required_clusters - len(existing_clusters)
        free_clusters = self._find_free_clusters(needed)
        clusters = existing_clusters + free_clusters
        patched = bytearray(image_bytes)
        self._write_file_size(patched, entry, len(replacement))
        for current, next_cluster in zip(clusters, clusters[1:]):
            self._set_fat_entry_in_all_copies(patched, current, next_cluster)
        self._set_fat_entry_in_all_copies(patched, clusters[-1], END_OF_CLUSTER)
        self._write_file_clusters(patched, clusters, replacement)
        return bytes(patched)

    def delete_entry(self, image_bytes: bytes, path: str) -> bytes:
        """Return a copy with a file or empty directory deleted."""

        entry = self._entry_for_path(path)
        if entry.full_name in {".", ".."}:
            raise FilesystemError("Cannot delete special directory entries")
        if entry.is_dir:
            children = [child for child in self._entries_for_cluster_chain(entry.start_cluster) if child.full_name not in {".", ".."}]
            if children:
                raise FilesystemError("Directory delete currently requires an empty directory")

        patched = bytearray(image_bytes)
        if entry.image_offset >= len(patched):
            raise FilesystemError("Directory entry exceeds image size")
        patched[entry.image_offset] = 0xE5
        for cluster in self._cluster_chain(entry.start_cluster):
            self._set_fat_entry_in_all_copies(patched, cluster, FAT12_FREE)
        return bytes(patched)

    def import_file(self, image_bytes: bytes, directory: str, filename: str, data: bytes) -> bytes:
        """Return a copy with a host file imported into a FAT12 directory."""

        raw_name = self._encode_8_3_name(filename)
        self._ensure_name_available(directory, filename)
        clusters = self._allocate_clusters_for_data(data)
        patched = bytearray(image_bytes)
        self._write_cluster_chain(patched, clusters)
        self._write_file_clusters(patched, clusters, data)
        slot = self._find_free_directory_slot(directory)
        entry = self._build_directory_entry(raw_name, attributes=0x20, start_cluster=clusters[0] if clusters else 0, size=len(data))
        patched[slot.image_offset : slot.image_offset + 32] = entry
        return bytes(patched)

    def create_directory(self, image_bytes: bytes, parent: str, name: str) -> bytes:
        """Return a copy with one empty directory created."""

        raw_name = self._encode_8_3_name(name)
        self._ensure_name_available(parent, name)
        cluster = self._find_free_clusters(1)[0]
        patched = bytearray(image_bytes)
        self._set_fat_entry_in_all_copies(patched, cluster, END_OF_CLUSTER)
        cluster_offset = self._cluster_to_lba(cluster) * self.bytes_per_sector
        cluster_size = self.sectors_per_cluster * self.bytes_per_sector
        if cluster_offset + cluster_size > len(patched):
            raise FilesystemError("Directory cluster exceeds image size")
        parent_cluster = 0 if parent.strip("/") == "" else self._entry_for_path(parent).start_cluster
        directory_data = bytearray(cluster_size)
        directory_data[0:32] = self._build_directory_entry(self._special_directory_name("."), 0x10, cluster, 0)
        directory_data[32:64] = self._build_directory_entry(self._special_directory_name(".."), 0x10, parent_cluster, 0)
        patched[cluster_offset : cluster_offset + cluster_size] = directory_data
        slot = self._find_free_directory_slot(parent)
        entry = self._build_directory_entry(raw_name, attributes=0x10, start_cluster=cluster, size=0)
        patched[slot.image_offset : slot.image_offset + 32] = entry
        return bytes(patched)

    def _replace_file_with_existing_allocation(
        self,
        image_bytes: bytes,
        entry: DirectoryEntry,
        replacement: bytes,
        *,
        update_size: bool,
    ) -> bytes:
        clusters = self._cluster_chain(entry.start_cluster)
        capacity = len(clusters) * self.sectors_per_cluster * self.bytes_per_sector
        if len(replacement) > capacity:
            raise FilesystemError(
                f"Replacement is {len(replacement):,} bytes but existing FAT chain holds only {capacity:,} bytes"
            )

        patched = bytearray(image_bytes)
        if update_size:
            self._write_file_size(patched, entry, len(replacement))
        if not replacement:
            return bytes(patched)
        self._write_file_clusters(patched, clusters, replacement)
        return bytes(patched)

    def _write_file_size(self, image_bytes: bytearray, entry: DirectoryEntry, size: int) -> None:
        size_offset = entry.image_offset + 28
        if size_offset + 4 > len(image_bytes):
            raise FilesystemError("Directory entry size field exceeds image size")
        image_bytes[size_offset : size_offset + 4] = size.to_bytes(4, "little")

    def _write_file_clusters(self, image_bytes: bytearray, clusters: List[int], data: bytes) -> None:
        written = 0
        cluster_size = self.sectors_per_cluster * self.bytes_per_sector
        for cluster in clusters:
            if written >= len(data):
                break
            offset = self._cluster_to_lba(cluster) * self.bytes_per_sector
            chunk = data[written : written + cluster_size]
            end = offset + len(chunk)
            if end > len(image_bytes):
                raise FilesystemError("Cluster data exceeds image size")
            image_bytes[offset:end] = chunk
            written += len(chunk)

    def _allocate_clusters_for_data(self, data: bytes) -> List[int]:
        if not data:
            return []
        cluster_size = self.sectors_per_cluster * self.bytes_per_sector
        required_clusters = (len(data) + cluster_size - 1) // cluster_size
        return self._find_free_clusters(required_clusters)

    def _write_cluster_chain(self, image_bytes: bytearray, clusters: List[int]) -> None:
        if not clusters:
            return
        for current, next_cluster in zip(clusters, clusters[1:]):
            self._set_fat_entry_in_all_copies(image_bytes, current, next_cluster)
        self._set_fat_entry_in_all_copies(image_bytes, clusters[-1], END_OF_CLUSTER)

    def _build_directory_entry(self, raw_name: bytes, attributes: int, start_cluster: int, size: int) -> bytes:
        if len(raw_name) != 11:
            raise FilesystemError("Internal FAT12 name must be 11 bytes")
        entry = bytearray(32)
        entry[0:11] = raw_name
        entry[11] = attributes
        entry[26:28] = start_cluster.to_bytes(2, "little")
        entry[28:32] = size.to_bytes(4, "little")
        return bytes(entry)

    def _special_directory_name(self, name: str) -> bytes:
        return name.encode("ascii").ljust(11, b" ")

    def _encode_8_3_name(self, name: str) -> bytes:
        if "/" in name or "\\" in name:
            raise FilesystemError("FAT12 names must not contain path separators")
        if name in {"", ".", ".."}:
            raise FilesystemError("Choose a normal FAT12 8.3 name")
        try:
            ascii_name = name.upper().encode("ascii").decode("ascii")
        except UnicodeError as exc:
            raise FilesystemError("FAT12 import currently supports ASCII 8.3 names only") from exc
        invalid = set('"*+,/:;<=>?[\\]|')
        if any(ord(char) < 32 or char in invalid or char == " " for char in ascii_name):
            raise FilesystemError("FAT12 name contains unsupported characters")
        if ascii_name.count(".") > 1:
            raise FilesystemError("FAT12 import currently supports one 8.3 extension separator")
        base, dot, ext = ascii_name.partition(".")
        if not base or len(base) > 8 or (dot and len(ext) > 3):
            raise FilesystemError("FAT12 import currently supports 8.3 names only")
        if not dot:
            ext = ""
        return base.ljust(8).encode("ascii") + ext.ljust(3).encode("ascii")

    def _ensure_name_available(self, directory: str, name: str) -> None:
        entries = self._entries_for_path(directory)
        if self._find_entry(entries, name) is not None:
            raise FilesystemError(f"FAT12 entry already exists: {name}")

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
        entry = self._entry_for_path(path)
        if entry.is_dir:
            raise FilesystemError("Cannot extract a directory entry")
        return entry

    def _entry_for_path(self, path: str) -> DirectoryEntry:
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
            raise FilesystemError(f"Filesystem entry '{path}' not found")
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

    def _find_free_clusters(self, count: int) -> List[int]:
        clusters: List[int] = []
        for cluster in range(2, self.total_clusters + 2):
            if self._fat_entry(cluster) == 0:
                clusters.append(cluster)
                if len(clusters) == count:
                    return clusters
        raise FilesystemError(f"Need {count:,} free FAT12 cluster(s), found {len(clusters):,}")

    def _set_fat_entry_in_all_copies(self, image_bytes: bytearray, cluster: int, value: int) -> None:
        if not 0 <= value <= 0xFFF:
            raise FilesystemError("FAT12 value out of range")
        fat_byte_offset = cluster + cluster // 2
        if fat_byte_offset + 2 > self.sectors_per_fat * self.bytes_per_sector:
            raise FilesystemError("FAT entry out of range")
        for fat_index in range(self.fat_count):
            offset = (self.reserved_sectors + fat_index * self.sectors_per_fat) * self.bytes_per_sector + fat_byte_offset
            if offset + 2 > len(image_bytes):
                raise FilesystemError("FAT copy exceeds image size")
            first = image_bytes[offset]
            second = image_bytes[offset + 1]
            if cluster % 2 == 0:
                image_bytes[offset] = value & 0xFF
                image_bytes[offset + 1] = (second & 0xF0) | ((value >> 8) & 0x0F)
            else:
                image_bytes[offset] = (first & 0x0F) | ((value << 4) & 0xF0)
                image_bytes[offset + 1] = (value >> 4) & 0xFF

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
        root_lba = self.reserved_sectors + self.fat_count * self.sectors_per_fat
        entries = self._parse_directory(self.root_dir_data, root_lba)
        if not parts:
            return entries
        for part in parts:
            match = self._find_entry(entries, part)
            if match is None:
                raise FilesystemError(f"Directory '{path}' not found")
            if not match.is_dir:
                raise FilesystemError(f"'{part}' is not a directory")
            entries = self._entries_for_cluster_chain(match.start_cluster)
        return entries

    def _entries_for_cluster_chain(self, start_cluster: int) -> List[DirectoryEntry]:
        entries: List[DirectoryEntry] = []
        for cluster in self._cluster_chain(start_cluster):
            cluster_lba = self._cluster_to_lba(cluster)
            entries.extend(self._parse_directory(self._read_cluster(cluster), cluster_lba))
        return entries

    def _find_free_directory_slot(self, path: str) -> DirectorySlot:
        if self.image is None:
            raise FilesystemError("Filesystem not initialised")
        if path.strip("/") == "":
            root_lba = self.reserved_sectors + self.fat_count * self.sectors_per_fat
            return self._find_free_slot_in_data(self.root_dir_data, root_lba)
        entry = self._entry_for_path(path)
        if not entry.is_dir:
            raise FilesystemError(f"'{path}' is not a directory")
        for cluster in self._cluster_chain(entry.start_cluster):
            cluster_lba = self._cluster_to_lba(cluster)
            try:
                return self._find_free_slot_in_data(self._read_cluster(cluster), cluster_lba)
            except FilesystemError:
                continue
        raise FilesystemError("Directory has no free entry slots")

    def _find_free_slot_in_data(self, data: bytes, base_lba: int) -> DirectorySlot:
        for idx in range(0, len(data), 32):
            entry = data[idx : idx + 32]
            if len(entry) < 32:
                break
            if entry[0] in {0x00, 0xE5}:
                return DirectorySlot(base_lba * self.bytes_per_sector + idx)
        raise FilesystemError("Directory has no free entry slots")

    def _parse_directory(self, data: bytes, base_lba: int) -> List[DirectoryEntry]:
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
            if name in {".", ".."}:
                continue
            start_cluster = int.from_bytes(entry[26:28], "little")
            size = int.from_bytes(entry[28:32], "little")
            if start_cluster == 0 and (attributes & 0x10 or size > 0):
                continue
            entries.append(
                DirectoryEntry(
                    name=name,
                    extension=ext,
                    attributes=attributes,
                    start_cluster=start_cluster,
                    size=size,
                    raw=entry,
                    image_offset=base_lba * self.bytes_per_sector + idx,
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
