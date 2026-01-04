"""Reconstruct FM sectors from decoded bitstreams."""
from __future__ import annotations

from typing import List, Optional

from ..models import Bitstream
from .models import Sector, TrackSectors


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


def _decode_data_byte(bits: List[int], offset: int) -> Optional[int]:
    if offset + 16 > len(bits):
        return None
    data_bits = bits[offset + 1 : offset + 16 : 2]
    value = 0
    for bit in data_bits:
        value = (value << 1) | bit
    return value

ID_ADDRESS_MARK = 0xFE
DATA_ADDRESS_MARKS = {0xFB, 0xF8}


def _decode_fm_bytes(bits: List[int], offset: int) -> List[int]:
    bytes_out: List[int] = []
    pos = offset
    while pos + 16 <= len(bits):
        value = _decode_data_byte(bits, pos)
        if value is None:
            break
        bytes_out.append(value)
        pos += 16
    return bytes_out


def _scan_fm_stream(bytes_out: List[int], cylinder: int, head: int, expected_sectors: Optional[int]) -> TrackSectors:
    sectors: List[Sector] = []
    last_header: Optional[tuple[int, int, int, int, bool]] = None
    weak = 0

    idx = 0
    while idx < len(bytes_out):
        value = bytes_out[idx]
        if expected_sectors and len(sectors) >= expected_sectors:
            break
        if value == ID_ADDRESS_MARK and idx + 6 < len(bytes_out):
            c, h, r, n = bytes_out[idx + 1 : idx + 5]
            crc_hi = bytes_out[idx + 5]
            crc_lo = bytes_out[idx + 6]
            header_field = bytes([value, c, h, r, n])
            crc_calc = _crc16(header_field)
            crc_read = (crc_hi << 8) | crc_lo
            last_header = (c, h, r, n, crc_calc == crc_read)
            idx += 7
            continue
        if value in DATA_ADDRESS_MARKS and last_header:
            c, h, r, n, header_crc_ok = last_header
            data_len = 128 << n
            if idx + 1 + data_len + 2 > len(bytes_out):
                break
            data_bytes = bytes(bytes_out[idx + 1 : idx + 1 + data_len])
            crc_hi = bytes_out[idx + 1 + data_len]
            crc_lo = bytes_out[idx + 2 + data_len]
            crc_read = (crc_hi << 8) | crc_lo
            data_field = bytes([value]) + data_bytes
            crc_calc = _crc16(data_field)
            crc_ok = (crc_calc == crc_read) and header_crc_ok
            if not crc_ok:
                weak += 1
            sectors.append(
                Sector(
                    cylinder=c,
                    head=h,
                    sector_id=r,
                    size_code=n,
                    data=data_bytes,
                    crc_ok=crc_ok,
                    confidence=1.0,
                    deleted=value == 0xF8,
                    source_revolutions=[],
                )
            )
            last_header = None
            idx += 1 + data_len + 2
            continue
        idx += 1

    missing = 0
    if expected_sectors:
        found_ids = {s.sector_id for s in sectors}
        missing = max(expected_sectors - len(found_ids), 0)

    return TrackSectors(track=cylinder, head=head, sectors=sectors, weak=weak, missing=missing)


def reconstruct_fm_track(
    bitstream: Bitstream, cylinder: int = 0, head: int = 0, expected_sectors: Optional[int] = None
) -> TrackSectors:
    bits = bitstream.bits
    candidates = []
    for offset in (0, 1):
        bytes_out = _decode_fm_bytes(bits, offset)
        track = _scan_fm_stream(bytes_out, cylinder, head, expected_sectors)
        candidates.append(track)
    best = max(candidates, key=lambda t: (len(t.sectors), -t.weak))
    return best


__all__ = ["reconstruct_fm_track"]
