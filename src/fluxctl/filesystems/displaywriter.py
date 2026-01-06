"""IBM Displaywriter filesystem probe (layout-level only; no listing/extract)."""
from __future__ import annotations

from typing import Any, Dict, List

from ..exceptions import FilesystemError
from . import FileEntry, Filesystem, SectorImage


class DisplaywriterFS(Filesystem):
    """Detect Displaywriter mixed-sector FM 8-inch format.

    Characteristics (per preservation reports):
    - Single-sided FM
    - Track 0: 26 sectors, 128 bytes
    - Tracks 1-76: 15 sectors, 256 bytes
    Directory format is proprietary; listing/extract not implemented.
    """

    def __init__(self) -> None:
        self._probed = False

    def probe(self, image: SectorImage) -> bool:
        try:
            sectors: List[bytes] = []
            for idx, data in enumerate(image.iter_sectors()):
                sectors.append(data)
                if len(sectors) >= 26 + 15:  # enough for first two tracks if flattened T/S order
                    break
        except Exception:
            return False
        if not sectors:
            return False
        # Infer sector size pattern: first 26 sectors ~128 bytes, next 15 ~256 bytes.
        first = sectors[:26]
        rest = sectors[26:26 + 15]
        if len(first) < 26 or len(rest) < 10:
            return False
        if not all(112 <= len(s) <= 144 for s in first):
            return False
        if not all(240 <= len(s) <= 280 for s in rest):
            return False
        self._probed = True
        return True

    def list_directory(self, path: str = "/") -> List[FileEntry]:
        raise FilesystemError("Displaywriter directory listing not implemented")

    def extract_file(self, path: str) -> bytes:
        raise FilesystemError("Displaywriter file extraction not implemented")

    def metadata(self) -> Dict[str, Any]:
        return {"filesystem": "displaywriter", "probed": self._probed}
