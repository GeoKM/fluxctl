"""CBM DOS command-channel errors inferable from decoded sector evidence.

These are read-only image diagnostics, not claims about the original drive's
controller status. A sector image does not retain every header/controller
condition needed to distinguish all CBM DOS errors.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .sector.models import Sector


CBM_DOS_ERROR_MESSAGES: dict[int, str] = {
    20: "READ ERROR (block header not found)",
    21: "READ ERROR (no sync character)",
    22: "READ ERROR (data block not present)",
    23: "READ ERROR (checksum error in data block)",
    24: "READ ERROR (byte decoding error)",
    25: "WRITE ERROR (write-verify error)",
    26: "WRITE PROTECT ON",
    27: "READ ERROR (checksum error in header)",
    28: "WRITE ERROR (long data block)",
    29: "DISK ID MISMATCH",
}


@dataclass(frozen=True)
class CBMDosError:
    """An inferred CBM DOS error attached to a decoded sector."""

    code: int
    message: str
    basis: str


def cbm_dos_error_for_sector(
    sector: Optional[Sector],
    *,
    data_block_missing: bool = False,
) -> Optional[CBMDosError]:
    """Map available sector evidence to a conservative CBM DOS error.

    ``22`` is used when the sector ID is expected but no data block was
    recovered. ``23`` is used when data was recovered but its sector CRC is
    invalid. Header CRC, sync, byte-decoding, write, and disk-ID conditions
    are not represented by the current sector model and are not guessed.
    """

    if data_block_missing or sector is None or not sector.data:
        return CBMDosError(
            22,
            CBM_DOS_ERROR_MESSAGES[22],
            "expected sector ID present but no data block was recovered",
        )
    if not sector.crc_ok:
        return CBMDosError(
            23,
            CBM_DOS_ERROR_MESSAGES[23],
            "decoded data is present but its data checksum is invalid",
        )
    return None


def is_cbm_dos_layout(layout_id: str | None) -> bool:
    """Return whether a layout uses a Commodore CBM DOS physical format."""

    return bool(layout_id and layout_id.startswith("commodore_"))


__all__ = [
    "CBM_DOS_ERROR_MESSAGES",
    "CBMDosError",
    "cbm_dos_error_for_sector",
    "is_cbm_dos_layout",
]
