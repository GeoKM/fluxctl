"""Deterministic logical-sector to flux synthesis.

The encoders in this module recreate standards-compliant logical track data.
They intentionally do not claim to reproduce original analogue timing,
write-splice placement, weak bits, or protection-specific bit patterns.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from ..apple2 import APPLE2_GCR_DECODE
from ..exceptions import ExportError
from ..models import LayoutDescriptor
from ..sector.models import Sector, TrackSectors
from .gcr import GCR_ENCODE_4TO5


SCP_TICK_NS = 25.0


@dataclass(frozen=True, slots=True)
class SyntheticTrackFlux:
    cylinder: int
    head: int
    index_ticks: int
    intervals_ticks: tuple[int, ...]


def supported_scp_layout(layout: LayoutDescriptor | None) -> tuple[bool, str]:
    if layout is None:
        return False, "Native SCP export requires an explicit or detected layout"
    encoding = layout.encoding.lower()
    layout_id = layout.layout_id.lower()
    if encoding not in {"fm", "mfm", "gcr", "apple2_gcr"}:
        return False, f"Native SCP export does not support {layout.encoding} encoding"
    if encoding == "gcr" and not layout_id.startswith("commodore_gcr_"):
        return False, "Native SCP GCR export currently supports Commodore GCR layouts only"
    unsupported_markers = (
        "wang_", "hs32", "hard_sector", "displaywriter", "rx02", "xdf",
        "cpmplus", "mmfm", "apple_gcr", "victor_", "northstar_",
    )
    if any(marker in layout_id for marker in unsupported_markers):
        return False, "This layout needs a specialised physical track encoder before SCP export is safe"
    return True, ""


def synthesize_track_flux(track: TrackSectors, layout: LayoutDescriptor) -> SyntheticTrackFlux:
    supported, reason = supported_scp_layout(layout)
    if not supported:
        raise ExportError(reason)
    rpm = float(layout.rpm_nominal or 300)
    revolution_ns = 60_000_000_000.0 / rpm
    encoding = layout.encoding.lower()
    if layout.layout_id.startswith("amiga_"):
        bits, cell_ns = _encode_amiga_track(track, revolution_ns)
    elif encoding == "mfm":
        bits, cell_ns = _encode_ibm_track(track, layout, revolution_ns, mfm=True)
    elif encoding == "fm":
        bits, cell_ns = _encode_ibm_track(track, layout, revolution_ns, mfm=False)
    elif encoding == "gcr":
        bits, cell_ns = _encode_commodore_track(track, layout, revolution_ns)
    else:
        bits, cell_ns = _encode_apple2_track(track, revolution_ns)
    intervals = _circular_flux_intervals(bits, cell_ns)
    return SyntheticTrackFlux(
        cylinder=track.track,
        head=track.head,
        index_ticks=max(1, int(round(revolution_ns / SCP_TICK_NS))),
        intervals_ticks=tuple(intervals),
    )


def _crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def _byte_bits(data: bytes) -> list[int]:
    return [(byte >> shift) & 1 for byte in data for shift in range(7, -1, -1)]


def _mfm_bytes(data: bytes, previous_data: int = 0) -> tuple[list[int], int]:
    bits: list[int] = []
    prev = previous_data
    for byte in data:
        for shift in range(7, -1, -1):
            value = (byte >> shift) & 1
            bits.extend((1 if prev == 0 and value == 0 else 0, value))
            prev = value
    return bits, prev


def _fm_bytes(data: bytes) -> list[int]:
    bits: list[int] = []
    for byte in data:
        for shift in range(7, -1, -1):
            bits.extend((1, (byte >> shift) & 1))
    return bits


def _mfm_sync() -> list[int]:
    return _byte_bits(b"\x44\x89" * 3)


def _append_mfm(bits: list[int], data: bytes, previous_data: int = 0) -> int:
    encoded, previous_data = _mfm_bytes(data, previous_data)
    bits.extend(encoded)
    return previous_data


def _encode_ibm_track(
    track: TrackSectors,
    layout: LayoutDescriptor,
    revolution_ns: float,
    *,
    mfm: bool,
) -> tuple[list[int], float]:
    gap3 = int(layout.gap3_hint or (54 if mfm else 11))
    bits: list[int] = []
    if mfm:
        previous = _append_mfm(bits, b"\x4e" * 40)
        for sector in track.sectors:
            header = bytes((sector.cylinder & 0xFF, sector.head & 0xFF, sector.sector_id & 0xFF, sector.size_code & 0xFF))
            previous = _append_mfm(bits, b"\x00" * 12, previous)
            bits.extend(_mfm_sync())
            field = b"\xfe" + header
            crc = _crc16(b"\xa1\xa1\xa1" + field)
            previous = _append_mfm(bits, field + crc.to_bytes(2, "big"), 1)
            previous = _append_mfm(bits, b"\x4e" * 22 + b"\x00" * 12, previous)
            bits.extend(_mfm_sync())
            mark = 0xF8 if sector.deleted else 0xFB
            field = bytes((mark,)) + sector.data
            crc = _crc16(b"\xa1\xa1\xa1" + field)
            if not sector.crc_ok:
                crc ^= 1
            previous = _append_mfm(bits, field + crc.to_bytes(2, "big"), 1)
            previous = _append_mfm(bits, b"\x4e" * gap3, previous)
        cell_ns = _select_cell_ns(len(bits), revolution_ns, (2000.0, 1000.0, 500.0))
        filler, _ = _mfm_bytes(b"\x4e" * 256, previous)
    else:
        bits.extend(_fm_bytes(b"\xff" * 20))
        for sector in track.sectors:
            header = bytes((sector.cylinder & 0xFF, sector.head & 0xFF, sector.sector_id & 0xFF, sector.size_code & 0xFF))
            bits.extend(_fm_bytes(b"\x00" * 3))
            field = b"\xfe" + header
            bits.extend(_fm_bytes(field + _crc16(field).to_bytes(2, "big")))
            bits.extend(_fm_bytes(b"\xff" * 5 + b"\x00" * 3))
            mark = 0xF8 if sector.deleted else 0xFB
            field = bytes((mark,)) + sector.data
            crc = _crc16(field)
            if not sector.crc_ok:
                crc ^= 1
            bits.extend(_fm_bytes(field + crc.to_bytes(2, "big") + b"\xff" * gap3))
        cell_ns = _select_cell_ns(len(bits), revolution_ns, (4000.0, 2000.0, 1000.0))
        filler = _fm_bytes(b"\xff" * 256)
    return _pad_track(bits, filler, cell_ns, revolution_ns), cell_ns


def _amiga_odd_even(data: bytes) -> bytes:
    return bytes((byte >> 1) & 0x55 for byte in data) + bytes(byte & 0x55 for byte in data)


def _amiga_checksum(data: bytes) -> int:
    checksum = 0
    for offset in range(0, len(data), 4):
        checksum ^= int.from_bytes(data[offset : offset + 4], "big")
    return (checksum ^ (checksum >> 1)) & 0x55555555


def _mfm_fill_raw_lanes(data: bytes) -> list[int]:
    raw = _byte_bits(data)
    previous_data = 0
    for index in range(0, len(raw), 2):
        next_data = raw[index + 1] if index + 1 < len(raw) else 0
        raw[index] = 1 if previous_data == 0 and next_data == 0 else 0
        previous_data = next_data
    return raw


def _encode_amiga_track(track: TrackSectors, revolution_ns: float) -> tuple[list[int], float]:
    raw = bytearray(128 * max(1, len(track.sectors) // 11))
    track_number = track.track * 2 + track.head
    # Keep sector zero away from the synthetic index splice. Some Amiga PLL
    # readers intentionally discard a field which straddles that boundary.
    ordered_sectors = list(track.sectors[1:]) + list(track.sectors[:1])
    for position, sector in enumerate(ordered_sectors):
        header = bytes((0xFF, track_number & 0xFF, sector.sector_id & 0xFF, max(0, len(ordered_sectors) - position)))
        label = bytes(16)
        header_checksum = _amiga_checksum(header + label)
        data_checksum = _amiga_checksum(sector.data)
        if not sector.crc_ok:
            data_checksum ^= 1
        raw.extend(b"\x44\x89\x44\x89")
        encoded = (
            _amiga_odd_even(header)
            + _amiga_odd_even(label)
            + _amiga_odd_even(header_checksum.to_bytes(4, "big"))
            + _amiga_odd_even(data_checksum.to_bytes(4, "big"))
            + _amiga_odd_even(sector.data)
            + _amiga_odd_even(bytes(4))
        )
        raw.extend(bytes(_bits_to_bytes(_mfm_fill_raw_lanes(encoded))))
    bits = _byte_bits(bytes(raw))
    cell_ns = _select_cell_ns(len(bits), revolution_ns, (2000.0, 1000.0, 500.0))
    return _pad_track(bits, _byte_bits(b"\xaa" * 256), cell_ns, revolution_ns), cell_ns


def _gcr_bytes(data: bytes) -> list[int]:
    bits: list[int] = []
    for byte in data:
        for nibble in (byte >> 4, byte & 0x0F):
            symbol = GCR_ENCODE_4TO5[nibble]
            bits.extend((symbol >> shift) & 1 for shift in range(4, -1, -1))
    return bits


def _encode_commodore_track(
    track: TrackSectors, layout: LayoutDescriptor, revolution_ns: float
) -> tuple[list[int], float]:
    bits = _byte_bits(b"\x55" * 10)
    physical_track = track.head * int(layout.tracks) + track.track + 1
    for sector in track.sectors:
        disk_id = (0, 0)
        checksum = sector.sector_id ^ physical_track ^ disk_id[0] ^ disk_id[1]
        header = bytes((0x08, checksum & 0xFF, sector.sector_id & 0xFF, physical_track & 0xFF, *disk_id, 0x0F, 0x0F))
        data_checksum = 0
        for byte in sector.data:
            data_checksum ^= byte
        if not sector.crc_ok:
            data_checksum ^= 1
        data = bytes((0x07,)) + sector.data + bytes((data_checksum, 0x0F, 0x0F))
        bits.extend([1] * 40)
        bits.extend(_gcr_bytes(header))
        bits.extend(_byte_bits(b"\x55" * 9))
        bits.extend([1] * 40)
        bits.extend(_gcr_bytes(data))
        bits.extend(_byte_bits(b"\x55" * 9))
    zone_track = track.track + 1
    cell_ns = 3250.0 if zone_track <= 17 else 3500.0 if zone_track <= 24 else 3750.0 if zone_track <= 30 else 4000.0
    return _pad_track(bits, _byte_bits(b"\x55" * 256), cell_ns, revolution_ns), cell_ns


_APPLE2_GCR_ENCODE = tuple(value for value, _ in sorted(APPLE2_GCR_DECODE.items(), key=lambda item: item[1]))


def _apple_4and4(value: int) -> bytes:
    return bytes(((value >> 1) | 0xAA, value | 0xAA))


def _apple_6and2(data: bytes, *, valid_checksum: bool) -> bytes:
    if len(data) != 256:
        raise ExportError("Apple II SCP export requires 256-byte sectors")
    values = [0] * 342
    for index in range(86):
        value = ((data[index] & 1) << 1) | ((data[index] & 2) >> 1)
        value |= ((data[index + 86] & 1) << 3) | ((data[index + 86] & 2) << 1)
        if index + 172 < 256:
            value |= ((data[index + 172] & 1) << 5) | ((data[index + 172] & 2) << 3)
        values[index] = value
    for index in range(86, 342):
        values[index] = data[index - 86] >> 2
    encoded = bytearray()
    previous = 0
    for value in values:
        encoded.append(_APPLE2_GCR_ENCODE[previous ^ value])
        previous = value
    checksum = previous if valid_checksum else previous ^ 1
    encoded.append(_APPLE2_GCR_ENCODE[checksum])
    return bytes(encoded)


def _encode_apple2_track(track: TrackSectors, revolution_ns: float) -> tuple[list[int], float]:
    bits: list[int] = [1, 0] * 300
    volume = 254
    for sector in track.sectors:
        checksum = volume ^ track.track ^ sector.sector_id
        bits.extend(_byte_bits(b"\xff" * 18 + b"\xd5\xaa\x96"))
        bits.extend(_byte_bits(b"".join(_apple_4and4(value) for value in (volume, track.track, sector.sector_id, checksum))))
        bits.extend(_byte_bits(b"\xde\xaa\xeb" + b"\xff" * 6 + b"\xd5\xaa\xad"))
        bits.extend(_byte_bits(_apple_6and2(sector.data, valid_checksum=sector.crc_ok)))
        bits.extend(_byte_bits(b"\xde\xaa\xeb"))
    cell_ns = 3920.0
    return _pad_track(bits, [1, 0] * 128, cell_ns, revolution_ns), cell_ns


def _select_cell_ns(bit_count: int, revolution_ns: float, candidates: Sequence[float]) -> float:
    for cell_ns in candidates:
        if bit_count * cell_ns <= revolution_ns * 0.98:
            return cell_ns
    raise ExportError("Encoded track does not fit within one revolution at a supported data rate")


def _pad_track(bits: list[int], filler: Sequence[int], cell_ns: float, revolution_ns: float) -> list[int]:
    target = int(round(revolution_ns / cell_ns))
    if len(bits) > target:
        raise ExportError("Encoded track exceeds the selected revolution length")
    if not filler:
        filler = (1, 0)
    remaining = target - len(bits)
    repeats, tail = divmod(remaining, len(filler))
    bits.extend(filler * repeats)
    bits.extend(filler[:tail])
    return bits


def _circular_flux_intervals(bits: Sequence[int], cell_ns: float) -> list[int]:
    transitions = [index for index, bit in enumerate(bits) if bit]
    if not transitions:
        raise ExportError("Synthetic track contains no flux transitions")
    # SCP revolutions are index-cued: the first interval is measured from the
    # index pulse to the first transition, not from that transition to the
    # following one. Keeping this phase is essential for readers which do not
    # stitch sector fields across the synthetic index boundary.
    cells_between = [transitions[0] + 1]
    cells_between.extend(right - left for left, right in zip(transitions, transitions[1:]))
    intervals = [
        max(1, int(round(cells * cell_ns / SCP_TICK_NS)))
        for cells in cells_between
    ]
    return intervals


def _bits_to_bytes(bits: Iterable[int]) -> bytes:
    values = list(bits)
    if len(values) % 8:
        values.extend([0] * (8 - len(values) % 8))
    return bytes(sum(values[offset + bit] << (7 - bit) for bit in range(8)) for offset in range(0, len(values), 8))


__all__ = ["SCP_TICK_NS", "SyntheticTrackFlux", "supported_scp_layout", "synthesize_track_flux"]
