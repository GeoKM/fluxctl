"""Read-only Wang OIS package-disk catalog support."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from ..exceptions import FilesystemError
from . import FileEntry, Filesystem, SectorImage


_SECTOR_SIZE = 256
_SECTORS_PER_TRACK = 16
_ALLOCATION_BLOCK_SIZE = 1024
_IMAGE_SIZE = 77 * _SECTORS_PER_TRACK * _SECTOR_SIZE
_CATALOG_RECORD_SIZE = 48


@dataclass(slots=True)
class WangOISCatalogEntry:
    """One node from an OIS package disk's hierarchical catalog."""

    name: str
    is_dir: bool
    catalog_sector: int
    catalog_offset: int
    start_block: int = 0
    sector_count: int = 0
    bytes_in_last_sector: int = 0
    has_prologue: bool = False
    children: list["WangOISCatalogEntry"] = field(default_factory=list)

    @property
    def size(self) -> int:
        if self.is_dir or self.sector_count <= 0:
            return 0
        final_bytes = self.bytes_in_last_sector or _SECTOR_SIZE
        return (self.sector_count - 1) * _SECTOR_SIZE + final_bytes


class WangOISFilesystem(Filesystem):
    """Read OIS installation-package catalogs from 315K Wang media.

    The catalog stores a tree of 48-byte node records. File extents begin on
    1 KiB allocation-block boundaries, while EOF length and the final-sector
    byte count are expressed in 256-byte sectors. Some files have one separate
    prologue sector immediately before their logical contents.
    """

    def __init__(self) -> None:
        self.image: Optional[SectorImage] = None
        self._data = b""
        self._catalog_block = 0
        self._catalog_base = 0
        self._catalog_pointer_unit = "allocation_block"
        self._roots: list[WangOISCatalogEntry] = []
        self._paths: dict[str, WangOISCatalogEntry] = {}
        self._package_id = ""

    @staticmethod
    def quick_probe(image: SectorImage) -> bool:
        """Check only the Wang header and catalog prologue.

        This deliberately avoids materialising the complete image. Generic
        filesystem detection calls this method before the full catalog parse,
        which matters for repeated probes of reconstructed SCP captures.
        """

        cached = getattr(image, "_wang_ois_quick_probe", None)
        if cached is not None:
            return bool(cached)
        if getattr(image, "bytes_per_sector", 0) != _SECTOR_SIZE:
            try:
                setattr(image, "_wang_ois_quick_probe", False)
            except (AttributeError, TypeError):
                pass
            return False
        try:
            header = image.read_sector(0)
            if len(header) < 24:
                return False
            catalog_block = int.from_bytes(header[22:24], "little")
            if catalog_block <= 0:
                return False
            for multiplier in (4, 1, 8):
                catalog_lba = catalog_block * multiplier
                try:
                    catalog_sector = image.read_sector(catalog_lba)
                except Exception:
                    continue
                if len(catalog_sector) >= 8 and catalog_sector[1:8] == b"Catalog":
                    try:
                        setattr(image, "_wang_ois_quick_probe", True)
                    except (AttributeError, TypeError):
                        pass
                    return True
        except Exception:
            try:
                setattr(image, "_wang_ois_quick_probe", False)
            except (AttributeError, TypeError):
                pass
            return False
        try:
            setattr(image, "_wang_ois_quick_probe", False)
        except (AttributeError, TypeError):
            pass
        return False

    def probe(self, image: SectorImage) -> bool:
        self.image = None
        self._data = b""
        self._roots = []
        self._paths = {}
        self._package_id = ""
        self._catalog_pointer_unit = "allocation_block"
        if not self.quick_probe(image):
            return False
        try:
            data = b"".join(image.iter_sectors())
        except Exception:
            return False
        if len(data) != _IMAGE_SIZE:
            return False

        # OIS package headers store the catalog allocation block at bytes
        # 22-23. The catalog extent itself begins with a typed "Catalog"
        # prologue, which prevents a same-sized unrelated image from matching.
        catalog_block = int.from_bytes(data[22:24], "little")
        catalog_base = 0
        for unit, multiplier in (
            ("allocation_block", _ALLOCATION_BLOCK_SIZE),
            ("sector", _SECTOR_SIZE),
            ("double_allocation_block", _ALLOCATION_BLOCK_SIZE * 2),
        ):
            candidate_base = catalog_block * multiplier
            if (
                0 < candidate_base <= len(data) - _CATALOG_RECORD_SIZE
                and data[candidate_base + 1 : candidate_base + 8] == b"Catalog"
            ):
                catalog_base = candidate_base
                self._catalog_pointer_unit = unit
                break
        if not catalog_base:
            return False

        self._data = data
        self._catalog_block = catalog_block
        self._catalog_base = catalog_base
        try:
            # The catalog header holds the first root-node pointer.  Earlier
            # package disks happen to use (0, 48), but later OIS releases may
            # start their tree at another record in the catalog extent.
            catalog_header = data[catalog_base : catalog_base + _CATALOG_RECORD_SIZE]
            root_pointer = (catalog_header[31], catalog_header[34])
            if root_pointer == (0, 0):
                raise FilesystemError("Wang OIS package catalog has no root pointer")
            roots = self._read_sibling_chain(*root_pointer, set())
            if not roots or not any(entry.is_dir for entry in roots):
                raise FilesystemError("Wang OIS package catalog has no root directory nodes")
            self._roots = roots
            self._index_paths(self._roots, "")
        except FilesystemError:
            self._data = b""
            self._roots = []
            self._paths = {}
            return False

        raw_package = data[:8].rstrip(b"\x00")
        if raw_package and all(32 <= byte < 127 for byte in raw_package):
            self._package_id = raw_package.decode("ascii")
        self.image = image
        return True

    def list_directory(self, path: str = "/") -> list[FileEntry]:
        normalised = self._normalise_path(path)
        if normalised == "/":
            entries = self._roots
        else:
            entry = self._paths.get(normalised.casefold())
            if entry is None:
                raise FilesystemError(f"Wang OIS path not found: {path}")
            if not entry.is_dir:
                raise FilesystemError(f"Wang OIS path is not a directory: {path}")
            entries = entry.children
        return [
            FileEntry(
                name=entry.name,
                is_dir=entry.is_dir,
                size=entry.size,
                cluster_start=entry.start_block * 4,
                attributes=1 if entry.has_prologue else 0,
            )
            for entry in entries
        ]

    def extract_file(self, path: str) -> bytes:
        entry = self._file_entry(path)
        start = entry.start_block * _ALLOCATION_BLOCK_SIZE
        if entry.has_prologue:
            start += _SECTOR_SIZE
        end = start + entry.size
        if start < 0 or end > len(self._data):
            raise FilesystemError(f"Wang OIS file extent is outside the image: {path}")
        return self._data[start:end]

    def file_sector_addresses(self, path: str) -> set[tuple[int, int, int]]:
        entry = self._file_entry(path)
        first_lba = entry.start_block * (_ALLOCATION_BLOCK_SIZE // _SECTOR_SIZE)
        count = entry.sector_count + (1 if entry.has_prologue else 0)
        return {
            (lba // _SECTORS_PER_TRACK, 0, lba % _SECTORS_PER_TRACK)
            for lba in range(first_lba, first_lba + count)
        }

    def metadata(self) -> Dict[str, Any]:
        files = [entry for entry in self._paths.values() if not entry.is_dir]
        return {
            "filesystem": "wang_ois",
            "package_id": self._package_id,
            "catalog_allocation_block": self._catalog_block,
            "catalog_pointer_unit": self._catalog_pointer_unit,
            "catalog_entries": len(self._paths),
            "files": len(files),
            "read_only": True,
        }

    def _read_sibling_chain(
        self,
        sector: int,
        offset: int,
        ancestry: set[tuple[int, int]],
    ) -> list[WangOISCatalogEntry]:
        entries: list[WangOISCatalogEntry] = []
        seen = set(ancestry)
        pointer = (sector, offset)
        while pointer != (0, 0):
            if pointer in seen:
                raise FilesystemError("Wang OIS catalog pointer loop detected")
            seen.add(pointer)
            record = self._catalog_record(*pointer)
            if all(byte in {0x00, 0x80} for byte in record[:16]):
                # Catalog chains use an empty record as their terminator.
                break
            entry = self._parse_record(record, *pointer)
            if entry.is_dir:
                child = (record[27], record[29])
                if child != (0, 0):
                    entry.children = self._read_sibling_chain(*child, seen)
            entries.append(entry)
            pointer = (record[31], record[34])
        return entries

    def _catalog_record(self, sector: int, offset: int) -> bytes:
        if sector < 0 or not 0 <= offset <= _SECTOR_SIZE - _CATALOG_RECORD_SIZE:
            raise FilesystemError("Wang OIS catalog pointer is out of range")
        if offset % _CATALOG_RECORD_SIZE:
            raise FilesystemError("Wang OIS catalog pointer is not record-aligned")
        start = self._catalog_base + sector * _SECTOR_SIZE + offset
        end = start + _CATALOG_RECORD_SIZE
        if end > len(self._data):
            raise FilesystemError("Wang OIS catalog record exceeds the image")
        return self._data[start:end]

    def _parse_record(self, record: bytes, sector: int, offset: int) -> WangOISCatalogEntry:
        raw_name = record[:16].split(b"\x00", 1)[0]
        if not raw_name or not all(32 <= byte < 127 for byte in raw_name):
            raise FilesystemError("Wang OIS catalog contains an invalid node name")
        name = raw_name.decode("ascii")
        node_type = record[26]
        if node_type not in {0, 1}:
            raise FilesystemError("Wang OIS catalog contains an unknown node type")
        is_dir = node_type == 0
        entry = WangOISCatalogEntry(
            name=name,
            is_dir=is_dir,
            catalog_sector=sector,
            catalog_offset=offset,
        )
        if not is_dir:
            entry.start_block = record[27]
            entry.sector_count = record[29]
            entry.bytes_in_last_sector = record[37]
            entry.has_prologue = bool(record[39] & 0x01)
            if entry.bytes_in_last_sector > _SECTOR_SIZE:
                raise FilesystemError(f"Wang OIS file {name} has an invalid extent")
            start = entry.start_block * _ALLOCATION_BLOCK_SIZE
            allocation = (entry.sector_count + int(entry.has_prologue)) * _SECTOR_SIZE
            if start + allocation > len(self._data):
                raise FilesystemError(f"Wang OIS file {name} exceeds the image")
        return entry

    def _index_paths(self, entries: list[WangOISCatalogEntry], parent: str) -> None:
        for entry in entries:
            path = self._normalise_path(f"{parent}/{entry.name}")
            key = path.casefold()
            if key in self._paths:
                raise FilesystemError(f"Duplicate Wang OIS catalog path: {path}")
            self._paths[key] = entry
            if entry.is_dir:
                self._index_paths(entry.children, path)

    def _file_entry(self, path: str) -> WangOISCatalogEntry:
        normalised = self._normalise_path(path)
        entry = self._paths.get(normalised.casefold())
        if entry is None:
            raise FilesystemError(f"Wang OIS file not found: {path}")
        if entry.is_dir:
            raise FilesystemError(f"Wang OIS path is a directory: {path}")
        return entry

    @staticmethod
    def _normalise_path(path: str) -> str:
        if not path or path == "/":
            return "/"
        return "/" + "/".join(part for part in path.split("/") if part)
