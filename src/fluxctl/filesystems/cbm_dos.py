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
        self.sides = 1
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

    def _logical_to_physical(self, track: int, sector: int) -> Tuple[int, int, int]:
        if self.sides > 1 and track > 35 and isinstance(self.image, TrackSectorImage):
            return track - 36, 1, sector
        return track - 1, 0, sector

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
            key = self._logical_to_physical(track, sector)
            if key in lookup:
                return lookup[key]
            raise FilesystemError("Invalid track/sector reference")
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
                blocks = int.from_bytes(entry[28:30], "little")
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
            self.sides = max(getattr(layout, "sides", 1), 1)
            self.sectors_per_track = list(layout.track_sectors) * self.sides
        elif not isinstance(image, TrackSectorImage):
            default_sector_count = sum(DEFAULT_SECTORS_PER_TRACK)
            if getattr(image, "total_sectors", 0) >= default_sector_count * 2:
                self.sides = 2
                self.sectors_per_track = list(DEFAULT_SECTORS_PER_TRACK) * 2
        if isinstance(image, TrackSectorImage) and image.tracks:
            actual_tracks = (max(track.track for track in image.tracks) + 1) * (
                max(track.head for track in image.tracks) + 1
            )
            if actual_tracks < len(self.sectors_per_track):
                self.sectors_per_track = self.sectors_per_track[:actual_tracks]
        bam = None
        try:
            bam = self._read_ts(*BAM_SECTOR)
        except FilesystemError:
            return False
        if bam is None or len(bam) < SECTOR_SIZE:
            return False
        self.dos_type = bam[0xA2:0xA4]
        if not self.dos_type or not all(chr(b).isalnum() for b in self.dos_type):
            return False
        try:
            self._parse_directory()
        except FilesystemError:
            return False
        return True

    def diagnostic_evidence(self, image: SectorImage) -> List[str]:
        """Describe why a CBM DOS image could not be mounted.

        This intentionally reports missing directory-chain sectors instead of
        treating an incomplete flux reconstruction as an unknown filesystem.
        The method never invents sector contents and is safe to use during
        probing and conversion preflight.
        """

        mounted = self.probe(image)
        if mounted:
            return ["cbm_dos_probe=1"]

        evidence: List[str] = ["cbm_dos_probe_failed=1"]
        try:
            bam = self._read_ts(*BAM_SECTOR)
        except FilesystemError:
            return evidence + ["cbm_dos_bam_missing=T18/S00"]

        if len(bam) < SECTOR_SIZE:
            return evidence + ["cbm_dos_bam_short=1"]
        evidence.append("cbm_dos_bam_present=T18/S00")
        if self.dos_type and all(chr(value).isalnum() for value in self.dos_type):
            evidence.append(f"cbm_dos_type={self.dos_type.decode('latin-1')}")

        track, sector = DIRECTORY_TRACK, DIRECTORY_START_SECTOR
        seen: set[Tuple[int, int]] = set()
        evidence.append("cbm_dos_directory_chain_start=T18/S01")
        while track != 0 and (track, sector) not in seen:
            seen.add((track, sector))
            try:
                data = self._read_ts(track, sector)
            except FilesystemError:
                evidence.append(f"cbm_dos_directory_chain_missing=T{track:02d}/S{sector:02d}")
                evidence.append(f"cbm_dos_directory_chain_reached={len(seen) - 1}")
                return evidence
            if len(data) < 2:
                evidence.append(f"cbm_dos_directory_chain_short=T{track:02d}/S{sector:02d}")
                return evidence
            track, sector = data[0], data[1]

        if track == 0:
            evidence.append("cbm_dos_directory_chain_complete=1")
        else:
            evidence.append("cbm_dos_directory_chain_loop=1")
        return evidence

    def _bam_sector(self) -> bytes:
        return self._read_ts(*BAM_SECTOR)

    def bam_blocks(self, max_tracks: Optional[int] = None) -> List[Tuple[int, int, int, str]]:
        """Return one BAM state for each physical CBM DOS block.

        States are derived from the BAM bitmap and refined with directory-file
        chains where available:
        ``bam_file`` is a block reached from a directory entry, ``bam_system``
        is BAM/directory metadata, ``bam_free`` is marked free in the BAM, and
        ``bam_used`` is allocated but not reached from the currently parsed root
        directory.
        """

        bam = self._bam_sector()
        file_blocks: set[Tuple[int, int]] = set()
        for record in self.directory:
            track, sector = record.start_track, record.start_sector
            seen: set[Tuple[int, int]] = set()
            while track != 0 and (track, sector) not in seen:
                seen.add((track, sector))
                file_blocks.add((track, sector))
                try:
                    data = self._read_ts(track, sector)
                except FilesystemError:
                    break
                if len(data) < 2:
                    break
                track, sector = data[0], data[1]

        directory_blocks = {(DIRECTORY_TRACK, 0)}
        try:
            track, sector = DIRECTORY_TRACK, DIRECTORY_START_SECTOR
            seen: set[Tuple[int, int]] = set()
            while track != 0 and (track, sector) not in seen:
                seen.add((track, sector))
                directory_blocks.add((track, sector))
                data = self._read_ts(track, sector)
                if len(data) < 2:
                    break
                track, sector = data[0], data[1]
        except FilesystemError:
            pass

        side1_bam = None
        if self.sides > 1:
            try:
                side1_bam = self._read_ts(53, 0)
                directory_blocks.add((53, 0))
            except FilesystemError:
                side1_bam = None

        blocks: List[Tuple[int, int, int, str]] = []
        track_limit = min(max_tracks or len(self.sectors_per_track), len(self.sectors_per_track))
        for track in range(1, track_limit + 1):
            if track <= 35:
                offset = 4 + (track - 1) * 4
                bitmap = bam[offset + 1 : offset + 4]
                head = 0
            elif self.sides > 1:
                side1_index = track - 36
                bitmap_offset = side1_index * 3
                bitmap = side1_bam[bitmap_offset : bitmap_offset + 3] if side1_bam is not None else b""
                head = 1
            else:
                bitmap = b""
                head = 0
            for sector in range(self._sectors_for_track(track - 1)):
                byte_index = sector // 8
                bit_index = sector % 8
                free = byte_index < len(bitmap) and bool(bitmap[byte_index] & (1 << bit_index))
                key = (track, sector)
                if key in file_blocks:
                    state = "bam_file"
                elif key in directory_blocks:
                    state = "bam_system"
                elif free:
                    state = "bam_free"
                else:
                    state = "bam_used"
                blocks.append((track, head, sector, state))
        return blocks

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

    def _iter_chain_blocks(self, start_track: int, start_sector: int) -> List[Tuple[int, int, bytes]]:
        blocks: List[Tuple[int, int, bytes]] = []
        seen: set[Tuple[int, int]] = set()
        track, sector = start_track, start_sector
        while track != 0 and (track, sector) not in seen:
            seen.add((track, sector))
            data = self._read_ts(track, sector)
            blocks.append((track, sector, data))
            if len(data) < 2 or data[0] == 0:
                break
            track, sector = data[0], data[1]
        return blocks

    def _record_for_file(self, path: str) -> DirectoryRecord:
        target = path.lstrip("/").upper()
        for record in self.directory:
            if record.name.upper() == target:
                return record
        raise FilesystemError(f"File not found: {path}")

    def list_directory(self, path: str = "/") -> List[FileEntry]:
        if path not in {"/", ""}:
            raise FilesystemError("CBM DOS reader only supports root directory")
        entries: List[FileEntry] = []
        for record in self.directory:
            if record.blocks:
                try:
                    size = len(self._read_chain(record.start_track, record.start_sector))
                except FilesystemError:
                    size = record.blocks * (SECTOR_SIZE - 2)
            else:
                size = 0
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
        record = self._record_for_file(path)
        return self._read_chain(record.start_track, record.start_sector)

    def file_sector_addresses(self, path: str) -> set[tuple[int, int, int]]:
        """Return physical ``(track, head, sector_id)`` addresses occupied by a file."""

        record = self._record_for_file(path)
        addresses: set[tuple[int, int, int]] = set()
        for track, sector, _data in self._iter_chain_blocks(record.start_track, record.start_sector):
            addresses.add(self._logical_to_physical(track, sector))
        return addresses

    def logical_file_sector_addresses(self, path: str) -> set[tuple[int, int, int]]:
        """Return CBM logical ``(track, head, sector)`` addresses occupied by a file."""

        record = self._record_for_file(path)
        addresses: set[tuple[int, int, int]] = set()
        for track, sector, _data in self._iter_chain_blocks(record.start_track, record.start_sector):
            _physical_track, head, _physical_sector = self._logical_to_physical(track, sector)
            addresses.add((track, head, sector))
        return addresses

    def import_file(self, image_bytes: bytes, directory: str, filename: str, data: bytes) -> bytes:
        """Return a copy with one root-level CBM DOS PRG file imported."""

        if directory.strip("/") != "":
            raise FilesystemError("CBM DOS import currently supports the root directory only")
        raw_name = self._encode_directory_name(filename)
        target_name = raw_name.replace(b"\xA0", b" ").rstrip().decode("latin-1")
        if any(record.name.upper() == target_name.upper() for record in self.directory):
            raise FilesystemError(f"CBM DOS entry already exists: {target_name}")

        slot = self._find_free_directory_slot()
        blocks_needed = max(1, (len(data) + (SECTOR_SIZE - 3)) // (SECTOR_SIZE - 2))
        blocks = self._find_free_blocks(blocks_needed)
        patched = bytearray(image_bytes)
        for index, (track, sector) in enumerate(blocks):
            block = bytearray(SECTOR_SIZE)
            chunk = data[index * (SECTOR_SIZE - 2) : (index + 1) * (SECTOR_SIZE - 2)]
            if index == len(blocks) - 1:
                block[0] = 0
                block[1] = len(chunk)
            else:
                next_track, next_sector = blocks[index + 1]
                block[0] = next_track
                block[1] = next_sector
            block[2 : 2 + len(chunk)] = chunk
            self._write_ts(patched, track, sector, block)
            self._mark_block_used(patched, track, sector)

        entry = bytearray(32)
        entry[0] = 0x82  # closed PRG
        entry[1] = blocks[0][0]
        entry[2] = blocks[0][1]
        entry[3:19] = raw_name
        entry[28:30] = len(blocks).to_bytes(2, "little")
        patched[slot : slot + 32] = entry
        return bytes(patched)

    def _write_ts(self, image_bytes: bytearray, track: int, sector: int, data: bytes) -> None:
        offset = self._ts_to_lba(track, sector) * SECTOR_SIZE
        if offset + SECTOR_SIZE > len(image_bytes):
            raise FilesystemError("CBM DOS block exceeds image size")
        image_bytes[offset : offset + SECTOR_SIZE] = data[:SECTOR_SIZE].ljust(SECTOR_SIZE, b"\x00")

    def _bam_bitmap_location(self, track: int, sector: int) -> tuple[int, int, int] | None:
        if track <= 35:
            bam_offset = self._ts_to_lba(18, 0) * SECTOR_SIZE
            entry_offset = bam_offset + 4 + (track - 1) * 4
            return entry_offset, entry_offset + 1 + sector // 8, sector % 8
        if self.sides <= 1:
            return None
        side_index = track - 36
        primary_bam_offset = self._ts_to_lba(18, 0) * SECTOR_SIZE
        side_bam_offset = self._ts_to_lba(53, 0) * SECTOR_SIZE
        count_offset = primary_bam_offset + 221 + side_index
        return count_offset, side_bam_offset + side_index * 3 + sector // 8, sector % 8

    def _block_is_free(self, track: int, sector: int) -> bool:
        try:
            if track <= 35:
                bam = self._bam_sector()
                offset = 4 + (track - 1) * 4
                bitmap = bam[offset + 1 : offset + 4]
            else:
                if self.sides <= 1:
                    return False
                bam = self._read_ts(53, 0)
                side_index = track - 36
                bitmap = bam[side_index * 3 : side_index * 3 + 3]
            return sector // 8 < len(bitmap) and bool(bitmap[sector // 8] & (1 << (sector % 8)))
        except FilesystemError:
            return False

    def _mark_block_used(self, image_bytes: bytearray, track: int, sector: int) -> None:
        location = self._bam_bitmap_location(track, sector)
        if location is None:
            raise FilesystemError("No BAM entry available for CBM DOS block")
        count_offset, byte_offset, bit = location
        if byte_offset >= len(image_bytes):
            raise FilesystemError("BAM bitmap exceeds image size")
        mask = 1 << bit
        if image_bytes[byte_offset] & mask:
            image_bytes[byte_offset] &= ~mask
            if image_bytes[count_offset] > 0:
                image_bytes[count_offset] -= 1

    def _find_free_blocks(self, count: int) -> list[tuple[int, int]]:
        blocks: list[tuple[int, int]] = []
        reserved_tracks = {18, 53} if self.sides > 1 else {18}
        for track in range(1, len(self.sectors_per_track) + 1):
            if track in reserved_tracks:
                continue
            for sector in range(self._sectors_for_track(track - 1)):
                if self._block_is_free(track, sector):
                    blocks.append((track, sector))
                    if len(blocks) == count:
                        return blocks
        raise FilesystemError(f"Need {count:,} free CBM DOS block(s), found {len(blocks):,}")

    def _find_free_directory_slot(self) -> int:
        track, sector = DIRECTORY_TRACK, DIRECTORY_START_SECTOR
        seen: set[Tuple[int, int]] = set()
        while track != 0 and (track, sector) not in seen:
            seen.add((track, sector))
            data = self._read_ts(track, sector)
            sector_offset = self._ts_to_lba(track, sector) * SECTOR_SIZE
            for idx in range(8):
                entry_offset = sector_offset + 2 + idx * 32
                if data[2 + idx * 32] in {0x00, 0xE5}:
                    return entry_offset
            track = data[0]
            sector = data[1]
        raise FilesystemError("CBM DOS root directory has no free entry slots")

    def _encode_directory_name(self, filename: str) -> bytes:
        name = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        if "." in name:
            name = name.rsplit(".", 1)[0]
        name = name.strip().upper()
        if not name:
            raise FilesystemError("Choose a non-empty CBM DOS file name")
        if len(name) > 16:
            raise FilesystemError("CBM DOS import currently supports names up to 16 characters")
        try:
            encoded = name.encode("ascii")
        except UnicodeError as exc:
            raise FilesystemError("CBM DOS import currently supports ASCII file names only") from exc
        invalid = set('/\\":*?,')
        if any(ord(char) < 32 or char in invalid for char in name):
            raise FilesystemError("CBM DOS name contains unsupported characters")
        return encoded.ljust(16, b"\xA0")

    def metadata(self) -> Dict[str, str]:
        return {"dos_type": self.dos_type.decode("latin-1", errors="ignore")}
