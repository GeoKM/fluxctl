"""Minimal SuperCard Pro parser for inspection and provenance.

The implementation matches the TRK layout and timing interpretation used by
Keir Fraser's Greaseweazle project to maximise compatibility with real-world
SCP dumps. To sanity-check behaviour against Greaseweazle, run
``python -m greaseweazle.tools.info sample.scp`` and compare the reported
revolution timings and tick count totals to :func:`parse_scp`.
"""
from __future__ import annotations

from array import array
import hashlib
import struct
from pathlib import Path
from typing import List, Sequence

from .exceptions import SCPFormatError
from .models import RevolutionFlux, SCPImage, TrackFlux
from .native import parse_scp_flux_bytes

MAGIC = b"SCP"
DEFAULT_TIMEBASE_NS = 25.0
# Greaseweazle treats a zero/very-low timebase in modern captures as a 40MHz
# sampler (25ns ticks). Some tools store a tiny integer (e.g., 2) which is
# actually the tick count for 50ns or an artefact of the writer. To stay
# compatible, clamp implausibly small timebases to the default.
MIN_REASONABLE_NS = 20.0
OFFSET_TABLE_ENTRIES = 168


def _decode_timebase(version: int, raw_timebase: int) -> float:
    """Return the nanosecond timebase derived from the SCP header.

    The behaviour mirrors Greaseweazle's SCP reader: older images (version
    byte ``0``) default to a 25ns tick unless the header encodes an explicit
    nanosecond value. Newer captures interpret values above 1000 as a capture
    clock frequency in kHz.
    """

    # Preserve legacy behaviour for very old captures (version 0/1) and
    # explicit high-resolution settings; clamp suspiciously small values only
    # on newer versions.
    if version == 0:
        if 0 < raw_timebase <= 1000:
            return float(raw_timebase)
        return DEFAULT_TIMEBASE_NS

    if version <= 1 and 0 < raw_timebase <= 1000:
        return float(raw_timebase)

    if 0 < raw_timebase <= 1000:
        if raw_timebase < 5:
            return DEFAULT_TIMEBASE_NS
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
    by ``timebase_ns`` and rounded to the nearest integer nanosecond. Long
    intervals are encoded using overflow sentinels: a ``0`` word adds ``0x10000``
    ticks to the following non-zero word, repeating for consecutive zeroes.
    The behaviour mirrors Greaseweazle's SCP reader and reconstructs the full
    interval instead of dropping the overflow markers.
    """

    intervals_ns: array[int] = array("I")
    if not flux_bytes:
        return intervals_ns
    native_intervals = parse_scp_flux_bytes(flux_bytes, timebase_ns)
    if native_intervals is not None:
        return native_intervals

    overflow = 0
    for (tick,) in struct.iter_unpack(">H", flux_bytes[: (len(flux_bytes) // 2) * 2]):
        if tick == 0:
            overflow += 0x10000
            continue

        ticks_total = overflow + tick
        intervals_ns.append(int(round(ticks_total * timebase_ns)))
        overflow = 0

    return intervals_ns


def parse_scp(path: Path) -> SCPImage:
    """Parse an SCP image, with a Greaseweazle fallback for newer variants.

    Some captures (e.g., produced by newer Greaseweazle builds) ship with
    track headers that our minimal parser does not yet understand. Rather
    than fail with empty revolutions, fall back to Greaseweazle's own SCP
    reader when available so we can still obtain flux timings.
    """

    # First try the built-in lightweight parser.
    data = path.read_bytes()
    version, revolutions, start_track, end_track, timebase_ns = _read_header(data)

    if end_track >= OFFSET_TABLE_ENTRIES:
        raise SCPFormatError("Track range exceeds SCP offset table")

    table_entries = min(
        OFFSET_TABLE_ENTRIES, end_track + 1, max(0, (len(data) - 16) // 4)
    )
    offsets: List[int] = [
        struct.unpack_from("<I", data, 16 + idx * 4)[0] if idx < table_entries else 0
        for idx in range(OFFSET_TABLE_ENTRIES)
    ]

    min_offset = min(
        (
            off
            for idx, off in enumerate(offsets)
            if start_track <= idx <= end_track and off > 0
        ),
        default=None,
    )
    if min_offset is not None:
        table_entries = min(table_entries, max(0, (min_offset - 16) // 4))

    offsets_table_end = 16 + table_entries * 4
    for idx in range(table_entries, OFFSET_TABLE_ENTRIES):
        offsets[idx] = 0

    tracks: List[TrackFlux] = []
    warnings: List[str] = []
    for idx in range(start_track, end_track + 1):
        if idx >= len(offsets):
            break

        block_offset = offsets[idx]
        if block_offset == 0:
            continue

        if block_offset < offsets_table_end:
            warnings.append(
                f"Track {idx}: offset points into header/offset table ({block_offset})"
            )
            continue
        if block_offset >= len(data):
            warnings.append(
                f"Track {idx}: offset past end of file ({block_offset} >= {len(data)})"
            )
            continue

        next_offset = len(data)
        for candidate in offsets[idx + 1 : end_track + 1]:
            if (
                candidate
                and candidate >= offsets_table_end
                and candidate <= len(data)
                and candidate > block_offset
            ):
                next_offset = min(next_offset, candidate)

        track_block = data[block_offset:next_offset]
        if len(track_block) < 16:
            warnings.append(f"Track {idx}: block truncated before TRK header")
            continue
        if not track_block.startswith(b"TRK"):
            warnings.append(f"Track {idx}: missing TRK header")
            continue

        track_block_end = block_offset + len(track_block)
        header_length = 16 + max(0, revolutions - 1) * 12
        if len(track_block) < header_length:
            warnings.append(f"Track {idx}: TRK block truncated before revolution records")
            continue

        track_index_byte = track_block[3]
        track_num = idx // 2
        head_num = idx % 2

        if track_index_byte != idx:
            warnings.append(
                f"Track {idx}: offset table index mismatches TRK byte ({track_index_byte})"
            )

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

            # Some images (e.g., certain v2.4 SCP captures) leave the
            # per-revolution headers zeroed but still store flux data after
            # the TRK header. Salvage a single revolution from the remaining
            # block bytes so downstream decoders can attempt recovery.
            if (
                not intervals
                and length_words == 0
                and offset_bytes == 0
                and rev_index == 0
                and len(track_block) > header_length
            ):
                flux_bytes = track_block[header_length:]
                intervals = _parse_flux_bytes(flux_bytes, timebase_ns)

                if intervals:
                    # Split the flux list into evenly sized revolutions based on
                    # the declared revolution count so downstream decoders can
                    # make multiple passes across the track.
                    total_ns = sum(intervals)
                    target_ns = total_ns / max(revolutions, 1)
                    accum_ns = 0
                    accum: list[int] = []
                    splits: list[list[int]] = []
                    for interval in intervals:
                        accum.append(interval)
                        accum_ns += interval
                        if accum_ns >= target_ns and len(splits) < max(revolutions - 1, 0):
                            splits.append(accum)
                            accum = []
                            accum_ns = 0
                    if accum:
                        splits.append(accum)
                    if not splits:
                        splits = [list(intervals)]

                    for split_idx, split in enumerate(splits):
                        rev_ns = int(round(sum(split)))
                        revolution_flux.append(
                            RevolutionFlux(
                                index=split_idx,
                                interval_ns=split,
                                index_time_ns=rev_ns,
                                data_length_bytes=len(split) * 2,
                                data_offset=None,
                            )
                        )
                    # Skip the standard rev loop; we've populated all revs.
                    break
                index_ticks = sum(intervals) if intervals else 0

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

    image = SCPImage(
        path=path,
        version=version,
        revolutions_per_track=revolutions,
        timebase_ns=timebase_ns,
        tracks=tracks,
        warnings=warnings,
    )

    # If every revolution decoded to an empty interval list, try Greaseweazle.
    if all(not rev.interval_ns for trk in image.tracks for rev in trk.revolutions):
        gw_image = _parse_with_greaseweazle(path)
        if gw_image is not None:
            gw_image.warnings.extend(warnings)
            gw_image.warnings.append("Parsed via Greaseweazle fallback SCP reader")
            return gw_image

    return image


def _parse_with_greaseweazle(path: Path) -> SCPImage | None:
    """Fallback SCP parser backed by Greaseweazle's implementation."""

    try:
        from greaseweazle.image.scp import SCP as GWSCP
    except Exception:
        return None

    data = path.read_bytes()
    gw = GWSCP(str(path), None)
    try:
        gw.from_bytes(data)
    except Exception:
        return None

    tracks: List[TrackFlux] = []
    warnings: List[str] = []
    ns_per_tick = 1_000_000_000.0 / GWSCP.sample_freq

    for track_nr, track in gw.to_track.items():
        cyl = track_nr // 2
        side = track_nr % 2
        flux = gw.get_track(cyl, side)
        if flux is None or not flux.list:
            continue

        rev_lengths = flux.index_list or []
        revs: List[RevolutionFlux] = []
        tick_accum = 0
        rev_idx = 0
        rev_intervals: list[int] = []
        target = rev_lengths[rev_idx] if rev_lengths else None

        for t in flux.list:
            tick_accum += t
            rev_intervals.append(int(round(t * ns_per_tick)))
            if target is not None and tick_accum >= target:
                index_ns = int(round(target * ns_per_tick))
                revs.append(
                    RevolutionFlux(
                        index=rev_idx,
                        interval_ns=rev_intervals,
                        index_time_ns=index_ns,
                        data_length_bytes=len(rev_intervals) * 2,
                        data_offset=None,
                    )
                )
                rev_idx += 1
                tick_accum = 0
                rev_intervals = []
                target = rev_lengths[rev_idx] if rev_idx < len(rev_lengths) else None

        if rev_intervals:
            index_ns = int(round((tick_accum or (rev_lengths[rev_idx] if rev_idx < len(rev_lengths) else 0)) * ns_per_tick))
            revs.append(
                RevolutionFlux(
                    index=rev_idx,
                    interval_ns=rev_intervals,
                    index_time_ns=index_ns,
                    data_length_bytes=len(rev_intervals) * 2,
                    data_offset=None,
                )
            )

        if revs:
            tracks.append(
                TrackFlux(
                    track=cyl,
                    side=side,
                    revolutions=revs,
                )
            )

    if not tracks:
        return None

    return SCPImage(
        path=path,
        version=version if 'version' in locals() else 0,
        revolutions_per_track=len(tracks[0].revolutions) if tracks else 0,
        timebase_ns=ns_per_tick,
        tracks=tracks,
        warnings=warnings,
    )


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
