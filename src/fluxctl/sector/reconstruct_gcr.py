"""Reconstruct Commodore GCR sectors from decoded bitstreams."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from ..encoding.gcr import decode_gcr_symbols_to_bytes, extract_gcr_symbols_from_bitstream
from ..models import Bitstream
from .models import Sector, TrackSectors

SYNC_THRESHOLD = 40
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
    """Return list of (start, end) bit offsets for sync marks."""

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
    return marks


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


def _xor_checksum(values: bytes) -> int:
    checksum = 0
    for value in values:
        checksum ^= value
    return checksum


def reconstruct_gcr_track(
    bitstream: Bitstream, cylinder: int = 0, head: int = 0, expected_sectors: Optional[int] = None
) -> TrackSectors:
    bits = bitstream.bits
    sync_marks = _find_sync_marks(bits)
    sectors: dict[int, Sector] = {}
    weak = 0

    for idx, (sync_start, sync_end) in enumerate(sync_marks):
        align_offset, _ = _score_alignment(bits, sync_end)
        header_block = _read_block(bits, sync_end + align_offset, 8)
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

        next_sync: Optional[Tuple[int, int]] = None
        for candidate_start, candidate_end in sync_marks[idx + 1 :]:
            if candidate_start > header_block.end_bit:
                next_sync = (candidate_start, candidate_end)
                break
        if next_sync is None:
            continue

        data_align, _ = _score_alignment(bits, next_sync[1])
        total_bytes = 1 + DATA_LENGTH + 1 + TAIL_BYTES
        data_block = _read_block(bits, next_sync[1] + data_align, total_bytes)
        if len(data_block.bytes) < total_bytes:
            continue
        if data_block.bytes[0] != DATA_ID:
            continue
        data_bytes = data_block.bytes[1 : 1 + DATA_LENGTH]
        checksum = data_block.bytes[1 + DATA_LENGTH]
        trailer_bytes = data_block.bytes[1 + DATA_LENGTH + 1 : 1 + DATA_LENGTH + 1 + TAIL_BYTES]
        data_ok = _xor_checksum(data_bytes) == checksum and trailer_bytes == b"\x00\x00"

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

    sector_list = list(sectors.values())
    missing = 0
    if expected_sectors is not None:
        missing = max(expected_sectors - len(sectors), 0)

    return TrackSectors(track=cylinder, head=head, sectors=sector_list, weak=weak, missing=missing)


__all__ = ["reconstruct_gcr_track"]
