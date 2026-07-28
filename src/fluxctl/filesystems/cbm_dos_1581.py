"""Commodore 1581 CBM DOS reader."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from ..exceptions import FilesystemError
from . import FileEntry, Filesystem, SectorImage, TrackSectorImage


DIRECTORY_TRACK = 40
DIRECTORY_HEADER_SECTOR = 0
DIRECTORY_START_SECTOR = 3
LOGICAL_SECTORS_PER_TRACK = 40
LOGICAL_SECTOR_SIZE = 256


@dataclass(slots=True)
class DirectoryRecord1581:
    name: str
    start_track: int
    start_sector: int
    file_type: int
    blocks: int

    @property
    def is_dir(self) -> bool:
        # 1581 partitions/subdirectories appear as CBM file type.
        return (self.file_type & 0x07) == 5


class CBMDOS1581(Filesystem):
    """Reader for 1581 CBM DOS 10 logical sectors."""

    def __init__(self) -> None:
        self.image: Optional[SectorImage] = None
        self.directory: List[DirectoryRecord1581] = []
        self.dos_type = ""
        self._logical_head_order = (0, 1)

    def _reset(self) -> None:
        self.__init__()

    def _physical_sector_halves(self, track: int, head: int, sector_id: int) -> tuple[bytes, bytes]:
        if self.image is None:
            raise FilesystemError("Filesystem not initialised")
        if isinstance(self.image, TrackSectorImage):
            data = self.image._sector_lookup.get((track - 1, head, sector_id))
            if data is None:
                raise FilesystemError("1581 physical sector not available")
        else:
            physical_lba = ((track - 1) * 2 + head) * 10 + (sector_id - 1)
            data = self.image.read_sector(physical_lba, 1)
        if len(data) < 512:
            raise FilesystemError("1581 physical sector is shorter than 512 bytes")
        return data[:LOGICAL_SECTOR_SIZE], data[LOGICAL_SECTOR_SIZE:512]

    def _read_logical_sector(self, track: int, sector: int) -> bytes:
        if track < 1 or track > 80 or sector < 0 or sector >= LOGICAL_SECTORS_PER_TRACK:
            raise FilesystemError("Invalid 1581 track/sector reference")
        half_index = sector % 2
        physical_sector = (sector % 20) // 2 + 1
        physical_head = self._logical_head_order[sector // 20]
        halves = self._physical_sector_halves(track, physical_head, physical_sector)
        return halves[half_index]

    def _detect_head_order(self) -> bool:
        candidates = [
            ((0, 1), self._physical_sector_halves(DIRECTORY_TRACK, 0, 1)[0]),
            ((1, 0), self._physical_sector_halves(DIRECTORY_TRACK, 1, 1)[0]),
        ]
        for order, header in candidates:
            if (
                len(header) >= 27
                and header[0] == DIRECTORY_TRACK
                and header[1] == DIRECTORY_START_SECTOR
                and header[2] == ord("D")
                and header[25:27] == b"3D"
            ):
                self._logical_head_order = order
                self.dos_type = "3D"
                return True
        return False

    def _iter_directory_sectors(self, start_track: int, start_sector: int) -> List[bytes]:
        sectors: List[bytes] = []
        seen: set[tuple[int, int]] = set()
        track, sector = start_track, start_sector
        while track != 0 and (track, sector) not in seen:
            seen.add((track, sector))
            data = self._read_logical_sector(track, sector)
            sectors.append(data)
            if len(data) < 2:
                break
            track, sector = data[0], data[1]
        return sectors

    def _parse_directory_from(self, start_track: int, start_sector: int) -> List[DirectoryRecord1581]:
        records: List[DirectoryRecord1581] = []
        for sector in self._iter_directory_sectors(start_track, start_sector):
            for idx in range(8):
                entry = sector[2 + idx * 32 : 2 + (idx + 1) * 32]
                if len(entry) < 32:
                    continue
                file_type = entry[0]
                if file_type == 0:
                    continue
                start_track = entry[1]
                start_sector = entry[2]
                if start_track == 0:
                    continue
                name = entry[3:19].replace(b"\xA0", b" ").rstrip(b" \x00").decode("latin-1")
                blocks = int.from_bytes(entry[28:30], "little")
                records.append(DirectoryRecord1581(name, start_track, start_sector, file_type, blocks))
        return records

    def _parse_directory(self) -> None:
        self.directory = self._parse_directory_from(DIRECTORY_TRACK, DIRECTORY_START_SECTOR)

    def probe(self, image: SectorImage) -> bool:
        self._reset()
        self.image = image
        layout_id = getattr(getattr(image, "layout", None), "layout_id", "")
        if layout_id and layout_id != "commodore_mfm_1581_800k":
            return False
        try:
            if not self._detect_head_order():
                return False
            self._parse_directory()
        except FilesystemError:
            return False
        return True

    def list_directory(self, path: str = "/") -> List[FileEntry]:
        records = self._records_for_path(path)
        return [
            FileEntry(
                name=record.name,
                is_dir=record.is_dir,
                size=record.blocks * LOGICAL_SECTOR_SIZE,
                cluster_start=(record.start_track << 8) | record.start_sector,
                attributes=record.file_type,
            )
            for record in records
        ]

    def _records_for_path(self, path: str) -> List[DirectoryRecord1581]:
        parts = [part for part in path.strip("/").split("/") if part]
        records = self.directory
        if not parts:
            return records
        for part in parts:
            match = next((record for record in records if record.name.lower() == part.lower()), None)
            if match is None:
                raise FilesystemError(f"Directory '{path}' not found")
            if not match.is_dir:
                raise FilesystemError(f"'{part}' is not a directory")
            records = self._parse_directory_from(match.start_track, match.start_sector)
        return records

    def extract_file(self, path: str) -> bytes:
        raise FilesystemError("1581 file extraction not implemented")

    def metadata(self) -> Dict[str, str]:
        return {"filesystem": "cbm_dos_1581", "dos_type": self.dos_type}
