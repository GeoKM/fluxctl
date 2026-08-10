"""RT-11 filesystem probe and minimal metadata reader."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..exceptions import FilesystemError
from . import FileEntry, Filesystem, SectorImage


RAD50_TABLE = " ABCDEFGHIJKLMNOPQRSTUVWXYZ$.%0123456789"


@dataclass(frozen=True)
class RT11InterchangeDataset:
    """One active IBM 3740 interchange HDR1 dataset label."""

    name: str
    record_length: int
    start_address: str
    end_address: str
    next_address: str
    label_sector: int

    @property
    def start_chs(self) -> tuple[int, int]:
        return int(self.start_address[:2]), int(self.start_address[2:])

    @property
    def end_chs(self) -> tuple[int, int]:
        return int(self.end_address[:2]), int(self.end_address[2:])

    @property
    def next_chs(self) -> tuple[int, int]:
        return int(self.next_address[:2]), int(self.next_address[2:])


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


class RT11InterchangeFilesystem(Filesystem):
    """Read IBM 3740/RX01 EBCDIC labels used by RT-11 interchange media.

    This is deliberately separate from RT-11's normal 512-byte block
    filesystem. Interchange disks use 128-byte physical sectors and track-zero
    HDR1 labels; a label can describe an empty dataset even when old content
    remains in the allocated extent.
    """

    _HDR1_RE = re.compile(
        r"^HDR1\s*(?P<name>.{1,17}?)\s+(?P<record_length>\d{3})\s+"
        r"(?P<start>\d{5})\s+(?P<end>\d{5}).*?(?P<next>\d{5})\s*$"
    )

    def __init__(self) -> None:
        self.datasets: list[RT11InterchangeDataset] = []
        self.volume_id = ""
        self.image: SectorImage | None = None

    def probe(self, image: SectorImage) -> bool:
        self.datasets = []
        self.volume_id = ""
        self.image = None
        if getattr(image, "bytes_per_sector", 0) != 128:
            return False

        labels: list[tuple[int, str]] = []
        for sector in range(26):
            try:
                data = image.read_sector(sector)
            except FilesystemError:
                continue
            text = data[:80].decode("cp037", errors="replace").replace("\x00", " ").rstrip()
            if text.startswith(("VOL1", "HDR1", "EOF1", "DDR1")):
                labels.append((sector, text))

        if not any(text.startswith("HDR1") for _sector, text in labels):
            return False
        for _sector, text in labels:
            if text.startswith("VOL1"):
                candidate = text[4:10].strip()
                if candidate:
                    self.volume_id = candidate
                continue
            match = self._HDR1_RE.match(text)
            if not match:
                continue
            self.datasets.append(
                RT11InterchangeDataset(
                    name=match.group("name").strip(),
                    record_length=int(match.group("record_length")),
                    start_address=match.group("start"),
                    end_address=match.group("end"),
                    next_address=match.group("next"),
                    label_sector=_sector,
                )
            )
        self.image = image
        return bool(self.datasets)

    def list_directory(self, path: str = "/") -> List[FileEntry]:
        if path not in {"", "/"}:
            raise FilesystemError("RT-11 interchange labels have no directories")
        entries = [
            FileEntry(
                name=dataset.name,
                is_dir=False,
                size=self._logical_size(dataset),
                cluster_start=dataset.label_sector,
            )
            for dataset in self.datasets
        ]
        for dataset in self.datasets:
            if not self._is_empty(dataset):
                continue
            entries.extend(
                [
                    FileEntry(
                        name=f"{dataset.name}.RESIDUAL.RAW",
                        is_dir=False,
                        size=self._allocated_sector_count(dataset) * 128,
                        cluster_start=dataset.label_sector,
                    ),
                    FileEntry(
                        name=f"{dataset.name}.RESIDUAL.json",
                        is_dir=False,
                        size=0,
                        cluster_start=dataset.label_sector,
                    ),
                ]
            )
        return entries

    def extract_file(self, path: str) -> bytes:
        name = path.strip("/")
        upper_name = name.upper()
        for dataset in self.datasets:
            if upper_name == f"{dataset.name.upper()}.RESIDUAL.RAW":
                payload, _missing = self._read_residual_extent(dataset)
                return payload
            if upper_name == f"{dataset.name.upper()}.RESIDUAL.JSON":
                _payload, missing = self._read_residual_extent(dataset)
                return self._residual_manifest(dataset, missing)

        dataset = next((entry for entry in self.datasets if entry.name.upper() == upper_name), None)
        if dataset is None:
            raise FilesystemError(f"RT-11 interchange dataset not found: {path}")
        if self._is_empty(dataset):
            raise FilesystemError(
                f"RT-11 interchange dataset {dataset.name} is labelled empty "
                f"(next-unused address {dataset.next_address}); export "
                f"{dataset.name}.RESIDUAL.RAW only for forensic recovery"
            )
        return self._read_logical_records(dataset)

    def metadata(self) -> Dict[str, Any]:
        label = self.volume_id or "IBM 3740/RX01 RT-11 Interchange"
        return {
            "filesystem": "rt11_interchange",
            "label": label,
            "datasets": str(len(self.datasets)),
            "logical_export": True,
            "empty_dataset_recovery_export": True,
        }

    @staticmethod
    def _address_index(address: tuple[int, int]) -> int:
        track, sector = address
        if track < 1 or sector < 1 or sector > 26:
            raise FilesystemError(f"Invalid RT-11 interchange address {track:02d}{sector:03d}")
        return (track - 1) * 26 + (sector - 1)

    @staticmethod
    def _chs_for_index(index: int) -> tuple[int, int]:
        return index // 26 + 1, index % 26 + 1

    def _is_empty(self, dataset: RT11InterchangeDataset) -> bool:
        return dataset.next_address == dataset.start_address

    def _allocated_sector_count(self, dataset: RT11InterchangeDataset) -> int:
        start = self._address_index(dataset.start_chs)
        end = self._address_index(dataset.end_chs)
        if end < start:
            raise FilesystemError(f"RT-11 interchange dataset {dataset.name} has an invalid reserved extent")
        return end - start + 1

    def _record_sector_count(self, dataset: RT11InterchangeDataset) -> int:
        start = self._address_index(dataset.start_chs)
        next_unused = self._address_index(dataset.next_chs)
        end = self._address_index(dataset.end_chs)
        if next_unused < start or next_unused > end + 1:
            raise FilesystemError(f"RT-11 interchange dataset {dataset.name} has an invalid next-unused address")
        return next_unused - start

    def _logical_size(self, dataset: RT11InterchangeDataset) -> int:
        if dataset.record_length < 1 or dataset.record_length > 128:
            return 0
        try:
            return self._record_sector_count(dataset) * dataset.record_length
        except FilesystemError:
            return 0

    def _read_sector_by_chs(self, track: int, sector: int) -> bytes:
        if self.image is None:
            raise FilesystemError("RT-11 interchange filesystem has not been probed")
        lba = (track * 26) + (sector - 1)
        data = self.image.read_sector(lba)
        if len(data) < 128:
            raise FilesystemError(f"RT-11 interchange sector {track:02d}/{sector:02d} is truncated")
        return data[:128]

    def _read_logical_records(self, dataset: RT11InterchangeDataset) -> bytes:
        if dataset.record_length < 1 or dataset.record_length > 128:
            raise FilesystemError(f"RT-11 interchange dataset {dataset.name} has an invalid record length")
        start = self._address_index(dataset.start_chs)
        count = self._record_sector_count(dataset)
        records: list[bytes] = []
        for offset in range(count):
            track, sector = self._chs_for_index(start + offset)
            records.append(self._read_sector_by_chs(track, sector)[: dataset.record_length])
        return b"".join(records)

    def _read_residual_extent(self, dataset: RT11InterchangeDataset) -> tuple[bytes, list[dict[str, int]]]:
        start = self._address_index(dataset.start_chs)
        count = self._allocated_sector_count(dataset)
        payload: list[bytes] = []
        missing: list[dict[str, int]] = []
        for offset in range(count):
            track, sector = self._chs_for_index(start + offset)
            try:
                payload.append(self._read_sector_by_chs(track, sector))
            except FilesystemError:
                payload.append(b"\x00" * 128)
                missing.append({"track": track, "sector": sector})
        return b"".join(payload), missing

    def _residual_manifest(self, dataset: RT11InterchangeDataset, missing: list[dict[str, int]]) -> bytes:
        return (json.dumps(
            {
                "dataset": dataset.name,
                "status": "labelled_empty_residual_extent",
                "record_length": dataset.record_length,
                "start": dataset.start_address,
                "end": dataset.end_address,
                "next_unused": dataset.next_address,
                "sector_bytes": 128,
                "missing_sectors_zero_filled": missing,
            },
            indent=2,
            sort_keys=True,
        ) + "\n").encode("utf-8")
