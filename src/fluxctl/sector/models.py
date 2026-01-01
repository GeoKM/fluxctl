"""Sector-level data models used during reconstruction and export."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class Sector:
    """Decoded sector data and metadata.

    ``size_code`` follows the IBM convention where ``0`` represents 128 bytes,
    ``1`` is 256 bytes, ``2`` is 512 bytes, ``3`` is 1024 bytes, and so on.
    """

    cylinder: int
    head: int
    sector_id: int
    size_code: int
    data: bytes
    crc_ok: bool
    confidence: float
    deleted: bool = False
    source_revolutions: List[int] = field(default_factory=list)

    @property
    def size(self) -> int:
        """Return the sector length in bytes using the size code hint."""

        expected = 128 << self.size_code if self.size_code >= 0 else 0
        return len(self.data) if self.data else expected

    @property
    def track(self) -> int:  # backward compatibility
        return self.cylinder

    @property
    def side(self) -> int:  # backward compatibility
        return self.head

    @property
    def state(self) -> str:
        """Lightweight health classification for mapping/reporting."""

        if not self.data:
            return "missing"
        if not self.crc_ok:
            return "bad"
        return "good"


@dataclass
class TrackSectors:
    track: int
    head: int
    sectors: List[Sector]
    weak: int = 0
    missing: int = 0

    @property
    def side(self) -> int:  # backward compatibility
        return self.head
