"""Structured hints about expected disk geometry."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass(frozen=True)
class LayoutHint:
    """Helper that carries externally sourced geometry hints."""

    tracks: Optional[int] = None
    sides: Optional[int] = None
    sectors_per_track: Optional[int] = None
    sector_size: Optional[int] = None
    interface: Optional[str] = None
    loader: Optional[str] = None
    total_size: Optional[int] = None
    total_sectors: Optional[int] = None
    metadata: Dict[str, str] = field(default_factory=dict)
