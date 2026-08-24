"""Filesystem abstractions and helpers.

This module defines lightweight interfaces for filesystem plugins as well as
utility wrappers for accessing reconstructed sector data. Filesystem plugins are
expected to follow the :class:`Filesystem` protocol and can be registered with
:mod:`fluxctl.plugins` for discovery by the CLI.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Protocol, Sequence, Tuple

from ..exceptions import FilesystemError
from ..plugins import PluginInfo, registry
from ..sector.models import TrackSectors


class SectorImage(Protocol):
    """Protocol for objects that can expose sector-addressable storage."""

    bytes_per_sector: int

    def read_sector(self, lba: int, count: int = 1) -> bytes:
        """Return the bytes for ``count`` sectors starting at ``lba``."""

    def iter_sectors(self) -> Iterable[bytes]:
        """Iterate over available sectors in logical order."""


class Filesystem(Protocol):
    """Minimal interface implemented by filesystem plugins."""

    def probe(self, image: SectorImage) -> bool:
        """Return ``True`` if the filesystem recognises ``image`` and is ready."""

    def list_directory(self, path: str = "/") -> List["FileEntry"]:
        """Return entries for the directory at ``path``."""

    def extract_file(self, path: str) -> bytes:
        """Return the file contents located at ``path``."""

    def metadata(self) -> Dict[str, Any]:
        """Return implementation-defined metadata about the mounted image."""


@dataclass(slots=True)
class FileEntry:
    """Basic directory entry information returned by filesystem plugins."""

    name: str
    is_dir: bool
    size: int
    cluster_start: int
    modified: Optional[str] = None
    attributes: Optional[int] = None


class RawSectorImage:
    """Sector access wrapper for flat disk images."""

    def __init__(self, data: bytes, bytes_per_sector: Optional[int] = 512):
        self.data = data
        self.bytes_per_sector = bytes_per_sector or 512
        self.total_sectors = len(data) // self.bytes_per_sector

    def read_sector(self, lba: int, count: int = 1) -> bytes:
        start = lba * self.bytes_per_sector
        end = start + count * self.bytes_per_sector
        if end > len(self.data):
            raise FilesystemError("Requested sector range exceeds image size")
        return self.data[start:end]

    def iter_sectors(self) -> Iterable[bytes]:  # pragma: no cover - simple generator
        for idx in range(self.total_sectors):
            yield self.read_sector(idx)


class TrackSectorImage:
    """Sector access wrapper backed by reconstructed :class:`TrackSectors` data."""

    def __init__(self, tracks: Sequence[TrackSectors], bytes_per_sector: Optional[int] = None):
        self.tracks = list(tracks)
        self.bytes_per_sector = bytes_per_sector or self._infer_sector_size()
        self._geometry: Optional[Tuple[int, int, int]] = None
        self._sector_lookup = self._build_lookup()

    def _infer_sector_size(self) -> int:
        for ts in self.tracks:
            for sector in ts.sectors:
                return sector.size
        raise FilesystemError("No sector data available to infer size")

    def _build_lookup(self) -> Dict[Tuple[int, int, int], bytes]:
        lookup: Dict[Tuple[int, int, int], bytes] = {}
        for ts in self.tracks:
            for sector in ts.sectors:
                lookup[(ts.track, ts.head, sector.sector_id)] = sector.data
        if not lookup:
            raise FilesystemError("No sectors available for filesystem access")
        return lookup

    def set_geometry(self, sectors_per_track: int, heads: int, sector_base: int = 1) -> None:
        self._geometry = (sectors_per_track, heads, sector_base)

    def _chs_for_lba(self, lba: int) -> Tuple[int, int, int]:
        if self._geometry is None:
            keys = sorted(self._sector_lookup.keys())
            if lba < 0 or lba >= len(keys):
                raise FilesystemError("Geometry unknown; LBA exceeds available sectors")
            return keys[lba]
        sectors_per_track, heads, sector_base = self._geometry
        track = lba // (sectors_per_track * heads)
        rem = lba % (sectors_per_track * heads)
        head = rem // sectors_per_track
        sector_id = (rem % sectors_per_track) + sector_base
        return (track, head, sector_id)

    def read_sector(self, lba: int, count: int = 1) -> bytes:
        segments: List[bytes] = []
        for offset in range(count):
            chs = self._chs_for_lba(lba + offset)
            try:
                sector_data = self._sector_lookup[chs]
            except KeyError as exc:
                raise FilesystemError(f"Sector {chs} not available in reconstructed data") from exc
            segments.append(sector_data)
        return b"".join(segments)

    def iter_sectors(self) -> Iterable[bytes]:  # pragma: no cover - integration helper
        for key in sorted(self._sector_lookup.keys()):
            yield self._sector_lookup[key]


def load_builtin_filesystems() -> List[PluginInfo]:
    """Register bundled filesystem plugins and return them."""

    if registry.filesystem:
        return list(registry.filesystem.values())

    from .fat12 import FAT12
    from .amiga import AmigaOFS
    from .cbm_dos import CBMDOS
    from .cbm_dos_1581 import CBMDOS1581
    from .cpm import CPMFilesystem
    from .rt11 import RT11Filesystem, RT11InterchangeFilesystem
    from .displaywriter import DisplaywriterFS
    from .seiko_8300 import Seiko8300CPM, Seiko8300EBCDICDataset
    from .trsdos import TRSDOS13Filesystem
    from .newdos80 import NEWDOS80Filesystem
    from .ldos import LDOSTRSDOS6Filesystem
    from .prodos import ProDOSFilesystem
    from .apple_dos import AppleDOS33Filesystem
    from .wang_ois import WangOISFilesystem
    from .xdf import XDF12

    registry.register_filesystem(
        "fat12",
        PluginInfo(
            name="FAT12 Filesystem",
            version="0.1",
            entry=FAT12(),
            description="MS-DOS FAT12 filesystem",
        ),
    )
    registry.register_filesystem(
        "ibm_xdf_fat12",
        PluginInfo(
            name="IBM XDF FAT12 Filesystem",
            version="0.1",
            entry=XDF12(),
            description="Read-only logical FAT12 access for IBM XDF media",
        ),
    )
    registry.register_filesystem(
        "amiga",
        PluginInfo(
            name="Amiga OFS Filesystem",
            version="0.1",
            entry=AmigaOFS(),
            description="Simplified Amiga OFS/FFS reader",
        ),
    )
    registry.register_filesystem(
        "cbm_dos",
        PluginInfo(
            name="CBM DOS Filesystem",
            version="0.1",
            entry=CBMDOS(),
            description="Commodore DOS 2.x filesystem",
        ),
    )
    registry.register_filesystem(
        "cbm_dos_1581",
        PluginInfo(
            name="CBM DOS 1581 Filesystem",
            version="0.1",
            entry=CBMDOS1581(),
            description="Commodore 1581 CBM DOS 10 filesystem",
        ),
    )
    registry.register_filesystem(
        "rt11_interchange",
        PluginInfo(
            name="RT-11 Interchange Labels",
            version="0.1",
            entry=RT11InterchangeFilesystem(),
            description="IBM 3740/RX01 EBCDIC RT-11 interchange label reader",
        ),
    )
    registry.register_filesystem(
        "rt11",
        PluginInfo(
            name="RT-11 Filesystem",
            version="0.1",
            entry=RT11Filesystem(),
            description="DEC RT-11 filesystem probe",
        ),
    )
    registry.register_filesystem(
        "displaywriter",
        PluginInfo(
            name="Displaywriter Filesystem",
            version="0.1",
            entry=DisplaywriterFS(),
            description="IBM Displaywriter mixed-sector FM format probe",
        ),
    )
    registry.register_filesystem(
        "seiko_8300_cpm",
        PluginInfo(
            name="Seiko 8300 CP/M Catalog",
            version="0.1",
            entry=Seiko8300CPM(),
            description="Read-only Seiko 8300 EBCDIC CP/M catalog probe",
        ),
    )
    registry.register_filesystem(
        "seiko_8300_ebcdic_dataset",
        PluginInfo(
            name="Seiko EBCDIC Indexed Dataset",
            version="0.1",
            entry=Seiko8300EBCDICDataset(),
            description="Read-only Seiko-family EBCDIC indexed dataset reader",
        ),
    )
    registry.register_filesystem(
        "trsdos",
        PluginInfo(
            name="TRSDOS 1.3 Filesystem",
            version="0.1",
            entry=TRSDOS13Filesystem(),
            description="TRS-80 Model III/4 TRSDOS 1.3 filesystem",
        ),
    )
    registry.register_filesystem(
        "newdos80",
        PluginInfo(
            name="NEWDOS/80 Filesystem",
            version="0.1",
            entry=NEWDOS80Filesystem(),
            description="TRS-80 NEWDOS/80 filesystem",
        ),
    )
    registry.register_filesystem(
        "ldos_trsdos6",
        PluginInfo(
            name="LDOS/TRSDOS 6 Filesystem",
            version="0.1",
            entry=LDOSTRSDOS6Filesystem(),
            description="TRS-80 LDOS 5.x and TRSDOS/LS-DOS 6.x filesystem",
        ),
    )
    registry.register_filesystem(
        "prodos",
        PluginInfo(
            name="Apple ProDOS Filesystem",
            version="0.1",
            entry=ProDOSFilesystem(),
            description="Apple II ProDOS filesystem reader",
        ),
    )
    registry.register_filesystem(
        "apple_dos_3_3",
        PluginInfo(
            name="Apple DOS 3.3 Filesystem",
            version="0.1",
            entry=AppleDOS33Filesystem(),
            description="Apple II DOS 3.3 VTOC and catalog reader",
        ),
    )
    registry.register_filesystem(
        "wang_ois",
        PluginInfo(
            name="Wang OIS Filesystem",
            version="0.1",
            entry=WangOISFilesystem(),
            description="Wang OIS installation-package catalog reader",
        ),
    )
    registry.register_filesystem(
        "cpm",
        PluginInfo(
            name="CP/M Filesystem",
            version="0.1",
            entry=CPMFilesystem(),
            description="CP/M filesystem probe",
        ),
    )
    return list(registry.filesystem.values())


__all__ = [
    "FileEntry",
    "Filesystem",
    "RawSectorImage",
    "SectorImage",
    "TrackSectorImage",
    "load_builtin_filesystems",
]
