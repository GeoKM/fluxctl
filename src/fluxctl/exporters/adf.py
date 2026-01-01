"""Amiga ADF exporter."""
from __future__ import annotations

from typing import Any, Dict

from ..exceptions import ExportError
from ..filesystems import RawSectorImage, TrackSectorImage
from . import Exporter

ADF_SIZE = 901120


class ADFExporter(Exporter):
    """Write reconstructed sectors into a flat Amiga ADF image."""

    extensions = (".adf",)

    def supports(self, image) -> bool:
        if isinstance(image, RawSectorImage):
            return image.bytes_per_sector == 512
        if isinstance(image, TrackSectorImage):
            return image.bytes_per_sector == 512
        return False

    def export(self, image) -> bytes:
        if not self.supports(image):
            raise ExportError("ADF exporter requires 512-byte sectors")
        payload = b"".join(image.iter_sectors())
        if len(payload) < ADF_SIZE:
            payload = payload.ljust(ADF_SIZE, b"\x00")
        return payload[:ADF_SIZE]

    def metadata(self) -> Dict[str, Any]:
        return {"name": "ADF exporter", "version": "0.1", "adf_size": ADF_SIZE}
