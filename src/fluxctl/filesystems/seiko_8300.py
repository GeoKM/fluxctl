"""Read-only catalog support for Seiko 8300 CP/M media."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List

from ..exceptions import FilesystemError
from . import FileEntry, Filesystem, SectorImage, TrackSectorImage


@dataclass(frozen=True, slots=True)
class SeikoCatalogRecord:
    """Decoded fields from one Seiko 8300 catalog record."""

    name: str
    start_offset: int
    end_offset: int
    size: int
    record_flags: int
    field_16_17: int
    field_18_19: int
    version: str


@dataclass(frozen=True, slots=True)
class SeikoDatasetRecord:
    """One Seiko-family EBCDIC indexed-data header."""

    name: str
    track: int
    head: int
    sector_id: int


class Seiko8300CPM(Filesystem):
    """Recognise and list the Seiko 8300's EBCDIC catalog.

    The disk uses a CP/M system but its catalog is not a standard ASCII CP/M
    directory: track 0 contains EBCDIC DDR-style labels and track 1 contains
    32-byte EBCDIC catalog records.  Allocation fields are not yet decoded,
    so this plugin deliberately remains read-only and does not extract data.
    """

    def __init__(self) -> None:
        self._probed = False
        self._entries: List[FileEntry] = []
        self._records: List[SeikoCatalogRecord] = []
        self._label_count = 0

    @staticmethod
    def _track(image: SectorImage, track: int, head: int):
        if not isinstance(image, TrackSectorImage):
            return None
        for candidate in image.tracks:
            if candidate.track == track and candidate.head == head:
                return candidate
        return None

    @staticmethod
    def _track_bytes(image: SectorImage, track: int, head: int) -> bytes:
        sectors = Seiko8300CPM._track(image, track, head)
        if sectors is None:
            return b""
        return b"".join(sector.data for sector in sorted(sectors.sectors, key=lambda item: item.sector_id))

    @staticmethod
    def _catalog_name(record: bytes) -> str:
        return record[1:9].decode("cp037", errors="replace").strip()

    @staticmethod
    def _catalog_record(record: bytes) -> SeikoCatalogRecord | None:
        if len(record) < 32 or record[0] != 0:
            return None
        name = Seiko8300CPM._catalog_name(record)
        if not name or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789#$-_" for char in name):
            return None
        start_offset = int.from_bytes(record[9:11], "little")
        end_offset = int.from_bytes(record[11:13], "little")
        size = int.from_bytes(record[13:15], "little")
        if end_offset < start_offset or end_offset - start_offset != size:
            return None
        return SeikoCatalogRecord(
            name=name,
            start_offset=start_offset,
            end_offset=end_offset,
            size=size,
            record_flags=record[15],
            field_16_17=int.from_bytes(record[16:18], "little"),
            field_18_19=int.from_bytes(record[18:20], "little"),
            version=record[22:26].decode("cp037", errors="replace").strip(),
        )

    def probe(self, image: SectorImage) -> bool:
        label_track = self._track_bytes(image, 0, 1)
        catalog_track = self._track_bytes(image, 1, 0)
        if len(label_track) < 26 * 256 or len(catalog_track) < 26 * 256:
            return False

        labels = [
            label_track[offset : offset + 256].decode("cp037", errors="replace")
            for offset in range(0, len(label_track), 256)
        ]
        ddr_labels = sum(text.startswith("DDR1") for text in labels)
        if ddr_labels < 4:
            return False

        records: list[SeikoCatalogRecord] = []
        for offset in range(0, len(catalog_track) - 31, 32):
            record = catalog_track[offset : offset + 32]
            decoded = self._catalog_record(record)
            if decoded is not None:
                records.append(decoded)
        if len(records) < 8:
            return False

        unique_records = list({record.name: record for record in records}.values())
        self._records = unique_records
        self._entries = [
            FileEntry(name=record.name, is_dir=False, size=0, cluster_start=record.start_offset)
            for record in unique_records
        ]
        self._label_count = ddr_labels
        self._probed = True
        return True

    def list_directory(self, path: str = "/") -> List[FileEntry]:
        if path not in {"", "/"}:
            raise FilesystemError("Seiko 8300 catalog has no decoded subdirectories")
        return list(self._entries)

    def extract_file(self, path: str) -> bytes:
        raise FilesystemError(
            "Seiko 8300 CP/M catalog extraction is not implemented; allocation fields are format-specific"
        )

    def metadata(self) -> Dict[str, Any]:
        offsets_contiguous = bool(self._records) and all(
            previous.end_offset == current.start_offset
            for previous, current in zip(self._records, self._records[1:])
        )
        offsets_monotonic = bool(self._records) and all(
            previous.end_offset <= current.start_offset
            for previous, current in zip(self._records, self._records[1:])
        )
        return {
            "filesystem": "seiko_8300_cpm",
            "probed": self._probed,
            "entries": len(self._entries),
            "track0_ddr1_labels": self._label_count,
            "read_only": True,
            "file_extents_decoded": False,
            "catalog_fields_mapped": True,
            "catalog_extent_units": "unverified catalog-relative bytes",
            "catalog_offsets_contiguous": offsets_contiguous,
            "catalog_offsets_monotonic": offsets_monotonic,
            "allocation_mapping_status": "unproven",
            "allocation_mapping_reason": (
                "Catalog offsets are internally consistent, but their relationship "
                "to physical sectors or CP/M allocation blocks is unvalidated."
            ),
            "file_sizes_verified": False,
            "catalog_records": [
                {
                    "name": record.name,
                    "start_offset": record.start_offset,
                    "end_offset": record.end_offset,
                    "size": record.size,
                    "record_flags": record.record_flags,
                    "field_16_17": record.field_16_17,
                    "field_18_19": record.field_18_19,
                    "version": record.version,
                }
                for record in self._records
            ],
        }


class Seiko8300EBCDICDataset(Filesystem):
    """Recognise Seiko-family EBCDIC indexed datasets.

    Some disks using the Seiko 8300's mixed-density physical format do not
    carry its CP/M catalog. They retain track-0 DDR1 labels and use recurring
    three-sector EBCDIC record groups instead. This reader reports the record
    headers without pretending they are extractable files.
    """

    _HEADER_RE = re.compile(r"^[A-Z0-9]{4}.{0,64}000\{")

    def __init__(self) -> None:
        self._probed = False
        self._records: list[SeikoDatasetRecord] = []
        self._label_count = 0

    @staticmethod
    def _header_name(data: bytes) -> str | None:
        text = data[:96].decode("cp037", errors="replace")
        if not Seiko8300EBCDICDataset._HEADER_RE.match(text):
            return None
        name = text[:32].strip()
        return name if name else None

    def probe(self, image: SectorImage) -> bool:
        self._probed = False
        self._records = []
        self._label_count = 0
        if not isinstance(image, TrackSectorImage):
            return False

        label_track = Seiko8300CPM._track_bytes(image, 0, 1)
        labels = [
            label_track[offset : offset + 256].decode("cp037", errors="replace")
            for offset in range(0, len(label_track), 256)
        ]
        self._label_count = sum(label.startswith("DDR1") for label in labels)
        if self._label_count < 20:
            return False

        records: list[SeikoDatasetRecord] = []
        # The observed data organization places a descriptor at sectors
        # 1, 4, 7, ... of successive MFM track/head rows.
        for track_data in image.tracks:
            if track_data.track < 1:
                continue
            for sector in sorted(track_data.sectors, key=lambda item: item.sector_id):
                if (sector.sector_id - 1) % 3:
                    continue
                name = self._header_name(sector.data)
                if name is None:
                    continue
                records.append(
                    SeikoDatasetRecord(
                        name=name,
                        track=track_data.track,
                        head=track_data.head,
                        sector_id=sector.sector_id,
                    )
                )

        # Require multiple independent headers. A DDR label alone only proves
        # IBM-style labeling, not this Seiko-family indexed organization.
        if len(records) < 8 or len({record.name for record in records}) < 6:
            return False

        self._records = records
        self._probed = True
        return True

    def list_directory(self, path: str = "/") -> List[FileEntry]:
        if path not in {"", "/"}:
            raise FilesystemError("Seiko EBCDIC indexed datasets have no decoded subdirectories")
        return [
            FileEntry(
                name=record.name,
                is_dir=False,
                size=0,
                cluster_start=record.track * 52 + record.head * 26 + record.sector_id - 1,
            )
            for record in self._records
        ]

    def extract_file(self, path: str) -> bytes:
        raise FilesystemError(
            "Seiko EBCDIC indexed-dataset extraction is not implemented; "
            "record extent fields are not decoded"
        )

    def metadata(self) -> Dict[str, Any]:
        return {
            "filesystem": "seiko_8300_ebcdic_dataset",
            "probed": self._probed,
            "read_only": True,
            "track0_ddr1_labels": self._label_count,
            "record_group_sectors": 3,
            "records": len(self._records),
            "file_extents_decoded": False,
            "extraction_supported": False,
            "dataset_records": [
                {
                    "name": record.name,
                    "track": record.track,
                    "head": record.head,
                    "sector_id": record.sector_id,
                }
                for record in self._records
            ],
        }
