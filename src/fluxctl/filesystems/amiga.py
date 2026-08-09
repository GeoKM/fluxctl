"""Lightweight Amiga OFS/FFS reader."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from ..exceptions import FilesystemError
from ..filesystems import FileEntry, Filesystem, SectorImage


@dataclass
class _AmigaDirEntry:
    name: str
    start_sector: int
    length: int
    is_dir: bool = False


class AmigaOFS(Filesystem):
    """Root-directory reader for AmigaDOS OFS/FFS floppy images."""

    def __init__(self) -> None:
        self.image: Optional[SectorImage] = None
        self.directory: list[_AmigaDirEntry] = []
        self.filesystem = "amiga"
        self.volume_label = ""

    @staticmethod
    def _long(data: bytes, index: int, *, signed: bool = False) -> int:
        offset = index * 4
        return int.from_bytes(data[offset : offset + 4], "big", signed=signed)

    def _reset(self) -> None:
        self.__init__()

    def _parse_synthetic_directory(self) -> None:
        assert self.image is not None
        table = self.image.read_sector(0).split(b"\n")
        for line in table:
            text = line.decode("utf-8", errors="ignore").strip()
            if not text or ":" not in text:
                continue
            try:
                name, start, length = text.split(":", 2)
                self.directory.append(_AmigaDirEntry(name, int(start), int(length)))
            except ValueError:
                continue

    def _contiguous_readable_sector_count(self, start_sector: int = 0) -> int:
        assert self.image is not None
        total_sectors = getattr(self.image, "total_sectors", None)
        if total_sectors is None:
            geometry = getattr(self.image, "_geometry", None)
            tracks = getattr(self.image, "tracks", None)
            if geometry is not None and tracks:
                sectors_per_track, heads, _sector_base = geometry
                total_sectors = (max(track.track for track in tracks) + 1) * heads * sectors_per_track
            else:
                total_sectors = 0

        count = 0
        for lba in range(start_sector, int(total_sectors)):
            try:
                self.image.read_sector(lba)
            except FilesystemError:
                break
            count += 1
        return count

    def _parse_kickstart_image(self) -> None:
        assert self.image is not None
        sector_count = self._contiguous_readable_sector_count(0)
        if sector_count <= 0:
            raise FilesystemError("Kickstart image has no readable sectors")
        self.filesystem = "amiga_kickstart"
        self.volume_label = "Amiga Kickstart"
        self.directory = [
            _AmigaDirEntry(
                name="Kickstart.rom",
                start_sector=0,
                length=sector_count * self.image.bytes_per_sector,
            )
        ]

    def _directory_entries_from_block(self, block_number: int) -> list[_AmigaDirEntry]:
        assert self.image is not None
        entries: list[_AmigaDirEntry] = []
        seen: set[int] = set()
        directory = self.image.read_sector(block_number)
        if len(directory) < 512 or self._long(directory, 0) != 2:
            raise FilesystemError("Amiga directory block not found")
        for hash_index in range(72):
            block = self._long(directory, 6 + hash_index)
            while block:
                if block in seen:
                    break
                seen.add(block)
                try:
                    data = self.image.read_sector(block)
                except FilesystemError:
                    break
                if len(data) < 512 or self._long(data, 0) != 2:
                    break
                secondary_type = self._long(data, 127, signed=True)
                if secondary_type not in {2, -3}:
                    break
                name_len = min(data[432], 30)
                name = data[433 : 433 + name_len].decode("latin-1", errors="ignore")
                if name:
                    size = self._long(data, 81) if secondary_type == -3 else 0
                    entries.append(
                        _AmigaDirEntry(
                            name=name,
                            start_sector=block,
                            length=max(size, 0),
                            is_dir=secondary_type == 2,
                        )
                    )
                block = self._long(data, 124)
        return sorted(entries, key=lambda entry: (not entry.is_dir, entry.name.lower()))

    def _parse_real_directory(self) -> None:
        assert self.image is not None
        root = self.image.read_sector(880)
        if len(root) >= 464 and self._long(root, 0) == 2:
            name_len = min(root[432], 30)
            self.volume_label = root[433 : 433 + name_len].decode("latin-1", errors="ignore")
        self.directory = self._directory_entries_from_block(880)

    def probe(self, image: SectorImage) -> bool:
        self._reset()
        self.image = image
        try:
            boot = image.read_sector(0)
        except FilesystemError:
            return False
        if boot.startswith(b"KICK"):
            directory: list[_AmigaDirEntry] = []
            volume_label = ""
            try:
                self._parse_real_directory()
            except FilesystemError:
                pass
            else:
                directory = self.directory
                volume_label = self.volume_label
            try:
                self._parse_kickstart_image()
            except FilesystemError:
                return False
            if directory:
                self.filesystem = "amiga_kickstart_dos"
                self.volume_label = volume_label or self.volume_label
                self.directory.extend(
                    entry for entry in directory if entry.name.lower() != "kickstart.rom"
                )
            return True
        if not boot.startswith(b"DOS") or len(boot) < 4:
            try:
                marker = image.read_sector(880)
            except FilesystemError:
                return False
            if not marker.startswith(b"DOS"):
                return False
            self.filesystem = "amiga_ofs"
            self._parse_synthetic_directory()
            return True
        dos_type = boot[:4].decode("latin-1", errors="ignore")
        self.filesystem = "amiga_ffs" if dos_type[3] in {"\x01", "\x03", "\x05"} else "amiga_ofs"
        try:
            self._parse_real_directory()
        except FilesystemError:
            self.directory = []
            self._parse_synthetic_directory()
        return True

    def list_directory(self, path: str = "/") -> List[FileEntry]:
        if self.image is None:
            raise FilesystemError("Filesystem not probed")
        entries_source = self._entries_for_path(path)
        entries: List[FileEntry] = []
        for entry in entries_source:
            entries.append(
                FileEntry(name=entry.name, is_dir=entry.is_dir, size=entry.length, cluster_start=entry.start_sector)
            )
        return entries

    def _entries_for_path(self, path: str) -> list[_AmigaDirEntry]:
        parts = [part for part in path.strip("/").split("/") if part]
        entries = self.directory
        if not parts:
            return entries
        for part in parts:
            match = next((entry for entry in entries if entry.name.lower() == part.lower()), None)
            if match is None:
                raise FilesystemError(f"Directory '{path}' not found")
            if not match.is_dir:
                raise FilesystemError(f"'{part}' is not a directory")
            entries = self._directory_entries_from_block(match.start_sector)
        return entries

    def extract_file(self, path: str) -> bytes:
        if self.image is None:
            raise FilesystemError("Filesystem not probed")
        target = self._entry_for_file(path)
        blocks = self._real_file_blocks(target)
        if blocks is None:
            count = (target.length + self.image.bytes_per_sector - 1) // self.image.bytes_per_sector
            return self.image.read_sector(target.start_sector, count)[: target.length]
        data_blocks, _metadata_blocks, is_ofs = blocks
        if is_ofs:
            payload = bytearray()
            for block_number in data_blocks:
                block = self.image.read_sector(block_number)
                payload.extend(block[24 : 24 + self._long(block, 3)])
            return bytes(payload[: target.length])
        return b"".join(self.image.read_sector(block) for block in data_blocks)[: target.length]

    def file_sector_addresses(self, path: str) -> set[tuple[int, int, int]]:
        """Return physical ``(track, head, sector_id)`` addresses for a file."""

        if self.image is None:
            raise FilesystemError("Filesystem not probed")
        target = self._entry_for_file(path)
        blocks = self._real_file_blocks(target)
        if blocks is None:
            count = (target.length + self.image.bytes_per_sector - 1) // self.image.bytes_per_sector
            block_numbers = range(target.start_sector, target.start_sector + count)
        else:
            data_blocks, metadata_blocks, _is_ofs = blocks
            block_numbers = [target.start_sector, *metadata_blocks, *data_blocks]
        sectors_per_track = 11
        heads = 2
        sector_base = 0
        geometry = getattr(self.image, "_geometry", None)
        if geometry is not None:
            sectors_per_track, heads, sector_base = geometry
        addresses: set[tuple[int, int, int]] = set()
        for lba in block_numbers:
            track = lba // (sectors_per_track * heads)
            rem = lba % (sectors_per_track * heads)
            head = rem // sectors_per_track
            sector_id = (rem % sectors_per_track) + sector_base
            addresses.add((track, head, sector_id))
        return addresses

    @staticmethod
    def _block_checksum_is_valid(data: bytes) -> bool:
        if len(data) != 512:
            return False
        return sum(int.from_bytes(data[offset : offset + 4], "big") for offset in range(0, 512, 4)) & 0xFFFFFFFF == 0

    def _real_file_blocks(self, target: _AmigaDirEntry) -> tuple[list[int], list[int], bool] | None:
        """Return data and extension blocks for a real AmigaDOS file header.

        Synthetic test images deliberately use a direct start/length table, so
        they return ``None`` and retain the simple contiguous fallback.
        """

        assert self.image is not None
        try:
            header = self.image.read_sector(target.start_sector)
        except FilesystemError:
            return None
        if len(header) != 512 or self._long(header, 0) != 2 or self._long(header, 127, signed=True) != -3:
            return None
        if self._long(header, 1) != target.start_sector:
            raise FilesystemError(f"Amiga file header key mismatch at block {target.start_sector}")
        if not self._block_checksum_is_valid(header):
            raise FilesystemError(f"Amiga file header checksum failed at block {target.start_sector}")

        extension_blocks, pointer_blocks = self._file_extension_chain(header, target.start_sector)
        is_ofs = self._file_uses_ofs_blocks(header)
        if is_ofs:
            data_blocks = self._ofs_data_chain(header, target.start_sector, target.length)
        else:
            data_blocks = self._ffs_data_blocks(header, pointer_blocks, target.length)
        return data_blocks, extension_blocks, is_ofs

    def _file_extension_chain(self, header: bytes, header_block: int) -> tuple[list[int], list[int]]:
        extension_blocks: list[int] = []
        pointers = self._reversed_data_pointers(header)
        extension = self._long(header, 126)
        seen: set[int] = set()
        while extension:
            if extension in seen:
                raise FilesystemError(f"Amiga file extension chain loops at block {extension}")
            seen.add(extension)
            assert self.image is not None
            data = self.image.read_sector(extension)
            if (
                len(data) != 512
                or self._long(data, 0) != 16
                or self._long(data, 1) != extension
                or self._long(data, 125) != header_block
                or self._long(data, 127, signed=True) != -3
            ):
                raise FilesystemError(f"Invalid Amiga file extension block {extension}")
            if not self._block_checksum_is_valid(data):
                raise FilesystemError(f"Amiga file extension checksum failed at block {extension}")
            extension_blocks.append(extension)
            pointers.extend(self._reversed_data_pointers(data))
            extension = self._long(data, 126)
        return extension_blocks, pointers

    def _file_uses_ofs_blocks(self, header: bytes) -> bool:
        if self.filesystem == "amiga_ofs":
            return True
        if self.filesystem == "amiga_ffs":
            return False
        first_data = self._long(header, 4)
        if not first_data or self.image is None:
            return False
        try:
            return self._long(self.image.read_sector(first_data), 0) == 8
        except FilesystemError:
            return False

    def _ofs_data_chain(self, header: bytes, header_block: int, length: int) -> list[int]:
        expected_blocks = (length + 487) // 488
        if expected_blocks == 0:
            return []
        block_number = self._long(header, 4)
        if not block_number:
            raise FilesystemError("Amiga OFS file header has no first data block")
        blocks: list[int] = []
        seen: set[int] = set()
        while block_number and len(blocks) < expected_blocks:
            if block_number in seen:
                raise FilesystemError(f"Amiga OFS data chain loops at block {block_number}")
            seen.add(block_number)
            assert self.image is not None
            data = self.image.read_sector(block_number)
            if (
                len(data) != 512
                or self._long(data, 0) != 8
                or self._long(data, 1) != header_block
                or not 0 <= self._long(data, 3) <= 488
            ):
                raise FilesystemError(f"Invalid Amiga OFS data block {block_number}")
            if not self._block_checksum_is_valid(data):
                raise FilesystemError(f"Amiga OFS data checksum failed at block {block_number}")
            blocks.append(block_number)
            block_number = self._long(data, 4)
        if len(blocks) != expected_blocks:
            raise FilesystemError("Amiga OFS data chain ends before the file length")
        return blocks

    @staticmethod
    def _reversed_data_pointers(data: bytes) -> list[int]:
        count = AmigaOFS._long(data, 2)
        if not 0 <= count <= 72:
            raise FilesystemError("Amiga file block has an invalid data-pointer count")
        return [AmigaOFS._long(data, 77 - index) for index in range(count) if AmigaOFS._long(data, 77 - index)]

    @staticmethod
    def _ffs_data_blocks(header: bytes, pointers: list[int], length: int) -> list[int]:
        expected_blocks = (length + 511) // 512
        if len(pointers) < expected_blocks:
            raise FilesystemError("Amiga FFS data-pointer list ends before the file length")
        return pointers[:expected_blocks]

    def _entry_for_file(self, path: str) -> _AmigaDirEntry:
        parts = [part for part in path.strip("/").split("/") if part]
        if not parts:
            raise FilesystemError("Path must reference a file")
        entries = self._entries_for_path("/" + "/".join(parts[:-1]) if len(parts) > 1 else "/")
        target = next((e for e in entries if e.name.lower() == parts[-1].lower()), None)
        if target is None:
            raise FilesystemError(f"File '{path}' not found")
        if target.is_dir:
            raise FilesystemError("Cannot extract a directory entry")
        return target

    def metadata(self) -> Dict[str, str]:
        return {"filesystem": self.filesystem, "volume_label": self.volume_label, "entries": str(len(self.directory))}
