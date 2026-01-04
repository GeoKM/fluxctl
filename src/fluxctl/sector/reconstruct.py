"""Reconstruct sectors from decoded bitstreams.

This module scans MFM bitstreams for IBM PC-style sync patterns (the missing
clock ``0xA1`` preamble encoded as ``0x4489``) and follows the classic
``A1 A1 A1 FE`` ID field / ``A1 A1 A1 FB`` data field layout. The parser keeps
to soft-sectored MFM disks for now, assumes a fixed bit-cell alignment derived
from the sync marks, and will be extended to cover FM and GCR encodings in
future iterations.
"""
from __future__ import annotations

from typing import List, Optional

from ..decoding import Decoder
from ..models import Bitstream, RevolutionFlux
from .models import Sector, TrackSectors
from .reconstruct_gcr import reconstruct_gcr_track
from .reconstruct_fm import reconstruct_fm_track


SYNC_WORD = 0x4489
ID_ADDRESS_MARK = 0xFE
DATA_ADDRESS_MARKS = {0xFB, 0xF8}


def _crc16(data: bytes, poly: int = 0x1021, init: int = 0xFFFF) -> int:
    crc = init
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ poly
            else:
                crc <<= 1
            crc &= 0xFFFF
    return crc & 0xFFFF


def _decode_word(bits: List[int], offset: int) -> Optional[int]:
    if offset + 16 > len(bits):
        return None
    value = 0
    for i in range(16):
        value = (value << 1) | bits[offset + i]
    return value


def _decode_data_byte(bits: List[int], offset: int) -> Optional[int]:
    if offset + 16 > len(bits):
        return None
    data_bits = bits[offset + 1 : offset + 16 : 2]
    value = 0
    for bit in data_bits:
        value = (value << 1) | bit
    return value


def reconstruct_track(
    bitstream: Bitstream, cylinder: int = 0, head: int = 0, expected_sectors: Optional[int] = None
) -> TrackSectors:
    """Parse an IBM-style MFM bitstream into sectors.

    The scanner looks for three consecutive sync words (``0x4489``) that mark
    the missing-clock ``0xA1`` bytes. The following byte decides whether the
    block is an ID Address Mark (``0xFE``) or Data Address Mark (``0xFB``/``0xF8``).
    Data is decoded by stripping clock bits (odd bit positions within each
    16-bit word). CRC16-IBM is calculated over the address mark and payload to
    verify integrity.

    Limitations: assumes a fixed bit-cell alignment detected at the sync mark,
    only handles MFM soft-sectored layouts, and reads a single revolution's
    bitstream without attempting inter-revolution stitching.
    """

    bits = bitstream.bits
    bit_str = "".join("1" if b else "0" for b in bits)
    search_pos = 0
    sectors: List[Sector] = []
    last_header: Optional[tuple[int, int, int, int, bool]] = None
    weak = 0

    pattern = format(SYNC_WORD, "016b")

    while True:
        pos = bit_str.find(pattern, search_pos)
        if pos == -1 or pos + 64 > len(bits):
            break
        if expected_sectors and len(sectors) >= expected_sectors:
            break
        sync_words = 0
        while sync_words < 3 and bit_str[pos + sync_words * 16 : pos + (sync_words + 1) * 16] == pattern:
            sync_words += 1
        if sync_words == 0:
            search_pos = pos + 1
            continue

        marker = _decode_data_byte(bits, pos + sync_words * 16)
        if marker is None:
            break

        if marker == ID_ADDRESS_MARK:
            header_bytes = [_decode_data_byte(bits, pos + (sync_words + 1 + i) * 16) for i in range(4)]
            if any(b is None for b in header_bytes):
                break
            c, h, r, n = [int(b) for b in header_bytes]
            crc_bytes = [_decode_data_byte(bits, pos + (sync_words + 5 + i) * 16) for i in range(2)]
            if any(b is None for b in crc_bytes):
                break
            header_field = bytes([0xA1, 0xA1, 0xA1, marker, c, h, r, n])
            crc_calc = _crc16(header_field)
            crc_read = (int(crc_bytes[0]) << 8) | int(crc_bytes[1])
            last_header = (c, h, r, n, crc_calc == crc_read)
            search_pos = pos + (sync_words + 7) * 16
            continue

        if marker in DATA_ADDRESS_MARKS and last_header:
            c, h, r, n, header_crc_ok = last_header
            data_len = 128 << n
            data_bytes: List[int] = []
            data_offset = pos + (sync_words + 1) * 16
            for i in range(data_len):
                value = _decode_data_byte(bits, data_offset + i * 16)
                if value is None:
                    break
                data_bytes.append(value)
            if len(data_bytes) < data_len:
                break
            crc_offset = data_offset + data_len * 16
            crc_values = [_decode_data_byte(bits, crc_offset + i * 16) for i in range(2)]
            if any(v is None for v in crc_values):
                break
            data_field = bytes([0xA1, 0xA1, 0xA1, marker, *data_bytes])
            crc_calc = _crc16(data_field)
            crc_read = (int(crc_values[0]) << 8) | int(crc_values[1])
            crc_ok = crc_calc == crc_read and header_crc_ok
            if not crc_ok:
                weak += 1
            sectors.append(
                Sector(
                    cylinder=c,
                    head=h,
                    sector_id=r,
                    size_code=n,
                    data=bytes(data_bytes),
                    crc_ok=crc_ok,
                    confidence=bitstream.metrics.confidence or 0.0,
                    deleted=marker == 0xF8,
                    source_revolutions=bitstream.source_revs,
                )
            )
            last_header = None
            search_pos = crc_offset + 2 * 16
            continue

        search_pos = pos + 1

    missing = 0
    if expected_sectors:
        found_ids = {s.sector_id for s in sectors}
        missing = max(expected_sectors - len(found_ids), 0)
        if not sectors:
            default_size_code = 2
            sectors = [
                Sector(
                    cylinder=cylinder,
                    head=head,
                    sector_id=idx,
                    size_code=default_size_code,
                    data=bytes(128 << default_size_code),
                    crc_ok=False,
                    confidence=bitstream.metrics.confidence or 0.0,
                    deleted=False,
                    source_revolutions=bitstream.source_revs,
                )
                for idx in range(1, expected_sectors + 1)
            ]
            weak = expected_sectors
            missing = 0

    return TrackSectors(track=cylinder, head=head, sectors=sectors, weak=weak, missing=missing)


def build_track_sectors(
    rev: RevolutionFlux,
    decoder: Decoder,
    cylinder: int = 0,
    head: int = 0,
    expected_sectors: Optional[int] = None,
    encoding: Optional[str] = None,
) -> TrackSectors:
    """Decode a revolution and reconstruct sectors using the supplied decoder."""

    effective_encoding = encoding or getattr(decoder, "encoding", None)
    if effective_encoding == "gcr" and hasattr(decoder, "set_track"):
        decoder.set_track(cylinder)
    bitstream = decoder.decode_revolution(rev)
    if effective_encoding == "gcr":
        return reconstruct_gcr_track(bitstream, cylinder=cylinder, head=head, expected_sectors=expected_sectors)
    if effective_encoding == "fm":
        return reconstruct_fm_track(bitstream, cylinder=cylinder, head=head, expected_sectors=expected_sectors)
    return reconstruct_track(bitstream, cylinder=cylinder, head=head, expected_sectors=expected_sectors)


__all__ = ["reconstruct_track", "build_track_sectors"]
