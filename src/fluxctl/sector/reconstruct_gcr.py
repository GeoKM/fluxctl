"""Reconstruct Commodore GCR sectors from decoded bitstreams."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple
import math

# PLL tuning constants borrowed conceptually from Greaseweazle's PLLTrack.
PLL_MIN = 3.5e-6  # 3.5us
PLL_MAX = 5.5e-6  # 5.5us
PLL_BETA = 0.65   # phase adjust
PLL_ALPHA = 0.30  # freq adjust

from ..encoding.gcr import decode_gcr_symbols_to_bytes, extract_gcr_symbols_from_bitstream
from ..models import Bitstream
from .models import Sector, TrackSectors

SYNC_THRESHOLD = 32
PLL_MIN = 3.5e-6
PLL_MAX = 5.5e-6
PLL_ADJ = 0.2
HEADER_ID = 0x08
DATA_ID = 0x07
DATA_LENGTH = 256
TAIL_BYTES = 2


@dataclass
class GCRBlock:
    start_bit: int
    end_bit: int
    bytes: bytes
    errors: int


def _find_sync_marks(bits: Sequence[int], threshold: int = SYNC_THRESHOLD) -> List[tuple[int, int]]:
    """Return list of (start, end) bit offsets for sync marks.

    Commodore GCR sync is a long run of 1s. We collect all runs >= threshold
    and also merge adjacent runs separated by a short gap (<8 bits) to handle
    slight PLL drift.
    """

    marks: List[tuple[int, int]] = []
    run = 0
    start = 0
    for idx, bit in enumerate(bits):
        if bit == 1:
            if run == 0:
                start = idx
            run += 1
        else:
            if run >= threshold:
                marks.append((start, idx))
            run = 0
    if run >= threshold:
        marks.append((start, len(bits)))

    # Merge marks separated by small gaps to reduce false boundaries.
    merged: List[tuple[int, int]] = []
    for mark in marks:
        if not merged:
            merged.append(mark)
            continue
        prev_start, prev_end = merged[-1]
        if mark[0] - prev_end <= 8:
            merged[-1] = (prev_start, mark[1])
        else:
            merged.append(mark)
    return merged


def _score_alignment(bits: Sequence[int], start_bit: int, sample_symbols: int = 40) -> tuple[int, int]:
    """Return best alignment offset and associated error count."""

    best_offset = 0
    best_errors = float("inf")
    for offset in range(5):
        symbols = extract_gcr_symbols_from_bitstream(bits, start_bit + offset, sample_symbols)
        _, errors = decode_gcr_symbols_to_bytes(symbols)
        if errors < best_errors:
            best_errors = errors
            best_offset = offset
    return best_offset, int(best_errors if best_errors != float("inf") else 0)


def _read_block(bits: Sequence[int], start_bit: int, byte_count: int) -> GCRBlock:
    symbols = extract_gcr_symbols_from_bitstream(bits, start_bit, byte_count * 2)
    decoded, errors = decode_gcr_symbols_to_bytes(symbols)
    consumed = len(symbols) * 5
    return GCRBlock(start_bit=start_bit, end_bit=start_bit + consumed, bytes=decoded, errors=errors)


def _decode_aligned_stream(bits: Sequence[int], offset: int) -> tuple[bytes, int, int]:
    symbol_count = (len(bits) - offset) // 5
    symbols = extract_gcr_symbols_from_bitstream(bits, offset, symbol_count)
    decoded, errors = decode_gcr_symbols_to_bytes(symbols)
    valid_symbols = len(symbols) - errors
    return decoded, valid_symbols, errors


def score_gcr_alignment(bits: Sequence[int]) -> tuple[int, int]:
    """Return the best-case valid symbol count and error tally for ``bits``.

    Alignment is tested across the five possible starting offsets within a
    5-bit GCR cell. The highest valid symbol count wins; ties fall back to the
    lowest error count.
    """

    best_valid = -1
    best_errors = float("inf")
    for offset in range(5):
        _, valid_symbols, errors = _decode_aligned_stream(bits, offset)
        if valid_symbols > best_valid or (valid_symbols == best_valid and errors < best_errors):
            best_valid = valid_symbols
            best_errors = errors
    return best_valid, int(best_errors if best_errors != float("inf") else 0)


def extract_best_gcr_nibble_stream(bitstream: Bitstream) -> bytes:
    """Return a decoded byte stream representing one GCR revolution.

    The helper scans all possible bit-cell alignments and decodes symbols to
    bytes, choosing the alignment with the highest number of valid symbols.
    """

    bits = bitstream.bits
    best_payload = b""
    best_valid = -1
    best_errors = float("inf")
    for offset in range(5):
        decoded, valid_symbols, errors = _decode_aligned_stream(bits, offset)
        if valid_symbols > best_valid or (valid_symbols == best_valid and errors < best_errors):
            best_valid = valid_symbols
            best_errors = errors
            best_payload = decoded
    return best_payload


def _xor_checksum(values: bytes) -> int:
    checksum = 0
    for value in values:
        checksum ^= value
    return checksum


def reconstruct_gcr_greaseweazle(
    revolutions,
    cylinder: int,
    head: int,
    expected_sectors: Optional[int] = None,
    timebase_ns: float = 25.0,
):
    """Decode a GCR track using Greaseweazle's codec if available."""

    try:
        from greaseweazle.codec.commodore.c64_gcr import C64GCR, C64GCRDef
        from greaseweazle.flux import Flux
    except Exception:
        return None

    revs = revolutions if isinstance(revolutions, list) else [revolutions]
    merged: dict[int, Sector] = {}
    # Commodore 1541 uses zone-dependent bitcell; default clock 4.0us
    cell_ns = 4000.0
    track_1based = cylinder + 1
    if track_1based <= 17:
        cell_ns = 3250.0
    elif track_1based <= 24:
        cell_ns = 3500.0
    elif track_1based <= 30:
        cell_ns = 3750.0

    def _codec():
        td = C64GCRDef("commodore.1541")
        td.add_param("secs", expected_sectors or 21)
        td.add_param("clock", cell_ns / 1000.0)  # C64GCRDef expects usec
        td.finalise()
        return C64GCR(cylinder, head, td)

    for rev in revs:
        intervals = getattr(rev, "interval_ns", None) or getattr(rev, "intervals", None)
        if not intervals:
            continue
        ticks = [max(1, int(round(ns / timebase_ns))) for ns in intervals]
        flux = Flux(index_list=[sum(ticks)], flux_list=ticks, sample_freq=40_000_000, index_cued=True)
        codec = _codec()
        codec.decode_flux(flux)
        for sec_id, sec_bytes in enumerate(codec.sector):
            if sec_bytes is None:
                continue
            candidate = Sector(
                cylinder=cylinder,
                head=head,
                sector_id=sec_id,
                size_code=1,
                data=sec_bytes,
                crc_ok=True,
                confidence=1.0,
                deleted=False,
                source_revolutions=[getattr(rev, "index", 0)],
            )
            existing = merged.get(sec_id)
            if existing is None or not existing.crc_ok:
                merged[sec_id] = candidate
        if expected_sectors and len(merged) >= expected_sectors:
            break

    if not merged:
        return None
    missing = max((expected_sectors or len(merged)) - len(merged), 0)
    return TrackSectors(
        track=cylinder, head=head, sectors=sorted(merged.values(), key=lambda s: s.sector_id), weak=0, missing=missing
    )


