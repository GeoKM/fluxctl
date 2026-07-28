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
                block = self._long(data, 126)
        return sorted(entries, key=lambda entry: (not entry.is_dir, entry.name.lower()))

    def _parse_real_directory(self) -> None:
        self.directory = self._directory_entries_from_block(880)

    def probe(self, image: SectorImage) -> bool:
        self._reset()
        self.image = image
        try:
            boot = image.read_sector(0)
        except FilesystemError:
            return False
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
        parts = [part for part in path.strip("/").split("/") if part]
        if not parts:
            raise FilesystemError("Path must reference a file")
        entries = self._entries_for_path("/" + "/".join(parts[:-1]) if len(parts) > 1 else "/")
        target = next((e for e in entries if e.name.lower() == parts[-1].lower()), None)
        if target is None:
            raise FilesystemError(f"File '{path}' not found")
        if target.is_dir:
            raise FilesystemError("Cannot extract a directory entry")
        start = target.start_sector
        count = (target.length + self.image.bytes_per_sector - 1) // self.image.bytes_per_sector
        data = self.image.read_sector(start, count)
        return data[: target.length]

    def metadata(self) -> Dict[str, str]:
        return {"filesystem": self.filesystem, "entries": str(len(self.directory))}
