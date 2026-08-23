"""Blank image creation operations shared by Fluxctl frontends."""
from __future__ import annotations

from pathlib import Path


def _services():
    from .. import studio_services

    return studio_services


def blank_image_presets():
    return _services()._legacy_blank_image_presets()


def create_blank_image(preset_id: str, output_path: Path, *, overwrite: bool = False):
    return _services()._legacy_create_blank_image(preset_id, output_path, overwrite=overwrite)
