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


class AmigaOFS(Filesystem):
    """Simplified Amiga filesystem implementation for fixtures.

    The implementation is intentionally conservative and focuses on recognising
    OFS-style disk layouts. Directory entries are encoded in sector 0 as simple
    ``name:start:length`` lines to keep the test fixtures small while still
    exercising the plugin plumbing.
    """

    def __init__(self) -> None:
        self.image: Optional[SectorImage] = None
        self.directory: list[_AmigaDirEntry] = []

    def _parse_directory(self) -> None:
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

    def probe(self, image: SectorImage) -> bool:
        self.image = image
        try:
            marker = image.read_sector(880)
        except FilesystemError:
            return False
        if not marker.startswith(b"DOS"):
            return False
        self.directory = []
        self._parse_directory()
        return True

    def list_directory(self, path: str = "/") -> List[FileEntry]:
        if self.image is None:
            raise FilesystemError("Filesystem not probed")
        entries: List[FileEntry] = []
        for entry in self.directory:
            entries.append(FileEntry(name=entry.name, is_dir=False, size=entry.length, cluster_start=entry.start_sector))
        return entries

    def extract_file(self, path: str) -> bytes:
        if self.image is None:
            raise FilesystemError("Filesystem not probed")
        target = next((e for e in self.directory if e.name == path or f"/{e.name}" == path), None)
        if target is None:
            raise FilesystemError(f"File '{path}' not found")
        start = target.start_sector
        count = (target.length + self.image.bytes_per_sector - 1) // self.image.bytes_per_sector
        data = self.image.read_sector(start, count)
        return data[: target.length]

    def metadata(self) -> Dict[str, str]:
        return {"filesystem": "amiga_ofs", "entries": len(self.directory)}
