"""Decoder interfaces and helpers."""
from __future__ import annotations

from typing import List, Protocol

from ..models import Bitstream, RevolutionFlux
from ..plugins import PluginInfo, registry


class Decoder(Protocol):
    """Protocol for flux decoders that emit bitstreams."""

    def decode_revolution(self, rev: RevolutionFlux) -> Bitstream:  # pragma: no cover - interface
        """Decode a single revolution's flux timings into a bitstream."""


def load_builtin_decoders() -> List[PluginInfo]:
    """Register bundled decoders and return their plugin metadata."""

    # Importing modules triggers decoder registration with the plugin registry.
    from . import mfm as _mfm  # noqa: F401
    from . import gcr as _gcr  # noqa: F401
    from . import apple2 as _apple2  # noqa: F401
    from . import fm as _fm  # noqa: F401

    return list(registry.encoding.values())


__all__ = ["Decoder"]

# Register builtin decoders
from . import gcr  # noqa: E402,F401
from . import apple2  # noqa: E402,F401
