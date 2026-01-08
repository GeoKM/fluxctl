"""Run the HxC CLI to collect disk geometry hints."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from fluxctl.exceptions import FluxctlError
from fluxctl.geohints import LayoutHint


@dataclass(frozen=True)
class HxcMetadata:
    """Parsed output from `hxcfe -infos`."""

    loader: Optional[str] = None
    interface: Optional[str] = None
    tracks: Optional[int] = None
    sides: Optional[int] = None
    total_size: Optional[int] = None
    total_sectors: Optional[int] = None
    error: Optional[str] = None

    def to_layout_hint(self) -> LayoutHint:
        hint_metadata: Dict[str, str] = {}
        if self.error:
            hint_metadata["hxc_error"] = self.error
        return LayoutHint(
            tracks=self.tracks,
            sides=self.sides,
            total_size=self.total_size,
            total_sectors=self.total_sectors,
            interface=self.interface,
            loader=self.loader,
            metadata=hint_metadata,
        )


_digits = re.compile(r"[\d,]+")


def _parse_int(text: str) -> Optional[int]:
    match = _digits.search(text)
    if not match:
        return None
    return int(match.group(0).replace(",", ""))


def parse_hxc_infos_output(output: str) -> HxcMetadata:
    """Convert the textual CLI output into structured metadata."""

    data = {
        "loader": None,
        "interface": None,
        "tracks": None,
        "sides": None,
        "total_size": None,
        "total_sectors": None,
        "error": None,
    }

    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        if "No loader support" in line:
            data["error"] = line
            continue
        if line.startswith("File loader found :"):
            value = line.split(":", 1)[1].strip()
            data["loader"] = value.split()[0].strip()
            continue
        if line.startswith("Floppy interface mode :"):
            value = line.split(":", 1)[1].strip()
            data["interface"] = value.split("-")[0].strip()
            continue
        if line.startswith("Number of Track :"):
            count = _parse_int(line)
            if count is not None:
                data["tracks"] = count
            continue
        if line.startswith("Number of Side :"):
            count = _parse_int(line)
            if count is not None:
                data["sides"] = count
            continue
        if line.startswith("Total Size :"):
            size_match = re.search(r"Total Size : ([\d,]+) Bytes", line)
            sectors_match = re.search(r"Number of sectors : ([\d,]+)", line)
            if size_match:
                data["total_size"] = int(size_match.group(1).replace(",", ""))
            if sectors_match:
                data["total_sectors"] = int(sectors_match.group(1).replace(",", ""))
    return HxcMetadata(**data)


def probe_hxcfe(target: Path, executable: Path, env: Optional[Dict[str, str]] = None) -> HxcMetadata:
    """Run `hxcfe -infos` and return the parsed output."""

    env_vars = (env.copy() if env else os.environ.copy())
    lib_dir = executable.parent
    env_vars.setdefault("DYLD_LIBRARY_PATH", str(lib_dir))
    env_vars.setdefault("LD_LIBRARY_PATH", str(lib_dir))

    try:
        proc = subprocess.run(
            [str(executable), f"-finput:{target}", "-infos"],
            cwd=target.parent,
            env=env_vars,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.strip() or exc.stdout.strip()
        raise FluxctlError(f"HxC CLI failed: {message}") from exc
    except OSError as exc:
        raise FluxctlError(f"Unable to execute hxcfe at {executable}: {exc}") from exc

    return parse_hxc_infos_output(proc.stdout)
