"""Minimal SuperCard Pro parser for inspection and provenance."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import List

from .models import RevolutionFlux, SCPImage, TrackFlux

MAGIC = b"SCP"


def _read_header(data: bytes) -> tuple[int, int, int]:
    if len(data) < 16:
        raise ValueError("SCP file too small")
    if data[0:3] != MAGIC:
        raise ValueError("Not an SCP file")
    version = data[3]
    # Simplified: bytes 4-5: disk type, 6-7: revolutions, 8-9: start track, 10-11: end track
    revolutions = int.from_bytes(data[6:8], "little", signed=False)
    timebase = int.from_bytes(data[14:16], "little", signed=False) or 25
    return version, revolutions, timebase


def parse_scp(path: Path) -> SCPImage:
    data = path.read_bytes()
    version, revolutions, timebase = _read_header(data)
    # This parser is intentionally light-weight; it does not decode track data fully
    # but reports presence counts based on header indices.
    start_track = data[8]
    end_track = data[9]
    tracks: List[TrackFlux] = []
    for track in range(start_track, end_track + 1):
        for side in range(2):
            tracks.append(TrackFlux(track=track, side=side, revolutions=[RevolutionFlux(index=i, interval_ns=[]) for i in range(revolutions)]))
    return SCPImage(path=path, version=version, revolutions_per_track=revolutions, timebase_ns=timebase, tracks=tracks)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
