"""Conservative inspection of Wang VS system-volume VTOC blocks.

The package-catalog reader in :mod:`wang_ois` is a different format.  Wang
system volumes use a four-part VTOC in 2 KiB blocks (FDAV, FDX1, FDX2, FDR),
but the field offsets and pointer encodings are not interchangeable with the
package catalog.  This module therefore reports only control-block evidence
that can be verified directly; it does not turn embedded utility strings into
files or invent file extents.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from ..exceptions import FilesystemError
from . import SectorImage


WANG_BLOCK_SIZE = 2048
WANG_VTOC_START_BLOCK = 4
WANG_VTOC_KINDS = ("FDAV", "FDX1", "FDX2", "FDR1")


@dataclass(frozen=True, slots=True)
class WangVTOCBlock:
    """A VTOC control-block signature found in a logical 2 KiB block."""

    block: int
    kind: str
    offset: int
    signature: bytes


@dataclass(frozen=True, slots=True)
class WangVTOCInspection:
    """Evidence returned by :func:`inspect_wang_vtoc`."""

    block_size: int
    vtoc_start_block: int
    blocks_scanned: int
    control_blocks: tuple[WangVTOCBlock, ...]
    missing_kinds: tuple[str, ...]
    file_extents_modelled: bool = False

    @property
    def recognised(self) -> bool:
        return not self.missing_kinds

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": "wang_vs_vtoc",
            "block_size": self.block_size,
            "vtoc_start_block": self.vtoc_start_block,
            "blocks_scanned": self.blocks_scanned,
            "recognised": self.recognised,
            "file_extents_modelled": self.file_extents_modelled,
            "missing_kinds": list(self.missing_kinds),
            "control_blocks": [
                {"block": item.block, "kind": item.kind, "offset": item.offset}
                for item in self.control_blocks
            ],
        }


def _iter_blocks(image: SectorImage) -> Iterable[bytes]:
    """Yield logical 2 KiB blocks without assuming a filesystem layout."""

    sectors = list(image.iter_sectors())
    if not sectors:
        return
    if any(len(sector) != 256 for sector in sectors):
        raise FilesystemError("Wang VTOC inspection requires 256-byte logical sectors")
    for start in range(0, len(sectors), 8):
        block = sectors[start : start + 8]
        if len(block) == 8:
            yield b"".join(block)


def inspect_wang_vtoc(image: SectorImage) -> WangVTOCInspection:
    """Inspect the documented Wang system-volume VTOC location.

    This deliberately requires the four control-block IDs in the first four
    VTOC blocks.  A scan of arbitrary utility text is not sufficient evidence
    because system/software disks can contain strings such as ``CREATE VTOC
    ENTRY`` without carrying a usable VTOC.
    """

    blocks = list(_iter_blocks(image))
    found: list[WangVTOCBlock] = []
    for relative, block in enumerate(blocks[WANG_VTOC_START_BLOCK : WANG_VTOC_START_BLOCK + 4]):
        absolute = WANG_VTOC_START_BLOCK + relative
        for kind in WANG_VTOC_KINDS:
            signature = kind.encode("ascii")
            offset = block.find(signature)
            if offset >= 0:
                found.append(WangVTOCBlock(absolute, kind, offset, signature))
                break
    found_kinds = {item.kind for item in found}
    return WangVTOCInspection(
        block_size=WANG_BLOCK_SIZE,
        vtoc_start_block=WANG_VTOC_START_BLOCK,
        blocks_scanned=max(0, len(blocks) - WANG_VTOC_START_BLOCK),
        control_blocks=tuple(found),
        missing_kinds=tuple(kind for kind in WANG_VTOC_KINDS if kind not in found_kinds),
    )

