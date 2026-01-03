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
    raw_timebase = int.from_bytes(data[12:14], "little", signed=False)
    if raw_timebase and raw_timebase > 1000:
        timebase = int(1_000_000 / raw_timebase)
    else:
        timebase = raw_timebase or 25
    if revolutions <= 0:
        raise SCPFormatError("SCP header reports no revolutions")
    if start_track > end_track:
        raise SCPFormatError("Start track greater than end track")
    return version, revolutions, start_track, end_track, timebase


def _parse_flux_bytes(flux_bytes: bytes, timebase_ns: int) -> Sequence[int]:
    """Convert raw flux bytes into interval timings.

    SuperCard Pro stores flux intervals as 16-bit little-endian tick counts.
    This helper keeps the parsing logic localised so the higher-level track
    parsing code can focus on correctly slicing per-revolution blobs.
    """

    if not flux_bytes:
        return []
    interval_count = len(flux_bytes) // 2
    if interval_count == 0:
        return []
    intervals_ticks = struct.unpack(f"<{interval_count}H", flux_bytes[: interval_count * 2])
    return array("I", (tick * timebase_ns for tick in intervals_ticks if tick))


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
        next_track_offset = next((o for o in offsets[idx + 1 :] if o != 0), len(data))
        header_len = 16 + revolutions * 8
        header = data[offset : offset + header_len]
        if len(header) < header_len or not header.startswith(b"TRK"):
            raise SCPFormatError("Track block missing TRK header")

        track_index = header[3]
        head_hint = header[4] if header[4] in (0, 1) else None

        # The TRK header stores absolute offsets to each revolution beginning at
        # byte 16, followed by optional per-revolution byte counts. Earlier
        # revisions flattened tracks by slicing a single blob; keep offsets and
        # lengths distinct so multi-revolution captures remain independent.
        revolution_offsets = [struct.unpack_from("<I", header, 16 + 4 * rev)[0] for rev in range(revolutions)]
        revolution_lengths = [struct.unpack_from("<I", header, 16 + 4 * revolutions + 4 * rev)[0] for rev in range(revolutions)]

        def _valid_offset_table(values: List[int]) -> bool:
            filtered = [val for val in values if 0 < val < len(data)]
            return len(filtered) == revolutions and all(a < b for a, b in zip(filtered, filtered[1:]))

        valid_lengths = [val for val in revolution_lengths if 0 < val < len(data)]
        base_length = min(valid_lengths) if valid_lengths else None

        if _valid_offset_table(revolution_offsets):
            rev_starts: List[int | None] = revolution_offsets
        elif _valid_offset_table(revolution_lengths):
            rev_starts = revolution_lengths
        else:
            candidates = {val for val in revolution_offsets + revolution_lengths if 0 < val < len(data)}
            rev_starts = sorted(candidates)[:revolutions]

        while len(rev_starts) < revolutions:
            rev_starts.append(None)

        ordered_starts = sorted([start for start in rev_starts if start is not None])

        # Map linear track index to cylinder/head. SCP files enumerate tracks as
        # (track * sides) + head, so we derive the side from the least
        # significant bit when explicit head metadata is absent or invalid.
        track_num = track_index // 2
        head_num = head_hint if head_hint is not None else track_index % 2

        revolution_flux: List[RevolutionFlux] = []

        for rev_index, rev_offset in enumerate(rev_starts):
            if rev_offset is None or rev_offset <= 0 or rev_offset >= len(data):
                revolution_flux.append(RevolutionFlux(index=rev_index, interval_ns=[], data_offset=None, data_length_bytes=None))
                continue

            length_hint = revolution_lengths[rev_index]
            length_is_valid = 0 < length_hint <= len(data) - rev_offset
            following_offsets = [o for o in ordered_starts if rev_offset < o]
            candidate_end: int | None = None

            if following_offsets:
                candidate_end = min(following_offsets)
            if length_is_valid:
                candidate_end = min(candidate_end, rev_offset + length_hint) if candidate_end else rev_offset + length_hint

            if candidate_end is None:
                candidate_end = next_track_offset if next_track_offset > rev_offset else len(data)
            elif next_track_offset > rev_offset:
                candidate_end = min(candidate_end, next_track_offset)

            if candidate_end is None and base_length:
                candidate_end = min(len(data), rev_offset + base_length)

            if candidate_end is None or candidate_end <= rev_offset:
                revolution_flux.append(
                    RevolutionFlux(index=rev_index, interval_ns=[], data_offset=rev_offset, data_length_bytes=None)
                )
                continue

            flux_bytes = data[rev_offset:candidate_end]
            intervals = _parse_flux_bytes(flux_bytes, timebase)
            revolution_flux.append(
                RevolutionFlux(
                    index=rev_index,
                    interval_ns=list(intervals),
                    data_offset=rev_offset,
                    data_length_bytes=len(flux_bytes),
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
