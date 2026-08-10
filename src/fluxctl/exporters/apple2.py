"""Apple II ProDOS-order and DOS-order sector image exporters."""
from __future__ import annotations

from typing import Any

from ..apple2 import APPLE2_DO_ORDER, APPLE2_PO_ORDER, apple2_sector_image_bytes
from ..exceptions import ExportError


class _Apple2SectorExporter:
    order: tuple[int, ...]
    format_name: str
    extensions: tuple[str, ...]

    def supports(self, image) -> bool:
        tracks = getattr(image, "tracks", None)
        layout = getattr(image, "layout", None)
        return bool(tracks) and getattr(layout, "layout_id", "").startswith("apple2_")

    def export(self, image) -> bytes:
        tracks = getattr(image, "tracks", None)
        if not tracks:
            raise ExportError("Apple II exporter requires decoded track sectors")
        return apple2_sector_image_bytes(tracks, self.order)

    def metadata(self) -> dict[str, Any]:
        return {
            "format": self.format_name,
            "name": f"Apple II {self.format_name.upper()} sector image",
            "version": "0.1",
            "extensions": list(self.extensions),
            "sector_order": list(self.order),
            "padded_missing": False,
        }


class Apple2POExporter(_Apple2SectorExporter):
    order = APPLE2_PO_ORDER
    format_name = "po"
    extensions = ("po",)


class Apple2DOExporter(_Apple2SectorExporter):
    order = APPLE2_DO_ORDER
    format_name = "do"
    extensions = ("do",)


__all__ = ["Apple2DOExporter", "Apple2POExporter"]
