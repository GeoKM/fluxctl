"""Minimal CP/M filesystem probe."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from ..exceptions import FilesystemError
from . import FileEntry, Filesystem, RawSectorImage, SectorImage


_ALLOWED_CHARS = set(b"ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789$%'-_@~`!#()^")


@dataclass(slots=True)
class CPMDirectoryRecord:
    user: int
    name: str
    extent: int
    records: int
    allocation: bytes


@dataclass(frozen=True, slots=True)
class CPMDiskParameters:
    reserved_tracks: int
    sectors_per_track: int
    sector_size: int
    block_size: int
    skew: tuple[int, ...]
    allocation_width: int = 1
    directory_blocks: int = 2

    @property
    def sectors_per_block(self) -> int:
        return self.block_size // self.sector_size


STANDARD_26_SECTOR_SKEW = (
    0,
    6,
    12,
    18,
    24,
    4,
    10,
    16,
    22,
    2,
    8,
    14,
    20,
    1,
    7,
    13,
    19,
    25,
    5,
    11,
    17,
    23,
    3,
    9,
    15,
    21,
)


def cpm_disk_parameters_for_layout(layout_id: str) -> CPMDiskParameters | None:
    if layout_id in {"generic_fm_8inch_cpm_256k", "dec_dec_rx02_rx02_250k"}:
        return CPMDiskParameters(
            reserved_tracks=2,
            sectors_per_track=26,
            sector_size=128,
            block_size=1024,
            skew=STANDARD_26_SECTOR_SKEW,
        )
    if layout_id == "kaypro_mfm_ssdd_40_200k":
        return CPMDiskParameters(
            reserved_tracks=1,
            sectors_per_track=10,
            sector_size=512,
            block_size=1024,
            skew=(0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
            directory_blocks=4,
        )
    if layout_id == "osborne_mfm_ssdd_200k":
        return CPMDiskParameters(
            reserved_tracks=3,
            sectors_per_track=5,
            sector_size=1024,
            block_size=1024,
            skew=(0, 1, 2, 3, 4),
        )
    return None


def _clean_name_field(field: bytes) -> str:
    return bytes(byte & 0x7F for byte in field).decode("ascii", errors="ignore").rstrip()


def _entry_name(entry: bytes) -> str:
    stem = _clean_name_field(entry[1:9])
    suffix = _clean_name_field(entry[9:12])
    if not stem:
        return ""
    return f"{stem}.{suffix}" if suffix else stem


def _encode_cpm_83_name(host_name: str) -> tuple[bytes, bytes, str]:
    name = Path(host_name).name.upper()
    if "." in name:
        stem, suffix = name.rsplit(".", 1)
    else:
        stem, suffix = name, ""
    stem = stem.strip()
    suffix = suffix.strip()
    if not stem or len(stem) > 8 or len(suffix) > 3:
        raise FilesystemError("CP/M import requires an 8.3 filename")
    raw = (stem + suffix).encode("ascii", errors="strict")
    if any(ch not in _ALLOWED_CHARS for ch in raw):
        raise FilesystemError("CP/M import filename contains unsupported characters")
    display = f"{stem}.{suffix}" if suffix else stem
    return stem.encode("ascii").ljust(8, b" "), suffix.encode("ascii").ljust(3, b" "), display


def _looks_like_cpm_entry(entry: bytes) -> bool:
    if len(entry) < 32:
        return False
    user = entry[0]
    if user >= 0x20:
        return False
    stem = bytes(byte & 0x7F for byte in entry[1:9]).rstrip(b" ")
    suffix = bytes(byte & 0x7F for byte in entry[9:12]).rstrip(b" ")
    if not stem:
        return False
    if b" " in stem or b" " in suffix:
        return False
    if any((ch not in _ALLOWED_CHARS) for ch in stem + suffix):
        return False
    if not _entry_name(entry):
        return False
    return True


def cpm_directory_score_for_layout(image: SectorImage, layout_id: str) -> int:
    """Return CP/M directory-entry density at the layout's DPB directory start."""

    params = cpm_disk_parameters_for_layout(layout_id)
    if params is None:
        return 0
    if params.sector_size != getattr(image, "bytes_per_sector", params.sector_size):
        return 0
    first_sector = params.reserved_tracks * params.sectors_per_track
    sectors_to_scan = max(1, min(8, params.directory_blocks * params.sectors_per_block))
    score = 0
    try:
        data = b"".join(image.read_sector(first_sector + offset) for offset in range(sectors_to_scan))
    except Exception:
        return 0
    for offset in range(0, len(data), 32):
        entry = data[offset : offset + 32]
        if len(entry) < 32 or entry[0] == 0xE5:
            continue
        if _looks_like_cpm_entry(entry):
            score += 1
    return score


