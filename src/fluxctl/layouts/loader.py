"""Layout descriptor loader."""
from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Dict, Iterable, List

from ..exceptions import LayoutNotFoundError
from ..models import LayoutDescriptor
from ..plugins import registry


def load_layout_descriptor(path: Path) -> LayoutDescriptor:
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    return LayoutDescriptor(
        schema_version=data["schema_version"],
        layout_id=data["layout_id"],
        name=data["name"],
        encoding=data["encoding"],
        rpm_nominal=data["rpm_nominal"],
        sides=data["sides"],
        tracks=data["tracks"],
        sectors_per_track=data["sectors_per_track"],
        sector_size=data["sector_size"],
        gap3_hint=data.get("gap3_hint"),
        id_rules=data["id_rules"],
        crc=data["crc"],
        address_marks=data["address_marks"],
        track_sectors=data.get("track_sectors"),
        sector_sizes=data.get("sector_sizes"),
        track_overrides=data.get("track_overrides"),
    )


def load_builtin_layouts() -> List[LayoutDescriptor]:
    layout_dir = resources.files("fluxctl.data.layouts")
    descriptors: List[LayoutDescriptor] = []
    for path in sorted(layout_dir.iterdir()):
        if path.name.endswith(".json"):
            descriptor = load_layout_descriptor(Path(path))
            registry.register_layout(descriptor.layout_id, descriptor)
            descriptors.append(descriptor)
    return descriptors


def ensure_layout_loaded(layout_id: str) -> LayoutDescriptor:
    if not registry.layout:
        load_builtin_layouts()
    descriptor = registry.get_layout(layout_id)
    if descriptor is None:
        raise LayoutNotFoundError(f"Unknown layout {layout_id}")
    return descriptor
