"""Minimal CP/M filesystem probe."""
from __future__ import annotations

from typing import Any, Dict, List

from ..exceptions import FilesystemError
from . import FileEntry, Filesystem, SectorImage


_ALLOWED_CHARS = set(b"ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 $%'-_@~`!#()^")


def _looks_like_cpm_entry(entry: bytes) -> bool:
    if len(entry) < 32:
        return False
    user = entry[0]
    # CP/M 3 may set high bits for attributes; tolerate up to 0x7F.
    if user >= 0x80:
        return False
    name = entry[1:12]
    if not name:
        return False
    if all(ch == 0x20 for ch in name):
        return False
    if any((ch not in _ALLOWED_CHARS) for ch in name):
        return False
    return True


class CPMFilesystem(Filesystem):
    """Lightweight CP/M probe based on directory entry structure."""

    def __init__(self) -> None:
        self._probed = False

    def probe(self, image: SectorImage) -> bool:
        """Heuristic CP/M probe: scan early sectors for directory entries."""

        if getattr(image, "bytes_per_sector", 0) and image.bytes_per_sector > 256:
            return False

        entries_checked = 0
        matches = 0
        empty = 0

        try:
            sector_iter = image.iter_sectors()
        except Exception:
            # Fallback to LBA reads if iter_sectors not available.
            def sector_iter():
                idx = 0
                while True:
                    yield image.read_sector(idx)
                    idx += 1

        for idx, data in enumerate(sector_iter):
            if idx >= 2048:  # generous cap to cover skewed directories
                break
            for offset in range(0, len(data), 32):
                entry = data[offset : offset + 32]
                if len(entry) < 32:
                    continue
                if entry[0] in (0x00, 0xE5):
                    empty += 1
                    continue
                entries_checked += 1
                if _looks_like_cpm_entry(entry):
                    matches += 1
            if entries_checked >= 64 and (matches >= 2 or empty >= 16):
                break

        self._probed = matches >= 2 or (matches >= 1 and empty >= 8)
        return self._probed

    def list_directory(self, path: str = "/") -> List[FileEntry]:
        raise FilesystemError("CP/M directory listing not implemented")

    def extract_file(self, path: str) -> bytes:
        raise FilesystemError("CP/M file extraction not implemented")

    def metadata(self) -> Dict[str, Any]:
        return {"filesystem": "cpm", "probed": self._probed}
