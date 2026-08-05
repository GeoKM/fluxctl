"""Helpers for discovering and describing bundled test fixtures."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence

from fluxctl.exceptions import FluxctlError


FIXTURE_EXTENSIONS: Sequence[str] = (".scp", ".img", ".imd", ".dsk", ".dmk", ".d64", ".d71", ".d81", ".adf")


class FixtureDiscoveryError(FluxctlError):
    """Raised when a fixture path cannot be parsed into a descriptor."""


@dataclass(slots=True)
class FixtureDescriptor:
    """Structured metadata parsed from a fixture filename and sidecar file."""

    path: Path
    manufacturer: str
    drive_style: str
    sides_density: str
    encoding: str
    os_name: str
    approx_capacity: str
    metadata: Mapping[str, Any]

    @property
    def stem(self) -> str:  # pragma: no cover - convenience property
        return self.path.stem

    @classmethod
    def from_path(cls, path: Path) -> "FixtureDescriptor":
        manufacturer, drive_style, sides_density, encoding, os_name, approx_capacity = _parse_fixture_stem(path.stem)
        metadata = _load_metadata_for_fixture(path)
        return cls(
            path=path,
            manufacturer=manufacturer,
            drive_style=drive_style,
            sides_density=sides_density,
            encoding=encoding,
            os_name=os_name,
            approx_capacity=approx_capacity,
            metadata=metadata,
        )


def _parse_fixture_stem(stem: str) -> List[str]:
    """Split a fixture stem into its canonical six components.

    Filenames follow the pattern ``<manufacturer>-<DriveStyle>-<SidesDensity>-<Encoding>-<OS>-<ApproxCapacity>``
    where the OS field may itself contain hyphens (e.g. ``MS-DOS``). This helper tolerates that by assuming the
    manufacturer, drive style, sides/density, and encoding occupy the first four segments, the capacity is the final
    segment, and any remaining pieces compose the OS name.
    """

    parts = stem.split("-")
    if len(parts) < 6:
        raise FixtureDiscoveryError(f"Fixture name '{stem}' does not match expected pattern")

    manufacturer = parts[0]
    drive_style = parts[1]
    sides_density = parts[2]
    encoding = parts[3]
    approx_capacity = parts[-1]
    os_parts = parts[4:-1]
    os_name = "-".join(os_parts) if os_parts else ""

    for field_name, value in (
        ("manufacturer", manufacturer),
        ("drive_style", drive_style),
        ("sides_density", sides_density),
        ("encoding", encoding),
        ("os", os_name),
        ("capacity", approx_capacity),
    ):
        if not value:
            raise FixtureDiscoveryError(f"Fixture name '{stem}' is missing a value for {field_name}")

    return [manufacturer, drive_style, sides_density, encoding, os_name, approx_capacity]


def _load_metadata_for_fixture(path: Path) -> Mapping[str, Any]:
    """Load JSON or YAML metadata that shares the fixture's stem."""

    for candidate in _metadata_candidates(path):
        if not candidate.exists():
            continue
        if candidate.suffix.lower() == ".json":
            return json.loads(candidate.read_text())
        if candidate.suffix.lower() in {".yaml", ".yml"}:  # pragma: no cover - exercised when YAML fixtures are added
            try:
                import yaml  # type: ignore
            except ImportError as exc:  # pragma: no cover - defensive branch
                raise FixtureDiscoveryError("pyyaml is required to load YAML fixture metadata") from exc
            return yaml.safe_load(candidate.read_text())
    return {}


def _metadata_candidates(path: Path) -> Iterable[Path]:
    stem = path.with_suffix("").name
    yield path.with_name(f"{stem}.json")
    yield path.with_name(f"{stem}.yaml")
    yield path.with_name(f"{stem}.yml")


def discover_fixtures(base_dir: Path) -> List[FixtureDescriptor]:
    """Recursively discover fixture images under ``base_dir``.

    Only files with known flux image extensions are considered. Metadata sidecars are resolved per fixture and attached to the
    resulting :class:`FixtureDescriptor` instance.
    """

    if not base_dir.exists():
        raise FixtureDiscoveryError(f"Fixture directory '{base_dir}' does not exist")

    descriptors: List[FixtureDescriptor] = []
    for path in sorted(base_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in FIXTURE_EXTENSIONS:
            continue
        try:
            descriptors.append(FixtureDescriptor.from_path(path))
        except FixtureDiscoveryError:
            # Ignore files that don't follow the expected naming pattern so well-formed fixtures still load
            continue
    return descriptors
