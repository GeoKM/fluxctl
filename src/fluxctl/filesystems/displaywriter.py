"""IBM Displaywriter standard-label directory reader."""
from __future__ import annotations

from typing import Any, Dict, List

from ..exceptions import FilesystemError
from . import FileEntry, Filesystem, SectorImage, TrackSectorImage


class DisplaywriterFS(Filesystem):
    """Detect Displaywriter mixed-sector FM 8-inch format.

    Characteristics (per preservation reports):
    - Single-sided FM
    - Track 0: 26 sectors, 128 bytes
    - Tracks 1-76: 15 sectors, 256 bytes
    Track 0 carries IBM standard labels in EBCDIC. This reader lists HDR1 label
    records but does not yet extract document data.
    """

    def __init__(self) -> None:
        self._probed = False
        self.image: SectorImage | None = None
        self._entries: List[FileEntry] = []

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
        self.image = image
        self._entries = self._read_label_entries(image)
        self._probed = True
        return True

    def list_directory(self, path: str = "/") -> List[FileEntry]:
        if path not in {"", "/"}:
            raise FilesystemError("Displaywriter subdirectories are not supported")
        return list(self._entries)

    def extract_file(self, path: str) -> bytes:
        raise FilesystemError("Displaywriter file extraction not implemented")

    def metadata(self) -> Dict[str, Any]:
        return {"filesystem": "displaywriter", "probed": self._probed, "entries": len(self._entries)}

    def _read_label_entries(self, image: SectorImage) -> List[FileEntry]:
        entries: List[FileEntry] = []
        for sector_id in range(8, 27):
            data = self._read_track0_sector(image, sector_id)
            if data is None or len(data) < 80:
                continue
            text = data[:128].decode("cp037", errors="replace")
            if text.startswith("HDR1"):
                name = text[5:22].strip()
                block_len_text = text[22:27].strip()
                try:
                    block_len = int(block_len_text)
                except ValueError:
                    block_len = 0
                entries.append(
                    FileEntry(
                        name=name or f"LABEL{sector_id:02d}",
                        is_dir=False,
                        size=0,
                        cluster_start=sector_id,
                        attributes=block_len,
                    )
                )
        return entries

    def _read_track0_sector(self, image: SectorImage, sector_id: int) -> bytes | None:
        if isinstance(image, TrackSectorImage):
            for track in image.tracks:
                if track.track != 0 or track.head != 0:
                    continue
                for sector in track.sectors:
                    if sector.sector_id == sector_id:
                        return sector.data
            return None
        try:
            # DisplayWriter label records begin at physical track 0 sector 8.
            return image.read_sector(sector_id - 1)
        except Exception:
            return None
