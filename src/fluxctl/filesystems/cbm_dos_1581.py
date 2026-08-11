"""Commodore 1581 CBM DOS reader."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from ..exceptions import FilesystemError
from .cbm_dos import CBM_FILE_TYPE_CODES
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


@dataclass(slots=True)
class _DirectorySlot1581:
    offset: int
    record: DirectoryRecord1581


class CBMDOS1581(Filesystem):
    """Reader for 1581 CBM DOS 10 logical sectors."""

    def __init__(self) -> None:
        self.image: Optional[SectorImage] = None
        self.directory: List[DirectoryRecord1581] = []
        self.dos_type = ""
        self.disk_name = ""
        self.disk_id = ""
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
        if self.image is not None and getattr(self.image, "bytes_per_sector", 0) == LOGICAL_SECTOR_SIZE:
            lba = (track - 1) * LOGICAL_SECTORS_PER_TRACK + sector
            return self.image.read_sector(lba, 1)
        half_index = sector % 2
        physical_sector = (sector % 20) // 2 + 1
        physical_head = self._logical_head_order[sector // 20]
        halves = self._physical_sector_halves(track, physical_head, physical_sector)
        return halves[half_index]

    def _logical_offset(self, track: int, sector: int) -> int:
        if track < 1 or track > 80 or sector < 0 or sector >= LOGICAL_SECTORS_PER_TRACK:
            raise FilesystemError("Invalid 1581 track/sector reference")
        return ((track - 1) * LOGICAL_SECTORS_PER_TRACK + sector) * LOGICAL_SECTOR_SIZE

    def _write_logical_sector(self, image_bytes: bytearray, track: int, sector: int, data: bytes) -> None:
        offset = self._logical_offset(track, sector)
        if offset + LOGICAL_SECTOR_SIZE > len(image_bytes):
            raise FilesystemError("1581 logical sector exceeds image size")
        image_bytes[offset : offset + LOGICAL_SECTOR_SIZE] = data[:LOGICAL_SECTOR_SIZE].ljust(
            LOGICAL_SECTOR_SIZE, b"\x00"
        )

    def _detect_head_order(self) -> bool:
        if self.image is not None and getattr(self.image, "bytes_per_sector", 0) == LOGICAL_SECTOR_SIZE:
            header = self._read_logical_sector(DIRECTORY_TRACK, DIRECTORY_HEADER_SECTOR)
            if (
                len(header) >= 27
                and header[0] == DIRECTORY_TRACK
                and header[1] == DIRECTORY_START_SECTOR
                and header[2] == ord("D")
                and header[25:27] == b"3D"
            ):
                self._parse_header_metadata(header)
                return True
            return False
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
                self._parse_header_metadata(header)
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
        entries: List[FileEntry] = []
        for record in records:
            if record.is_dir:
                size = record.blocks * LOGICAL_SECTOR_SIZE
            elif record.blocks:
                try:
                    size = len(self._read_chain(record.start_track, record.start_sector))
                except FilesystemError:
                    size = record.blocks * LOGICAL_SECTOR_SIZE
            else:
                size = 0
            entries.append(
                FileEntry(
                    name=record.name,
                    is_dir=record.is_dir,
                    size=size,
                    cluster_start=(record.start_track << 8) | record.start_sector,
                    attributes=record.file_type,
                )
            )
        return entries

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

    def _directory_start_for_path(self, path: str) -> tuple[int, int]:
        parts = [part for part in path.strip("/").split("/") if part]
        if not parts:
            return DIRECTORY_TRACK, DIRECTORY_START_SECTOR
        records = self.directory
        current = (DIRECTORY_TRACK, DIRECTORY_START_SECTOR)
        for part in parts:
            match = next((record for record in records if record.name.lower() == part.lower()), None)
            if match is None:
                raise FilesystemError(f"Directory '{path}' not found")
            if not match.is_dir:
                raise FilesystemError(f"'{part}' is not a directory")
            current = (match.start_track, match.start_sector)
            records = self._parse_directory_from(match.start_track, match.start_sector)
        return current

    def _record_for_file_path(self, path: str) -> DirectoryRecord1581:
        parts = [part for part in path.strip("/").split("/") if part]
        if not parts:
            raise FilesystemError("Path must reference a file")
        records = self._records_for_path("/" + "/".join(parts[:-1]) if len(parts) > 1 else "/")
        target = parts[-1].lower()
        for record in records:
            if record.name.lower() == target:
                if record.is_dir:
                    raise FilesystemError("Cannot extract a directory entry")
                return record
        raise FilesystemError(f"File not found: {path}")

    def _root_directory_slot_for_path(self, path: str) -> _DirectorySlot1581:
        parts = [part for part in path.strip("/").split("/") if part]
        if not parts:
            raise FilesystemError("Path must reference a file")
        if len(parts) > 1:
            raise FilesystemError("1581 mutation currently supports root directory entries only")
        target = parts[0].lower()
        track, sector = DIRECTORY_TRACK, DIRECTORY_START_SECTOR
        seen: set[tuple[int, int]] = set()
        while track != 0 and (track, sector) not in seen:
            seen.add((track, sector))
            data = self._read_logical_sector(track, sector)
            sector_offset = self._logical_offset(track, sector)
            for idx in range(8):
                entry_offset = sector_offset + 2 + idx * 32
                entry = data[2 + idx * 32 : 2 + (idx + 1) * 32]
                if len(entry) < 32 or entry[0] in {0x00, 0xE5}:
                    continue
                start_track = entry[1]
                start_sector = entry[2]
                if start_track == 0:
                    continue
                name = entry[3:19].replace(b"\xA0", b" ").rstrip(b" \x00").decode("latin-1")
                if name.lower() == target:
                    return _DirectorySlot1581(
                        entry_offset,
                        DirectoryRecord1581(
                            name=name,
                            start_track=start_track,
                            start_sector=start_sector,
                            file_type=entry[0],
                            blocks=int.from_bytes(entry[28:30], "little"),
                        ),
                    )
            track, sector = data[0], data[1]
        raise FilesystemError(f"File not found: {path}")

    def _read_chain(self, start_track: int, start_sector: int) -> bytes:
        chunks: List[bytes] = []
        for _track, _sector, data in self._iter_chain_blocks(start_track, start_sector):
            if len(data) < 2:
                break
            next_track, next_sector = data[0], data[1]
            if next_track == 0:
                used = min(next_sector, len(data) - 2)
                chunks.append(data[2 : 2 + used])
                break
            chunks.append(data[2:])
        return b"".join(chunks)

    def _iter_chain_blocks(self, start_track: int, start_sector: int) -> List[tuple[int, int, bytes]]:
        blocks: List[tuple[int, int, bytes]] = []
        seen: set[tuple[int, int]] = set()
        track, sector = start_track, start_sector
        while track != 0 and (track, sector) not in seen:
            seen.add((track, sector))
            data = self._read_logical_sector(track, sector)
            blocks.append((track, sector, data))
            if len(data) < 2 or data[0] == 0:
                break
            track, sector = data[0], data[1]
        return blocks

    def _logical_to_physical_address(self, track: int, sector: int) -> tuple[int, int, int]:
        physical_head = self._logical_head_order[sector // 20]
        physical_sector = (sector % 20) // 2 + 1
        return track - 1, physical_head, physical_sector

    def extract_file(self, path: str) -> bytes:
        record = self._record_for_file_path(path)
        return self._read_chain(record.start_track, record.start_sector)

    def file_sector_addresses(self, path: str) -> set[tuple[int, int, int]]:
        """Return physical map addresses containing a 1581 file's logical blocks."""

        record = self._record_for_file_path(path)
        addresses: set[tuple[int, int, int]] = set()
        for track, sector, _data in self._iter_chain_blocks(record.start_track, record.start_sector):
            addresses.add(self._logical_to_physical_address(track, sector))
        return addresses

    def logical_file_sector_addresses(self, path: str) -> set[tuple[int, int, int]]:
        """Return 1581 logical ``(track, head, sector)`` blocks for BAM/grid maps."""

        record = self._record_for_file_path(path)
        return {(track, 0, sector) for track, sector, _data in self._iter_chain_blocks(record.start_track, record.start_sector)}

    def import_file(self, image_bytes: bytes, directory: str, filename: str, data: bytes) -> bytes:
        """Return a copy with one CBM DOS 1581 file imported."""

        raw_name, file_type = self._encode_directory_name_and_type(filename)
        if file_type == CBM_FILE_TYPE_CODES["REL"]:
            raise FilesystemError("CBM DOS 1581 REL import is not implemented; side-sector allocation is required")
        target_name = raw_name.replace(b"\xA0", b" ").rstrip().decode("latin-1")
        directory_start = self._directory_start_for_path(directory)
        if any(record.name.upper() == target_name.upper() for record in self._records_for_path(directory)):
            raise FilesystemError(f"1581 entry already exists: {target_name}")

        slot = self._find_free_directory_slot(*directory_start)
        blocks_needed = max(1, (len(data) + (LOGICAL_SECTOR_SIZE - 3)) // (LOGICAL_SECTOR_SIZE - 2))
        blocks = self._find_free_blocks(blocks_needed)
        patched = bytearray(image_bytes)
        for index, (track, sector) in enumerate(blocks):
            block = bytearray(LOGICAL_SECTOR_SIZE)
            chunk = data[index * (LOGICAL_SECTOR_SIZE - 2) : (index + 1) * (LOGICAL_SECTOR_SIZE - 2)]
            if index == len(blocks) - 1:
                block[0] = 0
                block[1] = len(chunk)
            else:
                next_track, next_sector = blocks[index + 1]
                block[0] = next_track
                block[1] = next_sector
            block[2 : 2 + len(chunk)] = chunk
            self._write_logical_sector(patched, track, sector, block)
            self._mark_block_used(patched, track, sector)

        entry = bytearray(32)
        entry[0] = 0x80 | file_type  # closed CBM DOS file
        entry[1] = blocks[0][0]
        entry[2] = blocks[0][1]
        entry[3:19] = raw_name
        entry[28:30] = len(blocks).to_bytes(2, "little")
        patched[slot : slot + 32] = entry
        return bytes(patched)

    def create_directory(self, image_bytes: bytes, parent: str, name: str) -> bytes:
        """Return a copy with one empty 1581 directory created."""

        raw_name = self._encode_directory_name(name)
        target_name = raw_name.replace(b"\xA0", b" ").rstrip().decode("latin-1")
        parent_start = self._directory_start_for_path(parent)
        if any(record.name.upper() == target_name.upper() for record in self._records_for_path(parent)):
            raise FilesystemError(f"1581 entry already exists: {target_name}")

        slot = self._find_free_directory_slot(*parent_start)
        blocks = self._find_free_blocks(1)
        patched = bytearray(image_bytes)
        directory_block = bytearray(LOGICAL_SECTOR_SIZE)
        directory_block[0] = 0
        directory_block[1] = 0
        self._write_logical_sector(patched, blocks[0][0], blocks[0][1], directory_block)
        self._mark_block_used(patched, blocks[0][0], blocks[0][1])

        entry = bytearray(32)
        entry[0] = 0x85  # closed DIR
        entry[1] = blocks[0][0]
        entry[2] = blocks[0][1]
        entry[3:19] = raw_name
        entry[28:30] = len(blocks).to_bytes(2, "little")
        patched[slot : slot + 32] = entry
        return bytes(patched)

    def replace_file(self, image_bytes: bytes, path: str, replacement: bytes) -> bytes:
        """Return a copy with one root-level 1581 file's contents replaced."""

        slot = self._root_directory_slot_for_path(path)
        if slot.record.is_dir:
            raise FilesystemError("1581 replace currently supports files only")
        if slot.record.file_type & 0x07 == CBM_FILE_TYPE_CODES["REL"]:
            raise FilesystemError("CBM DOS 1581 REL mutation is not implemented; side-sector allocation is required")
        blocks_needed = max(1, (len(replacement) + (LOGICAL_SECTOR_SIZE - 3)) // (LOGICAL_SECTOR_SIZE - 2))
        patched = bytearray(image_bytes)
        for track, sector, _data in self._iter_chain_blocks(slot.record.start_track, slot.record.start_sector):
            self._mark_block_free(patched, track, sector)

        blocks = self._find_free_blocks_from_bytes(patched, blocks_needed)
        for index, (track, sector) in enumerate(blocks):
            block = bytearray(LOGICAL_SECTOR_SIZE)
            chunk = replacement[index * (LOGICAL_SECTOR_SIZE - 2) : (index + 1) * (LOGICAL_SECTOR_SIZE - 2)]
            if index == len(blocks) - 1:
                block[0] = 0
                block[1] = len(chunk)
            else:
                next_track, next_sector = blocks[index + 1]
                block[0] = next_track
                block[1] = next_sector
            block[2 : 2 + len(chunk)] = chunk
            self._write_logical_sector(patched, track, sector, block)
            self._mark_block_used(patched, track, sector)

        entry = bytearray(patched[slot.offset : slot.offset + 32])
        entry[0] = (entry[0] & 0xF8) | 0x02 | 0x80
        entry[1] = blocks[0][0]
        entry[2] = blocks[0][1]
        entry[28:30] = len(blocks).to_bytes(2, "little")
        patched[slot.offset : slot.offset + 32] = entry
        return bytes(patched)

    def delete_entry(self, image_bytes: bytes, path: str) -> bytes:
        """Return a copy with one root-level 1581 file scratched."""

        slot = self._root_directory_slot_for_path(path)
        if slot.record.is_dir:
            raise FilesystemError("1581 directory delete is not implemented yet")
        if slot.record.file_type & 0x07 == CBM_FILE_TYPE_CODES["REL"]:
            raise FilesystemError("CBM DOS 1581 REL mutation is not implemented; side-sector allocation is required")
        patched = bytearray(image_bytes)
        for track, sector, _data in self._iter_chain_blocks(slot.record.start_track, slot.record.start_sector):
            self._mark_block_free(patched, track, sector)
        patched[slot.offset] = 0x00
        return bytes(patched)

    def bam_blocks(self, max_tracks: Optional[int] = None) -> List[tuple[int, int, int, str]]:
        file_blocks: set[tuple[int, int]] = set()
        for record in self.directory:
            track, sector = record.start_track, record.start_sector
            seen: set[tuple[int, int]] = set()
            while track != 0 and (track, sector) not in seen:
                seen.add((track, sector))
                file_blocks.add((track, sector))
                try:
                    block = self._read_logical_sector(track, sector)
                except FilesystemError:
                    break
                if len(block) < 2:
                    break
                track, sector = block[0], block[1]

        system_blocks = {
            (DIRECTORY_TRACK, DIRECTORY_HEADER_SECTOR),
            (DIRECTORY_TRACK, 1),
            (DIRECTORY_TRACK, 2),
        }
        try:
            track, sector = DIRECTORY_TRACK, DIRECTORY_START_SECTOR
            seen: set[tuple[int, int]] = set()
            while track != 0 and (track, sector) not in seen:
                seen.add((track, sector))
                system_blocks.add((track, sector))
                block = self._read_logical_sector(track, sector)
                if len(block) < 2:
                    break
                track, sector = block[0], block[1]
        except FilesystemError:
            pass

        blocks: List[tuple[int, int, int, str]] = []
        track_limit = min(max_tracks or 80, 80)
        for track in range(1, track_limit + 1):
            for sector in range(LOGICAL_SECTORS_PER_TRACK):
                key = (track, sector)
                if key in file_blocks:
                    state = "bam_file"
                elif key in system_blocks:
                    state = "bam_system"
                elif self._block_is_free(track, sector):
                    state = "bam_free"
                else:
                    state = "bam_used"
                blocks.append((track, 0, sector, state))
        return blocks

    def _bam_bitmap_location(self, track: int, sector: int) -> tuple[int, int, int] | None:
        if track < 1 or track > 80 or sector < 0 or sector >= LOGICAL_SECTORS_PER_TRACK:
            return None
        bam_sector = 1 if track <= 40 else 2
        track_index = (track - 1) if track <= 40 else (track - 41)
        bam_offset = self._logical_offset(DIRECTORY_TRACK, bam_sector)
        entry_offset = bam_offset + 16 + track_index * 6
        return entry_offset, entry_offset + 1 + sector // 8, sector % 8

    def _block_is_free(self, track: int, sector: int) -> bool:
        location = self._bam_bitmap_location(track, sector)
        if location is None:
            return False
        count_offset, byte_offset, bit = location
        try:
            bam_sector = 1 if track <= 40 else 2
            bam = self._read_logical_sector(DIRECTORY_TRACK, bam_sector)
            local_byte = byte_offset - self._logical_offset(DIRECTORY_TRACK, bam_sector)
            return local_byte < len(bam) and bool(bam[local_byte] & (1 << bit))
        except FilesystemError:
            return False

    def _mark_block_used(self, image_bytes: bytearray, track: int, sector: int) -> None:
        location = self._bam_bitmap_location(track, sector)
        if location is None:
            raise FilesystemError("No 1581 BAM entry available for block")
        count_offset, byte_offset, bit = location
        if byte_offset >= len(image_bytes):
            raise FilesystemError("1581 BAM bitmap exceeds image size")
        mask = 1 << bit
        if image_bytes[byte_offset] & mask:
            image_bytes[byte_offset] &= ~mask
            if image_bytes[count_offset] > 0:
                image_bytes[count_offset] -= 1

    def _mark_block_free(self, image_bytes: bytearray, track: int, sector: int) -> None:
        location = self._bam_bitmap_location(track, sector)
        if location is None:
            raise FilesystemError("No 1581 BAM entry available for block")
        count_offset, byte_offset, bit = location
        if byte_offset >= len(image_bytes):
            raise FilesystemError("1581 BAM bitmap exceeds image size")
        mask = 1 << bit
        if not image_bytes[byte_offset] & mask:
            image_bytes[byte_offset] |= mask
            if image_bytes[count_offset] < 255:
                image_bytes[count_offset] += 1

    def _find_free_blocks(self, count: int) -> list[tuple[int, int]]:
        return self._find_free_blocks_from_bytes(None, count)

    def _find_free_blocks_from_bytes(self, image_bytes: Optional[bytearray], count: int) -> list[tuple[int, int]]:
        blocks: list[tuple[int, int]] = []
        reserved = {
            (DIRECTORY_TRACK, DIRECTORY_HEADER_SECTOR),
            (DIRECTORY_TRACK, 1),
            (DIRECTORY_TRACK, 2),
        }
        for track in range(1, 81):
            for sector in range(LOGICAL_SECTORS_PER_TRACK):
                if (track, sector) in reserved:
                    continue
                if self._block_is_free_in_bytes(image_bytes, track, sector):
                    blocks.append((track, sector))
                    if len(blocks) == count:
                        return blocks
        raise FilesystemError(f"Need {count:,} free 1581 block(s), found {len(blocks):,}")

    def _block_is_free_in_bytes(self, image_bytes: Optional[bytearray], track: int, sector: int) -> bool:
        if image_bytes is None:
            return self._block_is_free(track, sector)
        location = self._bam_bitmap_location(track, sector)
        if location is None:
            return False
        _count_offset, byte_offset, bit = location
        return byte_offset < len(image_bytes) and bool(image_bytes[byte_offset] & (1 << bit))

    def _find_free_directory_slot(
        self,
        start_track: int = DIRECTORY_TRACK,
        start_sector: int = DIRECTORY_START_SECTOR,
    ) -> int:
        track, sector = start_track, start_sector
        seen: set[tuple[int, int]] = set()
        while track != 0 and (track, sector) not in seen:
            seen.add((track, sector))
            data = self._read_logical_sector(track, sector)
            sector_offset = self._logical_offset(track, sector)
            for idx in range(8):
                entry_offset = sector_offset + 2 + idx * 32
                if data[2 + idx * 32] in {0x00, 0xE5}:
                    return entry_offset
            track, sector = data[0], data[1]
        raise FilesystemError("1581 root directory has no free entry slots")

    def _encode_directory_name(self, filename: str) -> bytes:
        return self._encode_directory_name_and_type(filename)[0]

    def _encode_directory_name_and_type(self, filename: str) -> tuple[bytes, int]:
        name = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        file_type = CBM_FILE_TYPE_CODES["PRG"]
        stem, separator, suffix = name.rpartition(".")
        if separator and stem and suffix.upper() in CBM_FILE_TYPE_CODES:
            name = stem
            file_type = CBM_FILE_TYPE_CODES[suffix.upper()]
        name = name.strip().upper()
        if not name:
            raise FilesystemError("Choose a non-empty 1581 file name")
        if len(name) > 16:
            raise FilesystemError("1581 import currently supports names up to 16 characters")
        try:
            encoded = name.encode("ascii")
        except UnicodeError as exc:
            raise FilesystemError("1581 import currently supports ASCII file names only") from exc
        invalid = set('/\\":*?,')
        if any(ord(char) < 32 or char in invalid for char in name):
            raise FilesystemError("1581 name contains unsupported characters")
        return encoded.ljust(16, b"\xA0"), file_type

    @staticmethod
    def _decode_header_field(raw: bytes) -> str:
        return raw.replace(b"\xA0", b" ").replace(b"\x00", b" ").decode("latin-1", errors="ignore").strip()

    def _parse_header_metadata(self, header: bytes) -> None:
        self.disk_name = self._decode_header_field(header[4:20])
        self.disk_id = self._decode_header_field(header[22:24])
        self.dos_type = self._decode_header_field(header[25:27])

    def metadata(self) -> Dict[str, str]:
        return {
            "filesystem": "cbm_dos_1581",
            "disk_name": self.disk_name,
            "disk_id": self.disk_id,
            "dos_type": self.dos_type,
        }
