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
    from .d71 import D71Exporter
    from .d81 import D81Exporter
    from .g64 import G64Exporter
    from .apple2 import Apple2DOExporter, Apple2POExporter
    from .scp import SCPExporter

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
            description="Commodore 1541 disk image (.d64)",
        ),
    )
    registry.register_exporter(
        "d71",
        PluginInfo(
            name="D71 exporter",
            version="0.1",
            entry=D71Exporter(),
            description="Commodore 1571 disk image (.d71)",
        ),
    )
    registry.register_exporter(
        "g64",
        PluginInfo(
            name="G64 exporter",
            version="0.1",
            entry=G64Exporter(),
            description="Commodore 1541 GCR nibble image (.g64)",
        ),
    )
    registry.register_exporter(
        "po",
        PluginInfo(
            name="Apple II ProDOS-order exporter",
            version="0.1",
            entry=Apple2POExporter(),
            description="Apple II ProDOS-order sector image (.po)",
        ),
    )
    registry.register_exporter(
        "do",
        PluginInfo(
            name="Apple II DOS-order exporter",
            version="0.1",
            entry=Apple2DOExporter(),
            description="Apple II DOS-order sector image (.do)",
        ),
    )
    registry.register_exporter(
        "d81",
        PluginInfo(
            name="D81 exporter",
            version="0.1",
            entry=D81Exporter(),
            description="Commodore 1581 disk image (.d81)",
        ),
    )
    registry.register_exporter(
        "scp",
        PluginInfo(
            name="Native SCP exporter",
            version="0.1",
            entry=SCPExporter(),
            description="Synthetic logical-flux SuperCard Pro image (.scp)",
        ),
    )
    return list(registry.exporter.values())


__all__ = ["Exporter", "load_builtin_exporters"]
