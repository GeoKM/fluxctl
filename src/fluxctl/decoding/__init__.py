"""Decoder interfaces and helpers."""
from __future__ import annotations

from typing import Protocol

from ..models import Bitstream, RevolutionFlux


class Decoder(Protocol):
    """Protocol for flux decoders that emit bitstreams."""

    def decode_revolution(self, rev: RevolutionFlux) -> Bitstream:  # pragma: no cover - interface
        """Decode a single revolution's flux timings into a bitstream."""


__all__ = ["Decoder"]