class CPMFilesystem(Filesystem):
    """Lightweight CP/M probe based on directory entry structure."""

    def __init__(self) -> None:
        self._probed = False
        self._records: List[CPMDirectoryRecord] = []
        self._variant = "cpm"
        self._image: SectorImage | None = None

    def _directory_records(self, image: SectorImage) -> List[CPMDirectoryRecord]:
        records: List[CPMDirectoryRecord] = []
        try:
            sector_iter = image.iter_sectors()
        except Exception:
            def sector_iter():
                idx = 0
                while True:
                    yield image.read_sector(idx)
                    idx += 1

        for idx, data in enumerate(sector_iter):
            if idx >= 2048:
                break
            sector_records: List[CPMDirectoryRecord] = []
            for offset in range(0, len(data), 32):
                entry = data[offset : offset + 32]
                if len(entry) < 32 or entry[0] == 0xE5:
                    continue
                if not _looks_like_cpm_entry(entry):
                    continue
                name = _entry_name(entry)
                if not name:
                    continue
                sector_records.append(
                    CPMDirectoryRecord(
                        user=entry[0],
                        name=name,
                        extent=entry[12],
                        records=entry[15],
                        allocation=entry[16:32],
                    )
                )
            # Require some local density so random program text is not enough.
            if len(sector_records) >= 2:
                records.extend(sector_records)
        return records

    def _record_from_entry(self, entry: bytes) -> CPMDirectoryRecord | None:
        if len(entry) < 32 or entry[0] == 0xE5:
            return None
        if not _looks_like_cpm_entry(entry):
            return None
        name = _entry_name(entry)
        if not name:
            return None
        return CPMDirectoryRecord(
            user=entry[0],
            name=name,
            extent=entry[12],
            records=entry[15],
            allocation=entry[16:32],
        )

    def _modelled_directory_record_offsets(
        self, image: SectorImage, params: CPMDiskParameters
    ) -> list[tuple[CPMDirectoryRecord, int]]:
        records: list[tuple[CPMDirectoryRecord, int]] = []
        first_sector = params.reserved_tracks * params.sectors_per_track
        sectors_to_scan = params.directory_blocks * params.sectors_per_block
        for sector_offset in range(sectors_to_scan):
            logical_sector = first_sector + sector_offset
            try:
                data = self._read_image_logical_sector(image, logical_sector, params)
            except Exception:
                return records
            sector_start = self._physical_lba_for_logical_sector(logical_sector, params) * params.sector_size
            for entry_offset in range(0, len(data), 32):
                entry = data[entry_offset : entry_offset + 32]
                record = self._record_from_entry(entry)
                if record is not None:
                    records.append((record, sector_start + entry_offset))
        return records

    def _modelled_directory_records(self, image: SectorImage, params: CPMDiskParameters) -> List[CPMDirectoryRecord]:
        return [record for record, _offset in self._modelled_directory_record_offsets(image, params)]

    def probe(self, image: SectorImage) -> bool:
        """Heuristic CP/M probe: scan early sectors for directory entries."""

        self._image = image
        params = self._parameters_for_image(image)
        if params is not None:
            self._records = self._modelled_directory_records(image, params)
            self._probed = bool(self._records) or self._is_modelled_blank_directory(image)
        else:
            self._records = self._directory_records(image)
            self._probed = len(self._records) >= 2
        self._variant = self._detect_variant(image)
        return self._probed

    def _is_modelled_blank_directory(self, image: SectorImage) -> bool:
        layout_id = getattr(getattr(image, "layout", None), "layout_id", "")
        params = cpm_disk_parameters_for_layout(layout_id)
        if params is None:
            return False
        if params.sector_size != getattr(image, "bytes_per_sector", params.sector_size):
            return False
        first_sector = params.reserved_tracks * params.sectors_per_track
        sectors_to_scan = params.directory_blocks * params.sectors_per_block
        try:
            data = b"".join(image.read_sector(first_sector + offset) for offset in range(sectors_to_scan))
        except Exception:
            return False
        for offset in range(0, len(data), 32):
            entry = data[offset : offset + 32]
            if len(entry) == 32 and entry != b"\xE5" * 32:
                return False
        return bool(data)

    def _detect_variant(self, image: SectorImage) -> str:
        names = {record.name.upper() for record in self._records}
        cpm3_markers = {
            "BOOTV3",
            "BIOS3",
            "CPM+.SYS",
            "CPM3.SYS",
            "CPM3.LIB",
            "DATEC.RSX",
            "DIRLBL.RSX",
            "SETDEF.COM",
            "SHOW.COM",
        }
        if cpm3_markers & names:
            return "c128_cpm_3_0"

        layout_id = getattr(getattr(image, "layout", None), "layout_id", "")
        if layout_id == "commodore_gcr_1541_cpm_170k":
            return "c64_cpm_2_2"
        if layout_id.startswith("commodore_mfm_1571_cpm_"):
            return "c128_cpm_3_0"
        return "cpm"

    def _disk_parameters(self) -> CPMDiskParameters | None:
        if self._image is None:
            return None
        return self._parameters_for_image(self._image)

    def _parameters_for_image(self, image: SectorImage) -> CPMDiskParameters | None:
        layout = getattr(image, "layout", None)
        layout_id = getattr(layout, "layout_id", "")
        return cpm_disk_parameters_for_layout(layout_id)

    def _is_c64_cpm_2_2(self) -> bool:
        layout_id = getattr(getattr(self._image, "layout", None), "layout_id", "") if self._image is not None else ""
        return self._variant == "c64_cpm_2_2" or layout_id == "commodore_gcr_1541_170k"

    def list_directory(self, path: str = "/") -> List[FileEntry]:
        if path not in {"/", ""}:
            raise FilesystemError("CP/M reader only supports root directory")
        grouped: Dict[tuple[int, str], List[CPMDirectoryRecord]] = {}
        for record in self._records:
            grouped.setdefault((record.user, record.name), []).append(record)

        entries: List[FileEntry] = []
        for (user, name), records in sorted(grouped.items(), key=lambda item: (item[0][0], item[0][1])):
            size = sum(record.records for record in records) * 128
            first_alloc = next((record.allocation[0] for record in records if record.allocation), 0)
            display_name = f"{user}:{name}" if user else name
            entries.append(
                FileEntry(
                    name=display_name,
                    is_dir=False,
                    size=size,
                    cluster_start=first_alloc,
                )
            )
        return entries

    def allocation_blocks(self) -> set[int]:
        """Return allocation block numbers referenced by directory entries."""

        params = self._disk_parameters()
        blocks = set(range(params.directory_blocks)) if params is not None else {0, 1}
        for record in self._records:
            blocks.update(block for block in record.allocation if block)
        return blocks

    def file_sector_addresses(self, path: str) -> set[tuple[int, int, int]]:
        """Return selected-file sector addresses for supported CP/M variants."""

        if not self._is_c64_cpm_2_2():
            raise FilesystemError("CP/M file allocation overlay needs a format-specific allocation map")
        addresses: set[tuple[int, int, int]] = set()
        for block in self._allocation_blocks_for_file(path):
            for logical_sector in range(block * 4, block * 4 + 4):
                logical_track = logical_sector // 17
                sector_id = logical_sector % 17
                track = logical_track + 2 if logical_track < 15 else logical_track - 15 + 18
                addresses.add((track, 0, sector_id))
        return addresses

    def _allocation_blocks_for_file(self, path: str) -> set[int]:
        return set(self._records_for_file(path, require_records=False)[1])

    def _records_for_file(self, path: str, *, require_records: bool) -> tuple[list[CPMDirectoryRecord], list[int]]:
        target_user, target_name = self._target_user_and_name(path)
        matches = [
            record
            for record in self._records
            if record.user == target_user and record.name.upper() == target_name
        ]
        if not matches:
            raise FilesystemError(f"File not found: {path}")
        blocks: list[int] = []
        for record in sorted(matches, key=lambda item: item.extent):
            if require_records and record.records == 0:
                continue
            blocks.extend(self._record_allocation_blocks(record))
        return sorted(matches, key=lambda item: item.extent), blocks

    def _target_user_and_name(self, path: str) -> tuple[int, str]:
        target = path.lstrip("/").upper()
        if ":" in target:
            user_text, target_name = target.split(":", 1)
            try:
                target_user = int(user_text)
            except ValueError as exc:
                raise FilesystemError(f"Invalid CP/M user area in path: {path}") from exc
        else:
            target_user = 0
            target_name = target
        return target_user, target_name

    def _record_allocation_blocks(self, record: CPMDirectoryRecord) -> list[int]:
        params = self._disk_parameters()
        width = params.allocation_width if params is not None else 1
        blocks: list[int] = []
        if width == 1:
            blocks.extend(block for block in record.allocation if block)
        elif width == 2:
            for offset in range(0, len(record.allocation), 2):
                block = int.from_bytes(record.allocation[offset : offset + 2], "little")
                if block:
                    blocks.append(block)
        return blocks

    def _read_logical_sector(self, sector_index: int, params: CPMDiskParameters) -> bytes:
        if self._image is None:
            raise FilesystemError("CP/M image is not mounted")
        return self._read_image_logical_sector(self._image, sector_index, params)

    def _read_image_logical_sector(self, image: SectorImage, sector_index: int, params: CPMDiskParameters) -> bytes:
        if params.sector_size != getattr(image, "bytes_per_sector", params.sector_size):
            raise FilesystemError("CP/M disk parameter block does not match image sector size")
        return image.read_sector(self._physical_lba_for_logical_sector(sector_index, params))

    def _physical_lba_for_logical_sector(self, sector_index: int, params: CPMDiskParameters) -> int:
        track = sector_index // params.sectors_per_track
        logical_sector = sector_index % params.sectors_per_track
        try:
            physical_sector = params.skew[logical_sector]
        except IndexError as exc:
            raise FilesystemError("CP/M sector skew table does not match sectors per track") from exc
        return track * params.sectors_per_track + physical_sector

    def _read_allocation_block(self, block: int, params: CPMDiskParameters) -> bytes:
        first_sector = params.reserved_tracks * params.sectors_per_track + block * params.sectors_per_block
        return b"".join(
            self._read_logical_sector(first_sector + offset, params)
            for offset in range(params.sectors_per_block)
        )

    def _write_logical_sector(self, image: bytearray, sector_index: int, params: CPMDiskParameters, data: bytes) -> None:
        if len(data) != params.sector_size:
            raise FilesystemError("CP/M sector write size mismatch")
        physical_lba = self._physical_lba_for_logical_sector(sector_index, params)
        offset = physical_lba * params.sector_size
        image[offset : offset + params.sector_size] = data

    def _write_allocation_block(self, image: bytearray, block: int, params: CPMDiskParameters, data: bytes) -> None:
        first_sector = params.reserved_tracks * params.sectors_per_track + block * params.sectors_per_block
        padded = data.ljust(params.block_size, b"\x1A")[: params.block_size]
        for offset in range(params.sectors_per_block):
            start = offset * params.sector_size
            self._write_logical_sector(
                image,
                first_sector + offset,
                params,
                padded[start : start + params.sector_size],
            )

    def _directory_offset(self, params: CPMDiskParameters) -> int:
        return params.reserved_tracks * params.sectors_per_track * params.sector_size

    def _free_directory_slots(self, image_bytes: bytes, params: CPMDiskParameters) -> list[int]:
        directory_offset = self._directory_offset(params)
        directory_size = params.directory_blocks * params.block_size
        slots: list[int] = []
        for offset in range(directory_offset, directory_offset + directory_size, 32):
            if image_bytes[offset] == 0xE5:
                slots.append(offset)
        return slots

    def _used_allocation_blocks(self, params: CPMDiskParameters) -> set[int]:
        used = set(range(params.directory_blocks))
        for record in self._records:
            used.update(self._record_allocation_blocks(record))
        return used

    def import_file(self, image_bytes: bytes, directory: str, host_name: str, data: bytes) -> bytes:
        if directory not in {"/", ""}:
            raise FilesystemError("CP/M import only supports the root directory")
        params = self._disk_parameters()
        if params is None:
            raise FilesystemError("CP/M import needs a modelled disk parameter block")
        if params.allocation_width != 1:
            raise FilesystemError("CP/M import currently supports one-byte allocation entries")
        if len(image_bytes) % params.sector_size:
            raise FilesystemError("CP/M image size is not aligned to the sector size")

        stem, suffix, display_name = _encode_cpm_83_name(host_name)
        if any(record.name.upper() == display_name for record in self._records):
            raise FilesystemError(f"File already exists: {display_name}")

        total_data_sectors = (len(image_bytes) // params.sector_size) - (params.reserved_tracks * params.sectors_per_track)
        total_blocks = total_data_sectors // params.sectors_per_block
        needed_blocks = max(1, (len(data) + params.block_size - 1) // params.block_size)
        used = self._used_allocation_blocks(params)
        free_blocks = [block for block in range(params.directory_blocks, total_blocks) if block not in used]
        if len(free_blocks) < needed_blocks:
            raise FilesystemError("Not enough free CP/M allocation blocks")

        needed_extents = max(1, (needed_blocks + 15) // 16)
        slots = self._free_directory_slots(image_bytes, params)
        if len(slots) < needed_extents:
            raise FilesystemError("No free CP/M directory slots")

        patched = bytearray(image_bytes)
        allocated = free_blocks[:needed_blocks]
        for index, block in enumerate(allocated):
            self._write_allocation_block(
                patched,
                block,
                params,
                data[index * params.block_size : (index + 1) * params.block_size],
            )

        records_remaining = (len(data) + 127) // 128
        for extent_index in range(needed_extents):
            extent_blocks = allocated[extent_index * 16 : (extent_index + 1) * 16]
            extent_records = min(records_remaining, len(extent_blocks) * params.sectors_per_block * (params.sector_size // 128))
            records_remaining = max(0, records_remaining - extent_records)
            entry = bytearray(32)
            entry[0] = 0
            entry[1:9] = stem
            entry[9:12] = suffix
            entry[12] = extent_index & 0x1F
            entry[15] = extent_records & 0xFF
            entry[16 : 16 + len(extent_blocks)] = bytes(extent_blocks)
            patched[slots[extent_index] : slots[extent_index] + 32] = entry

        return bytes(patched)

    def delete_entry(self, image_bytes: bytes, path: str) -> bytes:
        params = self._disk_parameters()
        if params is None:
            raise FilesystemError("CP/M delete needs a modelled disk parameter block")
        if len(image_bytes) % params.sector_size:
            raise FilesystemError("CP/M image size is not aligned to the sector size")
        target_user, target_name = self._target_user_and_name(path)
        image = RawSectorImage(image_bytes, params.sector_size)
        if self._image is not None:
            image.layout = getattr(self._image, "layout", None)
        matching_offsets = [
            offset
            for record, offset in self._modelled_directory_record_offsets(image, params)
            if record.user == target_user and record.name.upper() == target_name
        ]
        if not matching_offsets:
            raise FilesystemError(f"File not found: {path}")
        patched = bytearray(image_bytes)
        for offset in matching_offsets:
            patched[offset] = 0xE5
        return bytes(patched)

    def extract_file(self, path: str) -> bytes:
        params = self._disk_parameters()
        if params is None:
            raise FilesystemError("CP/M file extraction needs a format-specific disk parameter block")
        records, blocks = self._records_for_file(path, require_records=True)
        if not records:
            raise FilesystemError(f"File has no extractable records: {path}")
        expected_size = sum(record.records for record in records) * 128
        data = b"".join(self._read_allocation_block(block, params) for block in blocks)
        return data[:expected_size]

    def metadata(self) -> Dict[str, Any]:
        return {
            "filesystem": self._variant,
            "probed": self._probed,
            "entries": len(self._records),
        }
