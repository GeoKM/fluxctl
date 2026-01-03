"""Minimal SuperCard Pro parser for inspection and provenance."""
from __future__ import annotations

import hashlib
import struct
from array import array
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

    # The published format reserves bytes 8-11 for timing metadata. Real images
    # sometimes leave the field zeroed, so fall back to the default 25ns tick
    # time when the value looks implausible or absent.
    timebase_raw = int.from_bytes(data[8:12], "little", signed=False)
    timebase = timebase_raw if 5 <= timebase_raw <= 1_000 else 25

    if revolutions <= 0:
        raise SCPFormatError("SCP header reports no revolutions")
    if start_track > end_track:
        raise SCPFormatError("Start track greater than end track")
    return version, revolutions, start_track, end_track, timebase


def _parse_flux_bytes(flux_bytes: bytes, timebase_ns: int) -> Sequence[int]:
    """Convert raw flux bytes into interval timings.

    SuperCard Pro stores flux intervals as big-endian 16-bit tick counts. The
    tick values are multiplied by ``timebase_ns`` to expose nanosecond
    intervals.
    """

    if not flux_bytes:
        return []
    interval_count = len(flux_bytes) // 2
    if interval_count == 0:
        return []
    intervals_ticks = struct.unpack(f">{interval_count}H", flux_bytes[: interval_count * 2])
    return array("I", (tick * timebase_ns for tick in intervals_ticks if tick))


def parse_scp(path: Path) -> SCPImage:
    data = path.read_bytes()
    version, revolutions, start_track, end_track, timebase = _read_header(data)

    track_count = end_track - start_track + 1
    offsets: List[int] = []
    offsets_table_end = 16 + track_count * 4
    if len(data) < offsets_table_end:
        raise SCPFormatError("SCP file missing track offset table")
    for idx in range(track_count):
        offsets.append(struct.unpack_from("<I", data, 16 + idx * 4)[0])

    tracks: List[TrackFlux] = []
    for idx, offset in enumerate(offsets):
        if offset == 0:
            continue
        block_offset = offset
        if len(data) < block_offset + 4:
            raise SCPFormatError("Track block truncated before TRK header")
        if data[block_offset : block_offset + 3] != b"TRK":
            raise SCPFormatError("Track block missing TRK header")

        track_index = data[block_offset + 3]

        # TRK blocks store a per-revolution table directly after the four-byte
        # prologue. Each entry holds ``index_ticks`` (sum of tick words),
        # ``word_count`` (16-bit tick word count), and ``offset_bytes`` (byte
        # offset from the start of the TRK block to the revolution's flux
        # payload).
        table_offset = block_offset + 4
        table_length = revolutions * 12
        if len(data) < table_offset + table_length:
            raise SCPFormatError("TRK block truncated before revolution table")

        track_num = track_index // 2
        head_num = track_index % 2

        revolution_flux: List[RevolutionFlux] = []
        for rev_index in range(revolutions):
            entry_offset = table_offset + rev_index * 12
            index_ticks = struct.unpack_from("<I", data, entry_offset)[0]
            word_count = struct.unpack_from("<I", data, entry_offset + 4)[0]
            offset_bytes = struct.unpack_from("<I", data, entry_offset + 8)[0]

            flux_start = block_offset + offset_bytes
            flux_end = flux_start + word_count * 2
            if offset_bytes == 0 or flux_start < 0 or flux_end > len(data):
                revolution_flux.append(
                    RevolutionFlux(
                        index=rev_index,
                        interval_ns=[],
                        index_time_ns=index_ticks * timebase,
                        data_offset=offset_bytes,
                        data_length_bytes=word_count * 2,
                    )
                )
                continue

            flux_bytes = data[flux_start:flux_end]
            intervals = _parse_flux_bytes(flux_bytes, timebase)
            revolution_flux.append(
                RevolutionFlux(
                    index=rev_index,
                    interval_ns=list(intervals),
                    index_time_ns=index_ticks * timebase,
                    data_offset=offset_bytes,
                    data_length_bytes=word_count * 2,
                )
            )

        tracks.append(TrackFlux(track=track_num, side=head_num, revolutions=revolution_flux))

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
