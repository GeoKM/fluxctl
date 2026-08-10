"""Read-only Apple DOS 3.3 catalog and file extraction support."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..apple2 import APPLE2_SECTORS, APPLE2_TRACKS, Apple2SectorImage
from ..exceptions import FilesystemError
from . import FileEntry, SectorImage


@dataclass(frozen=True)
class AppleDOSFile:
    name: str
    file_type: int
    ts_track: int
    ts_sector: int
    sector_count: int


class AppleDOS33Filesystem:
    """Apple DOS 3.3 VTOC/catalog reader for 16-sector disks."""

    def __init__(self) -> None:
        self.image: Optional[Apple2SectorImage] = None
        self.volume_number = 0
        self.catalog_track = 0
        self.catalog_sector = 0

    def probe(self, image: SectorImage) -> bool:
        self.image = image if isinstance(image, Apple2SectorImage) else None
        if self.image is None:
            return False
        try:
            vtoc = self.image.read_physical_sector(17, 0)
        except Exception:
            return False
        catalog_track, catalog_sector = vtoc[1], vtoc[2]
        tracks, sectors = vtoc[0x34], vtoc[0x35]
        bytes_per_sector = int.from_bytes(vtoc[0x36:0x38], "little")
        if not (1 <= catalog_track < APPLE2_TRACKS and catalog_sector < APPLE2_SECTORS):
            return False
        if tracks not in {0, APPLE2_TRACKS} or sectors != APPLE2_SECTORS or bytes_per_sector != 256:
            return False
        self.volume_number = vtoc[6]
        self.catalog_track = catalog_track
        self.catalog_sector = catalog_sector
        try:
            self._catalog_entries()
        except FilesystemError:
            return False
        return True

    def list_directory(self, path: str = "/") -> list[FileEntry]:
        if path not in {"", "/"}:
            raise FilesystemError("Apple DOS 3.3 has a flat root directory")
        result: list[FileEntry] = []
        for entry in self._catalog_entries():
            result.append(
                FileEntry(
                    name=entry.name,
                    is_dir=False,
                    size=len(self._file_data_sectors(entry)) * 256,
                    cluster_start=(entry.ts_track << 8) | entry.ts_sector,
                    attributes=entry.file_type,
                )
            )
        return result

    def extract_file(self, path: str) -> bytes:
        entry = self._entry(path)
        assert self.image is not None
        payload = bytearray()
        for track, sector in self._file_data_sectors(entry):
            payload.extend(self.image.read_physical_sector(track, sector))
        return bytes(payload)

    def file_sector_addresses(self, path: str) -> set[tuple[int, int, int]]:
        entry = self._entry(path)
        addresses = {(track, 0, sector) for track, sector in self._file_data_sectors(entry)}
        addresses.update((track, 0, sector) for track, sector in self._ts_list_sectors(entry))
        return addresses

    def metadata(self) -> dict[str, object]:
        return {
            "filesystem": "apple_dos_3_3",
            "volume_number": self.volume_number,
            "catalog_track": self.catalog_track,
            "catalog_sector": self.catalog_sector,
            "sector_size": 256,
        }

    def _entry(self, path: str) -> AppleDOSFile:
        name = path.strip("/")
        match = next((entry for entry in self._catalog_entries() if entry.name.casefold() == name.casefold()), None)
        if match is None:
            raise FilesystemError(f"Apple DOS file '{path}' was not found")
        return match

    def _catalog_entries(self) -> list[AppleDOSFile]:
        assert self.image is not None
        entries: list[AppleDOSFile] = []
        seen: set[tuple[int, int]] = set()
        track, sector = self.catalog_track, self.catalog_sector
        while track:
            address = (track, sector)
            if address in seen or track >= APPLE2_TRACKS or sector >= APPLE2_SECTORS:
                raise FilesystemError("Apple DOS catalog chain is invalid")
            seen.add(address)
            data = self.image.read_physical_sector(track, sector)
            for slot in range(7):
                offset = 0x0B + slot * 35
                ts_track, ts_sector = data[offset], data[offset + 1]
                if ts_track in {0, 0xFF} or ts_track >= APPLE2_TRACKS or ts_sector >= APPLE2_SECTORS:
                    continue
                raw_name = bytes(byte & 0x7F for byte in data[offset + 3 : offset + 33])
                name = raw_name.decode("ascii", errors="replace").rstrip(" \x00")
                if not name:
                    continue
                entries.append(
                    AppleDOSFile(
                        name=name,
                        file_type=data[offset + 2],
                        ts_track=ts_track,
                        ts_sector=ts_sector,
                        sector_count=int.from_bytes(data[offset + 33 : offset + 35], "little"),
                    )
                )
            track, sector = data[1], data[2]
        return sorted(entries, key=lambda entry: entry.name.casefold())

    def _ts_list_sectors(self, entry: AppleDOSFile) -> list[tuple[int, int]]:
        assert self.image is not None
        result: list[tuple[int, int]] = []
        seen: set[tuple[int, int]] = set()
        track, sector = entry.ts_track, entry.ts_sector
        while track:
            address = (track, sector)
            if address in seen or track >= APPLE2_TRACKS or sector >= APPLE2_SECTORS:
                raise FilesystemError(f"Apple DOS T/S list for '{entry.name}' is invalid")
            seen.add(address)
            result.append(address)
            data = self.image.read_physical_sector(track, sector)
            track, sector = data[1], data[2]
        return result

    def _file_data_sectors(self, entry: AppleDOSFile) -> list[tuple[int, int]]:
        assert self.image is not None
        result: list[tuple[int, int]] = []
        for track, sector in self._ts_list_sectors(entry):
            data = self.image.read_physical_sector(track, sector)
            for offset in range(0x0C, 0x100, 2):
                data_track, data_sector = data[offset], data[offset + 1]
                if data_track == 0:
                    continue
                if data_track >= APPLE2_TRACKS or data_sector >= APPLE2_SECTORS:
                    raise FilesystemError(f"Apple DOS file '{entry.name}' has an invalid sector address")
                result.append((data_track, data_sector))
        return result


__all__ = ["AppleDOS33Filesystem"]
