"""Minimal SuperCard Pro parser for inspection and provenance.

The implementation matches the TRK layout and timing interpretation used by
Keir Fraser's Greaseweazle project to maximise compatibility with real-world
SCP dumps.
"""
from __future__ import annotations

from array import array
import hashlib
import struct
from pathlib import Path
from typing import List, Sequence

from .exceptions import SCPFormatError
from .models import RevolutionFlux, SCPImage, TrackFlux

MAGIC = b"SCP"
DEFAULT_TIMEBASE_NS = 25.0
OFFSET_TABLE_ENTRIES = 168


def _decode_timebase(version: int, raw_timebase: int) -> float:
    """Return the nanosecond timebase derived from the SCP header.

    The behaviour mirrors Greaseweazle's SCP reader: older images (version
    byte ``0``) default to a 25ns tick unless the header encodes an explicit
    nanosecond value. Newer captures interpret values above 1000 as a capture
    clock frequency in kHz.
    """

    if version == 0:
        if 0 < raw_timebase <= 1000:
            return float(raw_timebase)
        return DEFAULT_TIMEBASE_NS

    if 0 < raw_timebase <= 1000:
        return float(raw_timebase)
    if raw_timebase > 1000:
        return 1_000_000.0 / raw_timebase
    return DEFAULT_TIMEBASE_NS


def _read_header(data: bytes) -> tuple[int, int, int, int, float]:
    if len(data) < 16:
        raise SCPFormatError("SCP file too small for header")
    if data[0:3] != MAGIC:
        raise SCPFormatError("Not an SCP file")

    version = data[3]
    revolutions = data[5]
    start_track = data[6]
    end_track = data[7]

    timebase_raw = int.from_bytes(data[8:12], "little", signed=False)
    timebase = _decode_timebase(version, timebase_raw)

    if revolutions <= 0:
        raise SCPFormatError("SCP header reports no revolutions")
    if start_track > end_track:
        raise SCPFormatError("Start track greater than end track")
    return version, revolutions, start_track, end_track, timebase


def _parse_flux_bytes(flux_bytes: bytes, timebase_ns: float) -> Sequence[int]:
    """Convert raw flux bytes into nanosecond intervals.

    SuperCard Pro stores 16-bit big-endian tick words; each word is multiplied
    by ``timebase_ns`` and rounded to the nearest integer nanosecond. Zero tick
    words are ignored, matching the Greaseweazle interpretation.
    """

    intervals_ns: array[int] = array("I")
    if not flux_bytes:
        return intervals_ns

    for (tick,) in struct.iter_unpack(">H", flux_bytes[: (len(flux_bytes) // 2) * 2]):
        if tick:
            intervals_ns.append(int(round(tick * timebase_ns)))
    return intervals_ns


def parse_scp(path: Path) -> SCPImage:
    data = path.read_bytes()
    version, revolutions, start_track, end_track, timebase_ns = _read_header(data)

    if end_track >= OFFSET_TABLE_ENTRIES:
        raise SCPFormatError("Track range exceeds SCP offset table")

    offsets_table_end = 16 + OFFSET_TABLE_ENTRIES * 4
    if len(data) < offsets_table_end:
        raise SCPFormatError("SCP file missing track offset table")

    offsets = [struct.unpack_from("<I", data, 16 + idx * 4)[0] for idx in range(OFFSET_TABLE_ENTRIES)]

    tracks: List[TrackFlux] = []
    for idx in range(start_track, end_track + 1):
        block_offset = offsets[idx]
        if block_offset == 0:
            continue

        next_offset = len(data)
        for candidate in offsets[idx + 1 : end_track + 1]:
            if candidate and candidate > block_offset:
                next_offset = min(next_offset, candidate)

        if block_offset >= len(data):
            raise SCPFormatError("Track offset points past end of file")

        track_block = data[block_offset:next_offset]
        if len(track_block) < 16:
            raise SCPFormatError("Track block truncated before TRK header")
        if not track_block.startswith(b"TRK"):
            raise SCPFormatError("Track block missing TRK header")

        track_block_end = block_offset + len(track_block)
        header_length = 16 + max(0, revolutions - 1) * 12
        if len(track_block) < header_length:
            raise SCPFormatError("TRK block truncated before revolution records")

        track_index_byte = track_block[3]
        track_num = idx // 2
        head_num = idx % 2

        revolution_flux: List[RevolutionFlux] = []
        for rev_index in range(revolutions):
            if rev_index == 0:
                rev_entry_offset = block_offset + 4
            else:
                rev_entry_offset = block_offset + 16 + (rev_index - 1) * 12

            index_ticks = struct.unpack_from("<I", data, rev_entry_offset)[0]
            length_words = struct.unpack_from("<I", data, rev_entry_offset + 4)[0]
            offset_bytes = struct.unpack_from("<I", data, rev_entry_offset + 8)[0]

            data_length_bytes = length_words * 2
            data_offset = block_offset + offset_bytes

            flux_start = data_offset
            flux_end = flux_start + data_length_bytes
            valid_range = (
                offset_bytes >= 0
                and flux_start >= block_offset
                and flux_end <= track_block_end
            )

            if valid_range:
                flux_bytes = data[flux_start:flux_end]
                intervals = _parse_flux_bytes(flux_bytes, timebase_ns)
            else:
                intervals = array("I")

            index_time_ns = int(round(index_ticks * timebase_ns))
            revolution_flux.append(
                RevolutionFlux(
                    index=rev_index,
                    interval_ns=intervals,
                    index_time_ns=index_time_ns,
                    data_offset=data_offset,
                    data_length_bytes=data_length_bytes,
                )
            )

        tracks.append(TrackFlux(track=track_num, side=head_num, revolutions=revolution_flux))

        if track_index_byte != idx:
            # Track index byte is nominally the same as the offset table index;
            # mismatches are tolerated but worth flagging in debugging sessions.
            pass

    return SCPImage(
        path=path,
        version=version,
        revolutions_per_track=revolutions,
        timebase_ns=timebase_ns,
        tracks=tracks,
    )


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
