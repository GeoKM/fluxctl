"""Minimal CP/M filesystem probe."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from ..exceptions import FilesystemError
from . import FileEntry, Filesystem, SectorImage


_ALLOWED_CHARS = set(b"ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789$%'-_@~`!#()^")


@dataclass(slots=True)
class CPMDirectoryRecord:
    user: int
    name: str
    extent: int
    records: int
    allocation: bytes


def _clean_name_field(field: bytes) -> str:
    return bytes(byte & 0x7F for byte in field).decode("ascii", errors="ignore").rstrip()


def _entry_name(entry: bytes) -> str:
    stem = _clean_name_field(entry[1:9])
    suffix = _clean_name_field(entry[9:12])
    if not stem:
        return ""
    return f"{stem}.{suffix}" if suffix else stem


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


class CPMFilesystem(Filesystem):
    """Lightweight CP/M probe based on directory entry structure."""

    def __init__(self) -> None:
        self._probed = False
        self._records: List[CPMDirectoryRecord] = []
        self._variant = "cpm"

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

    def probe(self, image: SectorImage) -> bool:
        """Heuristic CP/M probe: scan early sectors for directory entries."""

        self._records = self._directory_records(image)
        self._probed = len(self._records) >= 2
        self._variant = self._detect_variant(image)
        return self._probed

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

    def extract_file(self, path: str) -> bytes:
        raise FilesystemError("CP/M file extraction not implemented")

    def metadata(self) -> Dict[str, Any]:
        return {
            "filesystem": self._variant,
            "probed": self._probed,
            "entries": len(self._records),
        }
