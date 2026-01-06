"""RT-11 filesystem probe and minimal metadata reader."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..exceptions import FilesystemError
from . import FileEntry, Filesystem, SectorImage


RAD50_TABLE = " ABCDEFGHIJKLMNOPQRSTUVWXYZ$.%0123456789"


def _rad50_word_to_chars(word: int) -> str:
    chars = []
    for div in (40 * 40, 40, 1):
        idx = word // div
        word = word % div
        if idx >= len(RAD50_TABLE):
            return ""
        chars.append(RAD50_TABLE[idx])
    return "".join(chars)


def _decode_rad50_name(words: List[int]) -> Optional[str]:
    if len(words) < 3:
        return None
    try:
        name = "".join(_rad50_word_to_chars(w) for w in words[:2]).strip()
        ext = _rad50_word_to_chars(words[2]).strip()
        return f"{name}.{ext}" if ext else name
    except Exception:
        return None


class RT11Filesystem(Filesystem):
    """Lightweight probe for RT-11 (RX02) volumes.

    Directory starts at block 6; entries are 16-byte (8-word) structures:
    [flags][name(2)][ext(1)][start][length][date]...  We only validate flags,
    RAD50 name, and length to classify the volume.
    """

    def __init__(self) -> None:
        self.is_rt11 = False
        self.label: Optional[str] = None

    def probe(self, image: SectorImage) -> bool:
        # Reblock to 512-byte units (RT-11 logical blocks)
        blocks: List[bytes] = []
        try:
            for data in image.iter_sectors():
                blocks.append(data)
        except Exception:
            return False
        if not blocks:
            return False

        # RX02 uses 256-byte sectors; allow either 256 or 512 depending on decoder.
        sector_size = len(blocks[0])
        if sector_size not in (256, 512):
            return False

        # Normalize to 512-byte blocks
        if sector_size == 256:
            if len(blocks) % 2:
                return False
            new_blocks = []
            for i in range(0, len(blocks), 2):
                new_blocks.append(blocks[i] + blocks[i + 1])
            blocks = new_blocks

        # Need at least directory block
        if len(blocks) <= 6:
            return False

        # Home block (block 1) often holds label in words 2-3
        home = blocks[1]
        try:
            home_words = list(int.from_bytes(home[i : i + 2], "little") for i in range(0, len(home), 2))
        except Exception:
            home_words = []
        if len(home_words) >= 4:
            lbl = _decode_rad50_name(home_words[2:5])
            if lbl:
                self.label = lbl.strip(".")

        entries_ok = 0
        empties = 0
        total = 0
        for dir_block in blocks[6 : min(len(blocks), 22)]:  # scan first ~16 directory blocks
            for offset in range(0, len(dir_block), 16):
                entry = dir_block[offset : offset + 16]
                if len(entry) < 16:
                    continue
                flag = int.from_bytes(entry[0:2], "little")
                if flag in (0x0000, 0xFFFF):
                    empties += 1
                    total += 1
                    continue
                if flag not in (1, 2, 3, 4):
                    total += 1
                    continue
                name_words = [
                    int.from_bytes(entry[2:4], "little"),
                    int.from_bytes(entry[4:6], "little"),
                    int.from_bytes(entry[6:8], "little"),
                ]
                name = _decode_rad50_name(name_words)
                length = int.from_bytes(entry[12:14], "little")
                if not name or length <= 0 or length > 0x7FFF:
                    total += 1
                    continue
                entries_ok += 1
                total += 1

        self.is_rt11 = entries_ok >= 2 and (entries_ok + empties) >= 4
        return self.is_rt11

    def list_directory(self, path: str = "/") -> List[FileEntry]:
        raise FilesystemError("RT-11 directory listing not implemented")

    def extract_file(self, path: str) -> bytes:
        raise FilesystemError("RT-11 extraction not implemented")

    def metadata(self) -> Dict[str, Any]:
        return {"filesystem": "rt11", "label": self.label or ""}
