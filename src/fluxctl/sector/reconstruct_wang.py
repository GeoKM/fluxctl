"""Sector reconstruction for Wang OIS/100 hard-sector FM captures."""
from __future__ import annotations

from typing import Sequence

from ..decoding.wang import wang_crc16
from ..exceptions import FluxDecodeError
from ..models import RevolutionFlux
from .models import Sector, TrackSectors


WANG_CELL_NS = 2000.0
WANG_HEADER_MARK = 0x03
WANG_PAYLOAD_OFFSET = 24
WANG_PAYLOAD_SIZE = 256
WANG_CRC_OFFSET = WANG_PAYLOAD_OFFSET + WANG_PAYLOAD_SIZE


def _fm_bytes(intervals: Sequence[int], phase: int, cell_ns: float = WANG_CELL_NS) -> bytes:
    bits: list[int] = []
    for interval in intervals:
        cells = max(1, int(round(interval / cell_ns)))
        bits.extend([0] * (cells - 1))
        bits.append(1)

    output = bytearray()
    pos = phase
    while pos + 16 <= len(bits):
        value = 0
        for index in range(8):
            value = (value << 1) | bits[pos + 1 + (index * 2)]
        output.append(value)
        pos += 16
    return bytes(output)


def _decode_revolution(revolution: RevolutionFlux, track: int) -> list[Sector]:
    best: dict[int, Sector] = {}
    raw = revolution.interval_ns
    for phase in range(16):
        decoded = _fm_bytes(raw, phase)
        for position in range(len(decoded) - WANG_CRC_OFFSET - 2):
            if decoded[position] != WANG_HEADER_MARK:
                continue
            if decoded[position + 1] != track:
                continue
            sector_id = decoded[position + 2]
            if sector_id >= 16:
                continue
            payload_start = position + WANG_PAYLOAD_OFFSET
            payload_end = payload_start + WANG_PAYLOAD_SIZE
            crc_start = position + WANG_CRC_OFFSET
            payload = decoded[payload_start:payload_end]
            stored_crc = int.from_bytes(decoded[crc_start : crc_start + 2], "big")
            if len(payload) != WANG_PAYLOAD_SIZE or wang_crc16(payload) != stored_crc:
                continue
            candidate = Sector(
                cylinder=track,
                head=0,
                sector_id=sector_id,
                size_code=1,
                data=payload,
                crc_ok=True,
                confidence=1.0,
                source_revolutions=[revolution.index],
            )
            best.setdefault(sector_id, candidate)
    return sorted(best.values(), key=lambda sector: sector.sector_id)


def reconstruct_wang_track(
    revolutions: Sequence[RevolutionFlux],
    track: int,
    head: int = 0,
    expected_sectors: int = 16,
) -> TrackSectors:
    """Decode and merge Wang sectors from both SCP splice windows."""

    if head != 0:
        return TrackSectors(track=track, head=head, sectors=[], missing=expected_sectors)
    candidates: list[Sector] = []
    for revolution in revolutions:
        if revolution.interval_ns:
            candidates.extend(_decode_revolution(revolution, track))
    merged: dict[int, Sector] = {}
    for sector in candidates:
        merged.setdefault(sector.sector_id, sector)
    sectors = sorted(merged.values(), key=lambda sector: sector.sector_id)
    return TrackSectors(
        track=track,
        head=head,
        sectors=sectors,
        missing=max(expected_sectors - len(sectors), 0),
    )


def wang_track_score(revolutions: Sequence[RevolutionFlux], track: int) -> int:
    """Return the number of CRC-valid Wang sectors in a track sample."""

    return len({sector.sector_id for revolution in revolutions for sector in _decode_revolution(revolution, track)})


__all__ = ["reconstruct_wang_track", "wang_track_score"]
