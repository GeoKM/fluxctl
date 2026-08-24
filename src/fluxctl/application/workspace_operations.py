"""Portable Studio workspace persistence."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def save_workspace(path: Path, state: dict[str, Any]) -> Path:
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def load_workspace(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema") != "fluxctl-studio-workspace-v1":
        raise ValueError("Unsupported Fluxctl Studio workspace")
    return value


__all__ = ["load_workspace", "save_workspace"]
