"""Exporter interfaces and builtin registrations.

This module defines the minimal protocol exporters must implement along with a
helper to register the built-in exporter plugins. Exporters are responsible for
transforming reconstructed sector images into standard container formats while
exposing metadata about their capabilities.
"""
from __future__ import annotations

from typing import Any, Dict, Protocol

from ..plugins import PluginInfo, registry


class Exporter(Protocol):
    """Protocol that all exporters must implement."""

    extensions: tuple[str, ...] | list[str]

    def supports(self, image) -> bool:
        """Return ``True`` if the exporter can operate on ``image``."""

    def export(self, image) -> bytes:
        """Return the exported image payload as bytes."""

    def metadata(self) -> Dict[str, Any]:
        """Return exporter-specific metadata such as name and version."""


def load_builtin_exporters() -> list[PluginInfo]:
    """Register bundled exporters and return their plugin metadata."""

    if registry.exporter:
        return list(registry.exporter.values())

    from .raw_img import RawIMGExporter
    from .imd import IMDExporter
    from .adf import ADFExporter
    from .d64 import D64Exporter

    registry.register_exporter(
        "raw",
        PluginInfo(
            name="Raw IMG exporter",
            version="0.1",
            entry=RawIMGExporter(),
            description="Raw sector dump (.img)",
        ),
    )
    registry.register_exporter(
        "imd",
        PluginInfo(
            name="IMD exporter",
            version="0.1",
            entry=IMDExporter(),
            description="ImageDisk format (.imd)",
        ),
    )
    registry.register_exporter(
        "adf",
        PluginInfo(
            name="ADF exporter",
            version="0.1",
            entry=ADFExporter(),
            description="Amiga Disk File exporter",
        ),
    )
    registry.register_exporter(
        "d64",
        PluginInfo(
            name="D64 exporter",
            version="0.1",
            entry=D64Exporter(),
            description="Commodore 1541/1571 disk image (.d64)",
        ),
    )
    return list(registry.exporter.values())


__all__ = ["Exporter", "load_builtin_exporters"]
