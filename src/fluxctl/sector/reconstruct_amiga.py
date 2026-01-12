"""Amiga MFM track reconstruction with odd/even checksum verification."""
from __future__ import annotations

from typing import List, Optional

from ..models import Bitstream
from .models import Sector, TrackSectors
from ..decoding.amiga_pll import AmigaPLLDecoder
from ..decoding.mfm import MFMDecoder

SYNC_WORD = 0x4489
# Amiga sectors contain 544 decoded bytes (after merging odd/even halves):
#   4  header bytes (format, track, sector, sectors-to-end)
#   16 label bytes
#   4  header checksum
#   4  data checksum
#   512 data bytes
#   4  gap/padding bytes (ignored)
DECODED_SECTOR_BYTES = 544
ENCODED_SECTOR_BYTES = DECODED_SECTOR_BYTES * 2  # odd + even halves
# MFM bitstreams from both our simple decoder and the Amiga PLL still include
# clock bits (C D C D ...). Each encoded data byte occupies 16 bits.
BYTES_PER_MFM_BYTE = 16


def _read_word(bits: List[int], offset: int) -> Optional[int]:
    if offset + 16 > len(bits):
        return None
    val = 0
    for i in range(16):
        val = (val << 1) | bits[offset + i]
    return val


def _read_long(bits: List[int], offset: int) -> Optional[int]:
    hi = _read_word(bits, offset)
    lo = _read_word(bits, offset + 16) if hi is not None else None
    if hi is None or lo is None:
        return None
    return (hi << 16) | lo


def _read_mfm_long(bits: List[int], offset: int) -> Optional[int]:
    """Decode a 32-bit Amiga word from 64 raw MFM bits (clock+data).

    The Amiga odd/even encoding stores each longword as raw MFM with a clock
    bit preceding every data bit. This helper drops the clock bits by sampling
    every other bit, mirroring :func:`_decode_data_byte` in the generic MFM
    reconstructor.
    """

    if offset + 64 > len(bits):
        return None
    value = 0
    for idx in range(offset + 1, offset + 64, 2):
        value = (value << 1) | bits[idx]
    return value


def _read_mfm_long_bytes(bits: List[int], offset: int) -> Optional[int]:
    """Decode a 32-bit word by first extracting four MFM bytes.

    This mirrors the IBM-style ``_decode_data_byte`` routine: every 16-bit
    word in ``bits`` holds an 8-bit data byte with clock bits stripped. Amiga
    sectors are encoded in the same clock/data pattern, only the odd/even bit
    split is Amiga-specific.
    """

    words = []
    for i in range(4):
        word = _read_word(bits, offset + i * 16)
        if word is None:
            return None
        # drop clock bits: take every other bit starting at bit 0x0080 (mask 0b0101010101010101)
        data = 0
        for bit_index in range(1, 16, 2):
            data = (data << 1) | ((word >> (15 - bit_index)) & 1)
        words.append(data)
    return (words[0] << 24) | (words[1] << 16) | (words[2] << 8) | words[3]


def _deinterleave(odd: int, even: int) -> int:
    odd &= 0x55555555
    even &= 0x55555555
    return ((odd << 1) | even) & 0xFFFFFFFF


def _xor(words: List[int]) -> int:
    acc = 0
    for w in words:
        acc ^= w
    return acc


def _decode_data_byte(bits: List[int], offset: int) -> Optional[int]:
    """Decode a single data byte from clock+data MFM bits."""

    if offset + 16 > len(bits):
        return None
    data_bits = bits[offset + 1 : offset + 16 : 2]  # drop clock bits
    val = 0
    for b in data_bits:
        val = (val << 1) | b
    return val


def _decode_odd_even(odd: List[int], even: List[int]) -> bytes:
    """Merge odd/even encoded Amiga bytes into original bytes."""

    return bytes(((o << 1) & 0xAA) | (e & 0x55) for o, e in zip(odd, even))


def _amiga_checksum(payload: bytes) -> int:
    """Return Amiga XOR checksum masked to odd bit lanes."""

    acc = 0
    for i in range(0, len(payload), 4):
        acc ^= int.from_bytes(payload[i : i + 4], "big")
    return (acc ^ (acc >> 1)) & 0x55555555


