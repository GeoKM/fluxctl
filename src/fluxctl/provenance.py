"""Helpers for writing provenance sidecar files."""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .models import ProvenanceRecord
from .output import atomic_write_text


def write_provenance(record: ProvenanceRecord, path: Path, *, overwrite: bool = False) -> Path:
    """Serialise ``record`` to ``path`` as JSON.

    A timestamp is injected if one has not been provided by the caller. The
    helper returns the path written for convenience in tests and CLI plumbing.
    """

    payload = asdict(record)
    if payload.get("input_path"):
        payload["input_path"] = str(payload["input_path"])
    if payload.get("output_path"):
        payload["output_path"] = str(payload["output_path"])
    payload["timestamp"] = record.timestamp or datetime.now(timezone.utc).isoformat()
    atomic_write_text(path, json.dumps(payload, indent=2), overwrite=overwrite)
    return path
