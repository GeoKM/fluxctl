"""Read-only Apple ProDOS filesystem support."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..apple2 import Apple2SectorImage
from ..exceptions import FilesystemError
from . import FileEntry, Filesystem, SectorImage


@dataclass(frozen=True)
class ProDOSEntry:
    name: str
    storage_type: int
    file_type: int
    key_pointer: int
    blocks_used: int
    eof: int
    access: int
    aux_type: int
    header_pointer: int

    @property
    def is_dir(self) -> bool:
        return self.storage_type == 0x0D


class ProDOSFilesystem(Filesystem):
    """ProDOS volume, directory, and seedling/sapling/tree file reader."""

    def __init__(self) -> None:
        self.image: Optional[SectorImage] = None
        self.volume_name = ""
        self.total_blocks = 0
        self.bitmap_pointer = 0
        self.file_count = 0
        self.entry_length = 39
        self.entries_per_block = 13

    def _reset(self) -> None:
        self.__init__()

    def probe(self, image: SectorImage) -> bool:
        self._reset()
        self.image = image
        if image.bytes_per_sector != 512:
            return False
        try:
            directory = image.read_sector(2)
        except Exception:
            return False
        if len(directory) != 512:
            return False
        storage_name = directory[4]
        if storage_name >> 4 != 0x0F:
            return False
        name_length = storage_name & 0x0F
        if not 1 <= name_length <= 15:
            return False
        name = directory[5 : 5 + name_length]
        if any(byte < 0x20 or byte > 0x7E for byte in name):
            return False
        entry_length = directory[0x23]
        entries_per_block = directory[0x24]
        total_blocks = int.from_bytes(directory[0x29:0x2B], "little")
        bitmap_pointer = int.from_bytes(directory[0x27:0x29], "little")
        if entry_length < 39 or not 1 <= entries_per_block <= 13:
            return False
        available_blocks = int(getattr(image, "total_sectors", 0) or 0)
        if total_blocks <= 0 or (available_blocks and total_blocks > available_blocks):
            return False
        if bitmap_pointer <= 0 or bitmap_pointer >= total_blocks:
            return False
        self.volume_name = name.decode("ascii", errors="replace")
        self.entry_length = entry_length
        self.entries_per_block = entries_per_block
        self.file_count = int.from_bytes(directory[0x25:0x27], "little")
        self.bitmap_pointer = bitmap_pointer
        self.total_blocks = total_blocks
        return True

    def list_directory(self, path: str = "/") -> list[FileEntry]:
        entries = self._entries_for_path(path)
        return [
            FileEntry(
                name=entry.name,
                is_dir=entry.is_dir,
                size=entry.eof,
                cluster_start=entry.key_pointer,
                attributes=entry.access,
            )
            for entry in entries
        ]

    def extract_file(self, path: str) -> bytes:
        entry = self._entry_for_path(path)
        if entry.is_dir:
            raise FilesystemError(f"'{path}' is a directory")
        storage_type = entry.storage_type
        key_pointer = entry.key_pointer
        eof = entry.eof
        if storage_type == 0x05:
            storage_type, key_pointer, eof = self._extended_data_fork(entry)
        blocks = self._data_blocks(storage_type, key_pointer, eof)
        assert self.image is not None
        payload = b"".join(self.image.read_sector(block) for block in blocks)
        return payload[:eof]

    def file_sector_addresses(self, path: str) -> set[tuple[int, int, int]]:
        entry = self._entry_for_path(path)
        extended_block: int | None = None
        if entry.is_dir:
            blocks = self._directory_blocks(entry.key_pointer)
        else:
            storage_type = entry.storage_type
            key_pointer = entry.key_pointer
            eof = entry.eof
            if storage_type == 0x05:
                extended_block = key_pointer
                storage_type, key_pointer, eof = self._extended_data_fork(entry)
            blocks = self._all_file_blocks(storage_type, key_pointer, eof)
            if extended_block is not None:
                blocks.add(extended_block)
        addresses: set[tuple[int, int, int]] = set()
        for block in blocks:
            addresses.update(Apple2SectorImage.block_sector_addresses(block))
        return addresses

    def metadata(self) -> dict[str, object]:
        return {
            "filesystem": "prodos",
            "volume_label": self.volume_name,
            "total_blocks": self.total_blocks,
            "bitmap_block": self.bitmap_pointer,
            "file_count": self.file_count,
            "block_size": 512,
        }

    def _entries_for_path(self, path: str) -> list[ProDOSEntry]:
        block = 2
        parts = [part for part in path.strip("/").split("/") if part]
        for part in parts:
            match = next((entry for entry in self._read_directory(block) if entry.name.casefold() == part.casefold()), None)
            if match is None:
                raise FilesystemError(f"Directory '{path}' was not found")
            if not match.is_dir:
                raise FilesystemError(f"'{part}' is not a directory")
            block = match.key_pointer
        return self._read_directory(block)

    def _entry_for_path(self, path: str) -> ProDOSEntry:
        parts = [part for part in path.strip("/").split("/") if part]
        if not parts:
            raise FilesystemError("Choose a ProDOS file or directory")
        parent = "/" + "/".join(parts[:-1]) if len(parts) > 1 else "/"
        match = next(
            (entry for entry in self._entries_for_path(parent) if entry.name.casefold() == parts[-1].casefold()),
            None,
        )
        if match is None:
            raise FilesystemError(f"ProDOS entry '{path}' was not found")
        return match

    def _read_directory(self, start_block: int) -> list[ProDOSEntry]:
        assert self.image is not None
        entries: list[ProDOSEntry] = []
        seen: set[int] = set()
        block = start_block
        first = True
        while block:
            if block in seen or block >= self.total_blocks:
                raise FilesystemError("ProDOS directory block chain is invalid")
            seen.add(block)
            data = self.image.read_sector(block)
            next_block = int.from_bytes(data[2:4], "little")
            for slot in range(self.entries_per_block):
                offset = 4 + slot * self.entry_length
                if offset + 39 > len(data):
                    break
                storage_type = data[offset] >> 4
                if storage_type == 0 or (first and slot == 0) or storage_type in {0x0E, 0x0F}:
                    continue
                name_length = data[offset] & 0x0F
                if not 1 <= name_length <= 15:
                    continue
                name = data[offset + 1 : offset + 1 + name_length].decode("ascii", errors="replace")
                entries.append(
                    ProDOSEntry(
                        name=name,
                        storage_type=storage_type,
                        file_type=data[offset + 0x10],
                        key_pointer=int.from_bytes(data[offset + 0x11 : offset + 0x13], "little"),
                        blocks_used=int.from_bytes(data[offset + 0x13 : offset + 0x15], "little"),
                        eof=int.from_bytes(data[offset + 0x15 : offset + 0x18], "little"),
                        access=data[offset + 0x1E],
                        aux_type=int.from_bytes(data[offset + 0x1F : offset + 0x21], "little"),
                        header_pointer=int.from_bytes(data[offset + 0x25 : offset + 0x27], "little"),
                    )
                )
            first = False
            block = next_block
        return sorted(entries, key=lambda entry: (not entry.is_dir, entry.name.casefold()))

    def _directory_blocks(self, start_block: int) -> list[int]:
        assert self.image is not None
        blocks: list[int] = []
        seen: set[int] = set()
        block = start_block
        while block and block not in seen and block < self.total_blocks:
            seen.add(block)
            blocks.append(block)
            block = int.from_bytes(self.image.read_sector(block)[2:4], "little")
        return blocks

    def _extended_data_fork(self, entry: ProDOSEntry) -> tuple[int, int, int]:
        assert self.image is not None
        extended = self.image.read_sector(entry.key_pointer)
        storage_type = extended[0]
        key_pointer = int.from_bytes(extended[1:3], "little")
        eof = int.from_bytes(extended[5:8], "little")
        if storage_type not in {1, 2, 3}:
            raise FilesystemError("Unsupported ProDOS extended data fork")
        return storage_type, key_pointer, eof

    def _data_blocks(self, storage_type: int, key_pointer: int, eof: int) -> list[int]:
        required = (eof + 511) // 512
        if required == 0:
            return []
        if storage_type == 1:
            return [key_pointer]
        if storage_type == 2:
            return [pointer for pointer in self._index_pointers(key_pointer)[:required] if pointer]
        if storage_type == 3:
            blocks: list[int] = []
            for index_block in self._index_pointers(key_pointer):
                if not index_block:
                    continue
                remaining = required - len(blocks)
                blocks.extend(
                    pointer for pointer in self._index_pointers(index_block)[:remaining] if pointer
                )
                if len(blocks) >= required:
                    break
            return blocks[:required]
        raise FilesystemError(f"Unsupported ProDOS storage type ${storage_type:X}")

    def _all_file_blocks(self, storage_type: int, key_pointer: int, eof: int) -> set[int]:
        blocks = set(self._data_blocks(storage_type, key_pointer, eof))
        if storage_type in {2, 3}:
            blocks.add(key_pointer)
        if storage_type == 3:
            blocks.update(pointer for pointer in self._index_pointers(key_pointer) if pointer)
        return blocks

    def _index_pointers(self, block: int) -> list[int]:
        assert self.image is not None
        data = self.image.read_sector(block)
        pointers: list[int] = []
        for index in range(256):
            pointer = data[index] | (data[index + 256] << 8)
            pointers.append(pointer)
        return pointers


__all__ = ["ProDOSFilesystem"]
