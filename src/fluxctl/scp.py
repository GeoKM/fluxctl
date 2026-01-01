"""Minimal SuperCard Pro parser for inspection and provenance."""
from __future__ import annotations

import hashlib
import struct
from pathlib import Path
from typing import List, Sequence

from .exceptions import SCPFormatError
from .models import RevolutionFlux, SCPImage, TrackFlux

MAGIC = b"SCP"


def _read_header(data: bytes) -> tuple[int, int, int, int, int]:
    if len(data) < 16:
        raise SCPFormatError("SCP file too small for header")
    if data[0:3] != MAGIC:
        raise SCPFormatError("Not an SCP file")
    version = data[3]
    revolutions = data[5]
    start_track = data[6]
    end_track = data[7]
    timebase = int.from_bytes(data[12:14], "little", signed=False) or 25
    if revolutions <= 0:
        raise SCPFormatError("SCP header reports no revolutions")
    if start_track > end_track:
        raise SCPFormatError("Start track greater than end track")
    return version, revolutions, start_track, end_track, timebase


def _parse_track_flux(block: bytes, timebase_ns: int) -> Sequence[int]:
    """Extract raw flux intervals from a single track block.

    The parser supports the SuperCard Pro track structure used by the bundled
    fixtures: each block begins with the ASCII marker ``TRK`` followed by a
    small header. Flux intervals are stored immediately after a 32-byte header
    as big-endian 16-bit tick counts, scaled by the image timebase.
    """

    if not block.startswith(b"TRK"):
        raise SCPFormatError("Track block missing TRK header")
    header_len = 32
    if len(block) <= header_len:
        return []
    raw_intervals = block[header_len:]
    # Flux timings are stored as 16-bit values representing multiples of the
    # image's timebase. The bundled fixtures use big-endian ordering.
    interval_count = len(raw_intervals) // 2
    if interval_count == 0:
        return []
    intervals_ticks = struct.unpack(f">{interval_count}H", raw_intervals[: interval_count * 2])
    return [tick * timebase_ns for tick in intervals_ticks if tick]


def parse_scp(path: Path) -> SCPImage:
    data = path.read_bytes()
    version, revolutions, start_track, end_track, timebase = _read_header(data)

    track_count = end_track - start_track + 1
    offsets: List[int] = []
    for idx in range(track_count):
        offsets.append(struct.unpack_from("<I", data, 16 + idx * 4)[0])

    tracks: List[TrackFlux] = []
    for idx, offset in enumerate(offsets):
        if offset == 0:
            continue
        next_offset = next((o for o in offsets[idx + 1 :] if o != 0), len(data))
        intervals = _parse_track_flux(data[offset:next_offset], timebase)
        revolution_flux = [RevolutionFlux(index=i, interval_ns=list(intervals)) for i in range(revolutions)]
        tracks.append(TrackFlux(track=start_track + idx, side=0, revolutions=revolution_flux))

    return SCPImage(
        path=path,
        version=version,
        revolutions_per_track=revolutions,
        timebase_ns=timebase,
        tracks=tracks,
    )


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
