"""Hardware imaging operations shared by Fluxctl frontends."""
from __future__ import annotations

from pathlib import Path
from typing import Optional
import shlex
import shutil
import subprocess
import sys

from .models import GreaseweazleFormat, GreaseweazleStatus, HardwareReadResult
from ..output import validate_output_path


def _greaseweazle_executable() -> Optional[Path]:
    exe_name = "gw.exe" if sys.platform.startswith("win") else "gw"
    candidate = Path(sys.executable).parent / exe_name
    if candidate.exists():
        return candidate
    found = shutil.which("gw")
    return Path(found) if found else None


def greaseweazle_status():
    executable = _greaseweazle_executable()
    if executable is None:
        return GreaseweazleStatus(False, "", "Greaseweazle command `gw` was not found.", "Install Greaseweazle, then run fluxctl doctor.")
    return GreaseweazleStatus(True, str(executable), f"Greaseweazle command available at {executable}")


def greaseweazle_formats():
    executable = _greaseweazle_executable()
    if executable is None:
        return []
    completed = subprocess.run([str(executable), "read", "--help"], text=True, capture_output=True, check=False)
    formats: set[str] = set()
    active = False
    for line in f"{completed.stdout}\n{completed.stderr}".splitlines():
        stripped = line.strip()
        if stripped.startswith("FORMAT options:"):
            active = True
            continue
        if active and (not stripped or stripped.startswith("Supported file suffixes:")):
            break
        if active:
            formats.update(token for token in stripped.split() if "." in token and not token.startswith(".") and all(char.isalnum() or char in "._-" for char in token))
    return [GreaseweazleFormat(item, item) for item in sorted(formats)]


def read_disk_with_greaseweazle(
    output: Path,
    *,
    drive: str = "A",
    gw_format: str = "",
    tracks: str = "",
    revs: Optional[int] = None,
    overwrite: bool = False,
):
    if output.suffix.lower() != ".scp":
        output = output.with_suffix(".scp")
    executable = _greaseweazle_executable()
    if executable is None:
        raise RuntimeError("Greaseweazle command `gw` is not available")
    if output.exists() and not overwrite:
        raise FileExistsError(f"Output image already exists: {output}")
    validate_output_path(output, overwrite=overwrite)
    args = [str(executable), "read", "--drive", drive, "--raw"]
    if gw_format:
        args.extend(["--format", gw_format])
    if tracks:
        args.extend(["--tracks", tracks])
    if revs is not None:
        if revs < 1:
            raise ValueError("Greaseweazle read revolutions must be 1 or greater")
        args.extend(["--revs", str(revs)])
    args.append(str(output))
    completed = subprocess.run(args, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit status {completed.returncode}"
        raise RuntimeError(f"Greaseweazle read failed: {detail}")
    return HardwareReadResult(str(output), args, " ".join(shlex.quote(item) for item in args), completed.stdout, completed.stderr)