def reconstruct_amiga_track(bitstream: Bitstream, cylinder: int = 0, head: int = 0) -> TrackSectors:
    """Decode a single revolution of an Amiga track."""

    bits = bitstream.bits
    bit_str = "".join("1" if b else "0" for b in bits)
    pattern = format(SYNC_WORD, "016b")
    pos = 0
    best_by_id: dict[int, Sector] = {}

    while True:
        sync_pos = bit_str.find(pattern, pos)
        if sync_pos == -1 or sync_pos + 64 > len(bits):
            break
        if _read_word(bits, sync_pos) != SYNC_WORD or _read_word(bits, sync_pos + 16) != SYNC_WORD:
            pos = sync_pos + 1
            continue

        # Decode 544 bytes worth of MFM-encoded data (odd+even halves).
        start = sync_pos + 32
        encoded_bytes: List[int] = []
        for i in range(ENCODED_SECTOR_BYTES):
            b = _decode_data_byte(bits, start + i * BYTES_PER_MFM_BYTE)
            if b is None:
                break
            encoded_bytes.append(b)
        if len(encoded_bytes) < ENCODED_SECTOR_BYTES:
            pos = sync_pos + 1
            continue

        odd = encoded_bytes[:DECODED_SECTOR_BYTES]
        even = encoded_bytes[DECODED_SECTOR_BYTES:]
        decoded = _decode_odd_even(odd, even)

        header = decoded[0:4]
        label = decoded[4:20]
        hchk = int.from_bytes(decoded[20:24], "big")
        dchk = int.from_bytes(decoded[24:28], "big")
        data_bytes = decoded[28 : 28 + 512]

        format_byte, track_byte, sector_id, togo = header
        header_ok = format_byte == 0xFF
        if header_ok:
            header_ok = _amiga_checksum(header + label) == hchk
        data_ok = _amiga_checksum(data_bytes) == dchk
        crc_ok = header_ok and data_ok

        candidate = Sector(
            cylinder=cylinder,
            head=head,
            sector_id=sector_id,
            size_code=2,
            data=data_bytes,
            crc_ok=crc_ok,
            confidence=bitstream.metrics.confidence or 0.0,
            deleted=False,
            source_revolutions=bitstream.source_revs,
        )

        existing = best_by_id.get(sector_id)
        if existing is None or (not existing.crc_ok and candidate.crc_ok) or (
            existing.crc_ok == candidate.crc_ok and candidate.confidence > existing.confidence
        ):
            best_by_id[sector_id] = candidate

        pos = start + ENCODED_SECTOR_BYTES * BYTES_PER_MFM_BYTE

    sectors = sorted(best_by_id.values(), key=lambda s: s.sector_id)
    return TrackSectors(track=cylinder, head=head, sectors=sectors, weak=0, missing=0)


def reconstruct_amiga_greaseweazle(revolutions, track: int, head: int, timebase_ns: float = 25.0):
    """Decode an Amiga track using Greaseweazle's PLL/codec if available.

    Accepts a single RevolutionFlux or an iterable of them. Returns ``None``
    when Greaseweazle is not installed.
    """

    try:
        from greaseweazle.codec.amiga.amigados import AmigaDOS_DD, AmigaDOS_HD
        from greaseweazle.flux import Flux
    except Exception:
        return None

    revs = revolutions if isinstance(revolutions, list) else [revolutions]
    codec_cls = AmigaDOS_HD if track >= 160 else AmigaDOS_DD

    merged: dict[int, Sector] = {}
    for rev in revs:
        ticks = [max(1, int(round(ns / timebase_ns))) for ns in rev.interval_ns]
        flux = Flux(index_list=[sum(ticks)], flux_list=ticks, sample_freq=40_000_000, index_cued=True)
        codec = codec_cls(track, head)
        codec.decode_flux(flux)
        for sec_id, sector in enumerate(codec.sector):
            if sector is None:
                continue
            _, data = sector
            candidate = Sector(
                cylinder=track,
                head=head,
                sector_id=sec_id,
                size_code=2,
                data=data,
                crc_ok=True,
                confidence=1.0,
                deleted=False,
                source_revolutions=[rev.index],
            )
            existing = merged.get(sec_id)
            if existing is None or (not existing.crc_ok and candidate.crc_ok):
                merged[sec_id] = candidate
        if len(merged) >= 11:
            break

    missing = max(11 - len(merged), 0)
    return TrackSectors(track=track, head=head, sectors=sorted(merged.values(), key=lambda s: s.sector_id), weak=0, missing=missing)


def reconstruct_amiga_with_pll(revolutions, track: int, head: int, timebase_ns: float = 25.0) -> TrackSectors:
    """Decode using Greaseweazle if available, otherwise merge internal MFM results."""

    revs = list(revolutions) if isinstance(revolutions, (list, tuple)) else [revolutions]

    try:
        from greaseweazle.codec.amiga.amigados import AmigaDOS_DD, AmigaDOS_HD
        from greaseweazle.flux import Flux

        codec_cls = AmigaDOS_HD if track >= 160 else AmigaDOS_DD
        merged: dict[int, Sector] = {}
        for rev in revs:
            ticks = [max(1, int(round(ns / 25.0))) for ns in rev.interval_ns]
            flux = Flux(index_list=[sum(ticks)], flux_list=ticks, sample_freq=40_000_000, index_cued=True)
            codec = codec_cls(track, head)
            codec.decode_flux(flux)
            for sec_id, sector in enumerate(codec.sector):
                if sector is None:
                    continue
                _, data = sector
                candidate = Sector(
                    cylinder=track,
                    head=head,
                    sector_id=sec_id,
                    size_code=2,
                    data=data,
                    crc_ok=True,
                    confidence=1.0,
                    deleted=False,
                    source_revolutions=[rev.index],
                )
                existing = merged.get(sec_id)
                if existing is None or (not existing.crc_ok and candidate.crc_ok):
                    merged[sec_id] = candidate
            if len(merged) >= 11:
                break
        missing = max(11 - len(merged), 0)
        return TrackSectors(track=track, head=head, sectors=sorted(merged.values(), key=lambda s: s.sector_id), weak=0, missing=missing)
    except Exception:
        decoder = MFMDecoder()
        merged: dict[int, Sector] = {}
        for rev in revs:
            bitstream = decoder.decode_revolution(rev)
            candidate = reconstruct_amiga_track(bitstream, cylinder=track, head=head)
            for sec in candidate.sectors:
                existing = merged.get(sec.sector_id)
                if existing is None or (not existing.crc_ok and sec.crc_ok) or (
                    existing.crc_ok == sec.crc_ok and sec.confidence > existing.confidence
                ):
                    merged[sec.sector_id] = sec
            if merged and len(merged) >= 11:
                break
        return TrackSectors(track=track, head=head, sectors=sorted(merged.values(), key=lambda s: s.sector_id), weak=0, missing=0)
