"""Locate optional external tools in source and packaged installations."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def _bundled_candidates(name: str) -> list[Path]:
    executable_dir = Path(sys.executable).resolve().parent
    roots = [executable_dir, executable_dir / "_internal"]
    if getattr(sys, "frozen", False):
        roots.append(Path(getattr(sys, "_MEIPASS", executable_dir)))
    return [root / "helpers" / name for root in roots]


def greaseweazle_executable() -> Path | None:
    name = "gw.exe" if os.name == "nt" else "gw"
    candidates: list[Path] = []
    if configured := os.environ.get("FLUXCTL_GW_PATH"):
        candidates.append(Path(configured).expanduser())
    candidates.extend(_bundled_candidates(name))
    candidates.extend(_bundled_candidates(f"gw/{name}"))
    candidates.append(Path(sys.executable).parent / name)
    if found := shutil.which("gw"):
        candidates.append(Path(found))
    return next((path for path in candidates if path.is_file()), None)


def bundled_hxcfe_candidates() -> list[Path]:
    name = "hxcfe.exe" if os.name == "nt" else "hxcfe"
    candidates: list[Path] = []
    if configured := os.environ.get("FLUXCTL_HXCFE_PATH"):
        candidates.append(Path(configured).expanduser())
    candidates.extend(_bundled_candidates(name))
    return candidates


def diagnostic_worker_executable() -> Path | None:
    name = "fluxctl-diag-worker.exe" if os.name == "nt" else "fluxctl-diag-worker"
    candidates = _bundled_candidates(f"diag-worker/{name}")
    return next((path for path in candidates if path.is_file()), None)
