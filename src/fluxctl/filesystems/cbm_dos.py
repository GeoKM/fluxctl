"""Minimal CBM DOS 2.x filesystem reader."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from ..exceptions import FilesystemError
from ..exporters.d64 import DEFAULT_SECTORS_PER_TRACK, SECTOR_SIZE
from . import FileEntry, Filesystem, SectorImage, TrackSectorImage

DIRECTORY_TRACK = 18
DIRECTORY_START_SECTOR = 1
BAM_SECTOR = (18, 0)


@dataclass(slots=True)
class DirectoryRecord:
    name: str
    start_track: int
    start_sector: int
    file_type: int
    blocks: int

    @property
    def is_dir(self) -> bool:
        return False


class CBMDOS(Filesystem):
    """Reader for Commodore CBM DOS disks (1541/1571)."""

    def __init__(self) -> None:
        self.image: Optional[SectorImage] = None
        self.sectors_per_track: List[int] = list(DEFAULT_SECTORS_PER_TRACK)
        self.directory: List[DirectoryRecord] = []
        self.dos_type: bytes = b""

    def _reset(self) -> None:
        self.__init__()

    def _sectors_for_track(self, track_index: int) -> int:
        if track_index < 0:
            return 0
        if track_index < len(self.sectors_per_track):
            return self.sectors_per_track[track_index]
        return self.sectors_per_track[-1]

    def _ts_to_lba(self, track: int, sector: int) -> int:
        track_index = track - 1
        if sector < 0 or sector >= self._sectors_for_track(track_index):
            raise FilesystemError("Invalid track/sector reference")
        return sum(self.sectors_per_track[:track_index]) + sector

    def _read_ts(self, track: int, sector: int) -> bytes:
        if self.image is None:
            raise FilesystemError("Filesystem not initialised")
        if isinstance(self.image, TrackSectorImage):
            lookup = getattr(self.image, "_sector_lookup", {})
            key = (track - 1, 0, sector)
            if key in lookup:
                return lookup[key]
        lba = self._ts_to_lba(track, sector)
        return self.image.read_sector(lba, 1)

    def _iter_directory_sectors(self) -> List[bytes]:
        sectors: List[bytes] = []
        seen: set[Tuple[int, int]] = set()
        track, sector = DIRECTORY_TRACK, DIRECTORY_START_SECTOR
        while track != 0 and (track, sector) not in seen:
            seen.add((track, sector))
            data = self._read_ts(track, sector)
            if len(data) < SECTOR_SIZE:
                break
            sectors.append(data)
            track = data[0]
            sector = data[1]
        return sectors

    def _parse_directory(self) -> None:
        self.directory = []
        for sector in self._iter_directory_sectors():
            for idx in range(8):
                entry = sector[2 + idx * 32 : 2 + (idx + 1) * 32]
                file_type = entry[0]
                if file_type == 0:
                    continue
                start_track = entry[1]
                start_sector = entry[2]
                if start_track == 0:
                    continue
                name_bytes = entry[3:19]
                name = name_bytes.replace(b"\xA0", b" ").rstrip(b" \x00").decode("latin-1")
                blocks = int.from_bytes(entry[30:32], "little")
                self.directory.append(
                    DirectoryRecord(
                        name=name,
                        start_track=start_track,
                        start_sector=start_sector,
                        file_type=file_type,
                        blocks=blocks,
                    )
                )

    def probe(self, image: SectorImage) -> bool:
        self._reset()
        self.image = image
        layout = getattr(image, "layout", None)
        if layout and getattr(layout, "track_sectors", None):
            self.sectors_per_track = list(layout.track_sectors)
        bam = None
        try:
            bam = self._read_ts(*BAM_SECTOR)
        except FilesystemError:
            return False
        if bam is None or len(bam) < SECTOR_SIZE:
            return False
        self.dos_type = bam[0xA2:0xA4]
        if not self.dos_type or not all(chr(b).isalnum() or chr(b) == " " for b in self.dos_type):
            return False
        try:
            self._parse_directory()
        except FilesystemError:
            return False
        return bool(self.directory)

    def _read_chain(self, start_track: int, start_sector: int) -> bytes:
        chunks: List[bytes] = []
        seen: set[Tuple[int, int]] = set()
        track, sector = start_track, start_sector
        while track != 0 and (track, sector) not in seen:
            seen.add((track, sector))
            data = self._read_ts(track, sector)
            if len(data) < 2:
                break
            next_track, next_sector = data[0], data[1]
            if next_track == 0:
                used = min(next_sector, len(data) - 2)
                chunks.append(data[2 : 2 + used])
                break
            chunks.append(data[2:])
            track, sector = next_track, next_sector
        return b"".join(chunks)

    def list_directory(self, path: str = "/") -> List[FileEntry]:
        if path not in {"/", ""}:
            raise FilesystemError("CBM DOS reader only supports root directory")
        entries: List[FileEntry] = []
        for record in self.directory:
            content = self._read_chain(record.start_track, record.start_sector)
            size = len(content)
            entries.append(
                FileEntry(
                    name=record.name,
                    is_dir=record.is_dir,
                    size=size,
                    cluster_start=(record.start_track << 8) | record.start_sector,
                )
            )
        return entries

    def extract_file(self, path: str) -> bytes:
        target = path.lstrip("/").upper()
        for record in self.directory:
            if record.name.upper() == target:
                return self._read_chain(record.start_track, record.start_sector)
        raise FilesystemError(f"File not found: {path}")

    def metadata(self) -> Dict[str, str]:
        return {"dos_type": self.dos_type.decode("latin-1", errors="ignore")}