def _pll_bits(intervals_ns: Sequence[int]) -> List[int]:
    """PLL to turn flux intervals (ns) into bit cells for GCR.

    Simplified from Greaseweazle's PLLTrack: maintain phase and period, adjust
    period (alpha) and phase (beta) toward measured intervals. This yields a
    more stable bit stream for noisy 1541 captures than naive sampling.
    """

    if not intervals_ns:
        return []

    # Initial clock: median interval divided by 2 (approx 4us per bitcell).
    base = sorted(intervals_ns)[len(intervals_ns) // 2] / 2
    clock = max(min(base, PLL_MAX * 1e9), PLL_MIN * 1e9)  # store in ns
    phase = 0.0
    bits: List[int] = []

    for t in intervals_ns:
        # Advance phase by interval.
        phase += t
        # Emit zeros until phase crosses a clock boundary.
        while phase >= clock:
            bits.append(0)
            phase -= clock
        # Flux reversal -> set last bit to 1.
        if bits:
            bits[-1] = 1
        else:
            bits.append(1)
        # Adjust clock toward current interval (per cell) and nudge phase.
        cells = max(1, round(t / clock))
        measured = t / cells
        clock += (measured - clock) * PLL_ALPHA
        clock = max(min(clock, PLL_MAX * 1e9), PLL_MIN * 1e9)
        phase += (measured - clock) * PLL_BETA

    return bits


def _fallback_parse_decoded_stream(
    bitstream: Bitstream,
    cylinder: int,
    head: int,
    expected_sectors: Optional[int],
    tracknr_expected: Optional[int] = None,
) -> TrackSectors:
    """Fallback parser: work on fully decoded byte stream, sync-search only.

    This is more permissive than the main parser and can recover sectors from
    noisy captures where symbol alignment varies. It scans for runs of 0xFF
    (sync), expects a header starting with 0x08 and data starting with 0x07,
    and uses XOR checksums per 1541 format.
    """

    decoded = extract_best_gcr_nibble_stream(bitstream)
    sectors: dict[int, Sector] = {}

    idx = 0
    n = len(decoded)
    while idx < n:
        # Require at least 3 sync bytes to reduce false positives.
        if decoded[idx] != 0xFF:
            idx += 1
            continue
        sync_start = idx
        while idx < n and decoded[idx] == 0xFF:
            idx += 1
        if idx - sync_start < 3:
            continue
        # Expect header id 0x08
        if idx + 7 >= n or decoded[idx] != HEADER_ID:
            continue
        hdr = decoded[idx : idx + 8]
        sum_hdr = 0
        for b in hdr[1:6]:
            sum_hdr ^= b
        if sum_hdr != 0:
            continue
        sector_id = hdr[2]
        tracknr = hdr[3]
        disk_id = (hdr[4] << 8) | hdr[5]
        if tracknr_expected is not None and tracknr != tracknr_expected:
            continue
        if expected_sectors and sector_id >= expected_sectors:
            continue

        # Find following data sync (0x07) within next 120 bytes.
        search = decoded[idx + 8 : idx + 140]
        if 0x07 not in search:
            continue
        ds_off = search.index(0x07)
        data_start = idx + 8 + ds_off
        if data_start + 260 > n:
            continue
        data_blk = decoded[data_start : data_start + 260]
        data_checksum = 0
        for b in data_blk[1:258]:
            data_checksum ^= b
        if data_checksum != data_blk[258]:
            crc_ok = False
        else:
            crc_ok = True
        payload = bytes(data_blk[1:257])
        sector = Sector(
            cylinder=cylinder,
            head=head,
            sector_id=sector_id,
            size_code=1,
            data=payload,
            crc_ok=crc_ok,
            confidence=bitstream.metrics.confidence or 0.0,
            deleted=False,
            source_revolutions=bitstream.source_revs,
        )
        # Keep highest-confidence / crc_ok version
        existing = sectors.get(sector_id)
        if existing is None or (sector.crc_ok and not existing.crc_ok) or (
            sector.crc_ok == existing.crc_ok and sector.confidence > existing.confidence
        ):
            sectors[sector_id] = sector

    return TrackSectors(track=cylinder, head=head, sectors=list(sectors.values()))


def reconstruct_gcr_track(
    bitstream: Bitstream, cylinder: int = 0, head: int = 0, expected_sectors: Optional[int] = None
) -> TrackSectors:
    # Try Greaseweazle codec if available and intervals present.
    if hasattr(bitstream, "intervals"):
        gw = reconstruct_gcr_greaseweazle(
            [bitstream],
            cylinder,
            head,
            expected_sectors=expected_sectors,
            timebase_ns=getattr(bitstream, "timebase_ns", 25.0),
        )
        if gw is not None:
            return gw

    bits = bitstream.bits
    # If the bitstream has no bits but we have raw intervals, attempt PLL re-decode.
    if (not bits or len(bits) < 1000) and hasattr(bitstream, "intervals"):
        try:
            bits = _pll_bits(bitstream.intervals)  # type: ignore[arg-type]
        except Exception:
            bits = []
    # Find sync marks; if none are found at the default threshold, retry with
    # a looser threshold to cope with captures that have shorter sync runs.
    sync_marks = _find_sync_marks(bits, threshold=SYNC_THRESHOLD)
    if not sync_marks and SYNC_THRESHOLD > 12:
        sync_marks = _find_sync_marks(bits, threshold=SYNC_THRESHOLD // 2)
    sectors: dict[int, Sector] = {}
    weak = 0

    for idx, (sync_start, sync_end) in enumerate(sync_marks):
        best_header: Optional[tuple[GCRBlock, int, int, int, int, bool]] = None
        best_score: Optional[tuple[int, int]] = None
        for offset in range(15):
            header_block = _read_block(bits, sync_end + offset, 8)
            if len(header_block.bytes) < 8 or header_block.bytes[0] != HEADER_ID:
                continue
            header_checksum = header_block.bytes[1]
            sector_id = header_block.bytes[2]
            header_track = header_block.bytes[3]
            id_lo = header_block.bytes[4]
            id_hi = header_block.bytes[5]
            trailer = header_block.bytes[6:8]
            header_ok = (_xor_checksum(bytes([sector_id, header_track, id_lo, id_hi])) == header_checksum)
            header_ok = header_ok and trailer == b"\x0f\x0f"
            score = (0 if header_ok else 1, header_block.errors)
            if best_score is None or score < best_score:
                best_score = score
                best_header = (header_block, sector_id, header_track, id_lo, id_hi, header_ok)
                if header_ok:
                    break
        if best_header is None:
            continue
        header_block, sector_id, header_track, id_lo, id_hi, header_ok = best_header

        next_sync: Optional[Tuple[int, int]] = None
        for candidate_start, candidate_end in sync_marks[idx + 1 :]:
            if candidate_start > header_block.end_bit:
                next_sync = (candidate_start, candidate_end)
                break
        if next_sync is None:
            continue

        total_bytes = 1 + DATA_LENGTH + 1 + TAIL_BYTES
        best_data: Optional[tuple[GCRBlock, bytes, bool]] = None
        best_data_score: Optional[tuple[int, int]] = None
        for offset in range(15):
            data_block = _read_block(bits, next_sync[1] + offset, total_bytes)
            if len(data_block.bytes) < total_bytes:
                continue
            if data_block.bytes[0] != DATA_ID:
                continue
            data_bytes = data_block.bytes[1 : 1 + DATA_LENGTH]
            checksum = data_block.bytes[1 + DATA_LENGTH]
            trailer_bytes = data_block.bytes[1 + DATA_LENGTH + 1 : 1 + DATA_LENGTH + 1 + TAIL_BYTES]
            data_ok = _xor_checksum(data_bytes) == checksum and trailer_bytes == b"\x0f\x0f"
            score = (0 if data_ok else 1, data_block.errors)
            if best_data_score is None or score < best_data_score:
                best_data_score = score
                best_data = (data_block, data_bytes, data_ok)
                if data_ok:
                    break
        if best_data is None:
            continue
        _, data_bytes, data_ok = best_data

        crc_ok = data_ok and header_ok
        confidence = bitstream.metrics.confidence or 0.0
        sector = Sector(
            cylinder=cylinder,
            head=head,
            sector_id=sector_id,
            size_code=1,
            data=data_bytes,
            crc_ok=crc_ok,
            confidence=confidence,
            deleted=False,
            source_revolutions=bitstream.source_revs,
        )

        existing = sectors.get(sector_id)
        if existing is None or (sector.crc_ok and not existing.crc_ok) or (
            sector.crc_ok == existing.crc_ok and sector.confidence > existing.confidence
        ):
            sectors[sector_id] = sector
        if not crc_ok:
            weak += 1

    # Fallback: byte-stream scan when no structured sectors decoded.
    if not sectors:
        tracknr_expected = head * 35 + cylinder + 1
        fallback = _fallback_parse_decoded_stream(
            bitstream,
            cylinder,
            head,
            expected_sectors=expected_sectors,
            tracknr_expected=tracknr_expected,
        )
        return fallback

    sector_list = list(sectors.values())
    missing = 0
    if expected_sectors is not None:
        missing = max(expected_sectors - len(sectors), 0)

    return TrackSectors(track=cylinder, head=head, sectors=sector_list, weak=weak, missing=missing)


__all__ = ["extract_best_gcr_nibble_stream", "reconstruct_gcr_track", "score_gcr_alignment"]

# TODO: 1571 CP/M non-boot disks can be MFM-formatted data while boot media
# remain GCR; once such fixtures exist, confirm probe/detect handling and
# document mixed-encoding workflows.
