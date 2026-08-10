"""Apple II 5.25-inch image and 6-and-2 GCR helpers."""
from __future__ import annotations

import binascii
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from bitarray import bitarray

from .exceptions import FluxDecodeError, FluxctlError
from .filesystems import TrackSectorImage
from .models import LayoutDescriptor, RevolutionFlux
from .sector.models import Sector, TrackSectors


APPLE2_TRACKS = 35
APPLE2_SECTORS = 16
APPLE2_SECTOR_SIZE = 256
APPLE2_BLOCK_SIZE = 512
APPLE2_PO_ORDER = (0, 2, 4, 6, 8, 10, 12, 14, 1, 3, 5, 7, 9, 11, 13, 15)
APPLE2_DO_ORDER = (0, 13, 11, 9, 7, 5, 3, 1, 14, 12, 10, 8, 6, 4, 2, 15)
APPLE2_PHYSICAL_ORDER = tuple(range(APPLE2_SECTORS))
APPLE2_GCR_DECODE = {
    value: index
    for index, value in enumerate(
        (
            0x96, 0x97, 0x9A, 0x9B, 0x9D, 0x9E, 0x9F, 0xA6,
            0xA7, 0xAB, 0xAC, 0xAD, 0xAE, 0xAF, 0xB2, 0xB3,
            0xB4, 0xB5, 0xB6, 0xB7, 0xB9, 0xBA, 0xBB, 0xBC,
            0xBD, 0xBE, 0xBF, 0xCB, 0xCD, 0xCE, 0xCF, 0xD3,
            0xD6, 0xD7, 0xD9, 0xDA, 0xDB, 0xDC, 0xDD, 0xDE,
            0xDF, 0xE5, 0xE6, 0xE7, 0xE9, 0xEA, 0xEB, 0xEC,
            0xED, 0xEE, 0xEF, 0xF2, 0xF3, 0xF4, 0xF5, 0xF6,
            0xF7, 0xF9, 0xFA, 0xFB, 0xFC, 0xFD, 0xFE, 0xFF,
        )
    )
}

_ADDRESS_PROLOG = bitarray(endian="big")
_ADDRESS_PROLOG.frombytes(b"\xD5\xAA\x96")
_DATA_PROLOG = bitarray(endian="big")
_DATA_PROLOG.frombytes(b"\xD5\xAA\xAD")


@dataclass(frozen=True)
class WOZImage:
    version: int
    tracks: list[TrackSectors]
    metadata: dict[str, str] = field(default_factory=dict)
    creator: str = ""
    write_protected: bool = False
    synchronized: bool = False
    cleaned: bool = False


class Apple2SectorImage(TrackSectorImage):
    """Expose Apple physical sectors as ProDOS 512-byte logical blocks."""

    def __init__(self, tracks: Sequence[TrackSectors], layout: LayoutDescriptor | None = None):
        super().__init__(tracks, bytes_per_sector=APPLE2_SECTOR_SIZE)
        self.bytes_per_sector = APPLE2_BLOCK_SIZE
        self.total_sectors = APPLE2_TRACKS * APPLE2_SECTORS // 2
        self.layout = layout

    def read_sector(self, lba: int, count: int = 1) -> bytes:
        if lba < 0 or count < 0 or lba + count > self.total_sectors:
            raise FluxctlError("Requested ProDOS block range exceeds image size")
        payload = bytearray()
        for block in range(lba, lba + count):
            track = block // 8
            pair = (block % 8) * 2
            for sector_id in APPLE2_PO_ORDER[pair : pair + 2]:
                try:
                    payload.extend(self._sector_lookup[(track, 0, sector_id)])
                except KeyError as exc:
                    raise FluxctlError(
                        f"Apple II sector T{track} H0 S{sector_id} is unavailable"
                    ) from exc
        return bytes(payload)

    def iter_sectors(self) -> Iterable[bytes]:
        for block in range(self.total_sectors):
            yield self.read_sector(block)

    def read_physical_sector(self, track: int, sector: int) -> bytes:
        try:
            return self._sector_lookup[(track, 0, sector)]
        except KeyError as exc:
            raise FluxctlError(f"Apple II sector T{track} H0 S{sector} is unavailable") from exc

    @staticmethod
    def block_sector_addresses(block: int) -> set[tuple[int, int, int]]:
        if block < 0 or block >= APPLE2_TRACKS * 8:
            return set()
        track = block // 8
        pair = (block % 8) * 2
        return {(track, 0, sector_id) for sector_id in APPLE2_PO_ORDER[pair : pair + 2]}


def tracks_from_apple2_sector_image(data: bytes, order: Sequence[int]) -> list[TrackSectors]:
    expected = APPLE2_TRACKS * APPLE2_SECTORS * APPLE2_SECTOR_SIZE
    if len(data) != expected:
        raise FluxDecodeError(f"Apple II sector image must be {expected:,} bytes; found {len(data):,}")
    tracks: list[TrackSectors] = []
    offset = 0
    for track in range(APPLE2_TRACKS):
        sectors: list[Sector] = []
        for sector_id in order:
            chunk = data[offset : offset + APPLE2_SECTOR_SIZE]
            offset += APPLE2_SECTOR_SIZE
            sectors.append(_sector(track, int(sector_id), chunk, True, 1.0))
        tracks.append(TrackSectors(track=track, head=0, sectors=sorted(sectors, key=lambda item: item.sector_id)))
    return tracks


def apple2_sector_image_bytes(tracks: Sequence[TrackSectors], order: Sequence[int]) -> bytes:
    lookup = {(ts.track, sector.sector_id): sector.data for ts in tracks for sector in ts.sectors}
    payload = bytearray()
    for track in range(APPLE2_TRACKS):
        for sector_id in order:
            data = lookup.get((track, int(sector_id)))
            if data is None or len(data) != APPLE2_SECTOR_SIZE:
                raise FluxDecodeError(f"Apple II sector T{track} S{sector_id} is unavailable")
            payload.extend(data)
    return bytes(payload)


def load_apple2_tracks(path: Path) -> tuple[list[TrackSectors], dict[str, object]]:
    suffix = path.suffix.lower()
    if suffix == ".po":
        return tracks_from_apple2_sector_image(path.read_bytes(), APPLE2_PO_ORDER), {"format": "po"}
    if suffix in {".img", ".dsk"}:
        data = path.read_bytes()
        for format_name, order in (("po", APPLE2_PO_ORDER), ("do", APPLE2_DO_ORDER)):
            tracks = tracks_from_apple2_sector_image(data, order)
            image = Apple2SectorImage(tracks)
            try:
                volume = image.read_sector(2)
            except FluxctlError:
                continue
            if len(volume) >= 43 and volume[4] >> 4 == 0x0F and 1 <= (volume[4] & 0x0F) <= 15:
                return tracks, {"format": format_name, "container": suffix.lstrip(".")}
            vtoc = next(
                (
                    sector.data
                    for track in tracks
                    if track.track == 17
                    for sector in track.sectors
                    if sector.sector_id == 0
                ),
                b"",
            )
            if (
                len(vtoc) == 256
                and 1 <= vtoc[1] < APPLE2_TRACKS
                and vtoc[2] < APPLE2_SECTORS
                and vtoc[0x34] in {0, APPLE2_TRACKS}
                and vtoc[0x35] == APPLE2_SECTORS
                and int.from_bytes(vtoc[0x36:0x38], "little") == APPLE2_SECTOR_SIZE
            ):
                return tracks, {"format": format_name, "container": suffix.lstrip(".")}
        return tracks_from_apple2_sector_image(data, APPLE2_PO_ORDER), {
            "format": "po",
            "container": suffix.lstrip("."),
        }
    if suffix == ".do":
        return tracks_from_apple2_sector_image(path.read_bytes(), APPLE2_DO_ORDER), {"format": "do"}
    if suffix == ".nib":
        return decode_nib_image(path.read_bytes()), {"format": "nib"}
    if suffix == ".woz":
        image = parse_woz(path)
        return image.tracks, {
            "format": f"woz{image.version}",
            "metadata": image.metadata,
            "creator": image.creator,
            "write_protected": image.write_protected,
            "synchronized": image.synchronized,
            "cleaned": image.cleaned,
        }
    raise FluxDecodeError(f"Unsupported Apple II image type: {suffix}")


def parse_woz(path: Path) -> WOZImage:
    data = path.read_bytes()
    if len(data) < 12 or data[:4] not in {b"WOZ1", b"WOZ2"} or data[4:8] != b"\xFF\x0A\x0D\x0A":
        raise FluxDecodeError("Not a valid WOZ1/WOZ2 image")
    version = int(chr(data[3]))
    expected_crc = struct.unpack_from("<I", data, 8)[0]
    if expected_crc and binascii.crc32(data[12:]) & 0xFFFFFFFF != expected_crc:
        raise FluxDecodeError("WOZ image CRC32 does not match its header")

    chunks: dict[bytes, bytes] = {}
    offset = 12
    while offset + 8 <= len(data):
        chunk_id = data[offset : offset + 4]
        size = struct.unpack_from("<I", data, offset + 4)[0]
        offset += 8
        end = offset + size
        if end > len(data):
            raise FluxDecodeError(f"WOZ chunk {chunk_id!r} exceeds file size")
        chunks[chunk_id] = data[offset:end]
        offset = end

    info = chunks.get(b"INFO", b"")
    tmap = chunks.get(b"TMAP", b"")
    trks = chunks.get(b"TRKS", b"")
    if len(tmap) < 160 or not trks:
        raise FluxDecodeError("WOZ image is missing TMAP or TRKS data")

    creator = info[5:37].decode("ascii", errors="replace").rstrip(" \x00") if len(info) >= 37 else ""
    write_protected = bool(info[2]) if len(info) > 2 else False
    synchronized = bool(info[3]) if len(info) > 3 else False
    cleaned = bool(info[4]) if len(info) > 4 else False
    metadata = _parse_woz_metadata(chunks.get(b"META", b""))
    decoded: list[TrackSectors] = []
    seen_descriptors: set[int] = set()
    for cylinder in range(APPLE2_TRACKS):
        descriptor = tmap[cylinder * 4]
        if descriptor == 0xFF or descriptor in seen_descriptors:
            continue
        seen_descriptors.add(descriptor)
        bits = _woz_track_bits(data, trks, version, descriptor)
        decoded.append(decode_apple2_bitstream(bits, cylinder=cylinder))
    if not decoded:
        raise FluxDecodeError("WOZ image contains no decodable Apple II tracks")
    return WOZImage(version, decoded, metadata, creator, write_protected, synchronized, cleaned)


def _woz_track_bits(file_data: bytes, trks: bytes, version: int, descriptor: int) -> bitarray:
    if version == 2:
        table_offset = descriptor * 8
        if table_offset + 8 > len(trks):
            raise FluxDecodeError("WOZ2 track descriptor exceeds TRKS table")
        start_block, block_count, bit_count = struct.unpack_from("<HHI", trks, table_offset)
        start = start_block * 512
        raw = file_data[start : start + block_count * 512]
    else:
        record_size = 6656 + 10
        start = descriptor * record_size
        if start + record_size > len(trks):
            raise FluxDecodeError("WOZ1 track descriptor exceeds TRKS data")
        raw = trks[start : start + 6656]
        bytes_used, bit_count = struct.unpack_from("<HH", trks, start + 6656)
        raw = raw[:bytes_used]
    if not raw or bit_count <= 0 or bit_count > len(raw) * 8:
        raise FluxDecodeError("WOZ track has invalid bit count")
    bits = bitarray(endian="big")
    bits.frombytes(raw)
    return bits[:bit_count]


def _parse_woz_metadata(raw: bytes) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for line in raw.decode("utf-8", errors="replace").splitlines():
        if "\t" not in line:
            continue
        key, value = line.split("\t", 1)
        metadata[key.strip()] = value.strip()
    return metadata


def decode_nib_image(data: bytes) -> list[TrackSectors]:
    if len(data) % APPLE2_TRACKS:
        raise FluxDecodeError("Apple NIB image size is not divisible into 35 tracks")
    track_size = len(data) // APPLE2_TRACKS
    if track_size < 6000:
        raise FluxDecodeError("Apple NIB track records are unexpectedly short")
    tracks: list[TrackSectors] = []
    for cylinder in range(APPLE2_TRACKS):
        bits = bitarray(endian="big")
        bits.frombytes(data[cylinder * track_size : (cylinder + 1) * track_size])
        tracks.append(decode_apple2_bitstream(bits, cylinder=cylinder))
    return tracks


def decode_apple2_revolutions(
    revolutions: Sequence[RevolutionFlux], cylinder: int, head: int = 0
) -> TrackSectors:
    from .decoding.apple2 import apple2_gcr_decoder

    candidates: list[TrackSectors] = []
    for revolution in revolutions:
        if not revolution.interval_ns:
            continue
        bitstream = apple2_gcr_decoder.decode_revolution(revolution)
        candidates.append(decode_apple2_bitstream(bitstream.bits, cylinder=cylinder, head=head))
        if len(candidates[-1].sectors) == APPLE2_SECTORS and not candidates[-1].weak:
            return candidates[-1]
    if not candidates:
        raise FluxDecodeError("No Apple II revolution could be decoded")
    best = max(candidates, key=lambda track: (len(track.sectors) - track.weak, len(track.sectors)))
    return best


def decode_apple2_bitstream(bits: Sequence[int], cylinder: int, head: int = 0) -> TrackSectors:
    stream = bits if isinstance(bits, bitarray) else bitarray(bits, endian="big")
    if not stream:
        return TrackSectors(track=cylinder, head=head, sectors=[], missing=APPLE2_SECTORS)
    circular = stream + stream[: min(len(stream), 4096)]
    sectors: dict[int, Sector] = {}
    for address_offset in circular.search(_ADDRESS_PROLOG):
        header_start = address_offset + len(_ADDRESS_PROLOG)
        header = circular[header_start : header_start + 64].tobytes()
        if len(header) != 8:
            continue
        volume, track_id, sector_id, checksum = (_decode_4and4(header[i], header[i + 1]) for i in range(0, 8, 2))
        if checksum != volume ^ track_id ^ sector_id or track_id != cylinder or not 0 <= sector_id < APPLE2_SECTORS:
            continue
        search_start = header_start + 64
        data_offsets = list(circular[search_start : search_start + 800].search(_DATA_PROLOG))
        if not data_offsets:
            continue
        data_start = search_start + data_offsets[0] + len(_DATA_PROLOG)
        encoded = _read_self_sync_bytes(circular, data_start, 343)
        payload, checksum_ok = _decode_6and2(encoded)
        candidate = _sector(cylinder, sector_id, payload, checksum_ok, 1.0 if checksum_ok else 0.5)
        existing = sectors.get(sector_id)
        if existing is None or (candidate.crc_ok and not existing.crc_ok):
            sectors[sector_id] = candidate
        if len(sectors) == APPLE2_SECTORS and all(sector.crc_ok for sector in sectors.values()):
            break
    ordered = sorted(sectors.values(), key=lambda item: item.sector_id)
    weak = sum(1 for sector in ordered if not sector.crc_ok)
    return TrackSectors(
        track=cylinder,
        head=head,
        sectors=ordered,
        weak=weak,
        missing=max(APPLE2_SECTORS - len(ordered), 0),
    )


def _decode_4and4(first: int, second: int) -> int:
    return (((first << 1) | 1) & second) & 0xFF


def _read_self_sync_bytes(bits: bitarray, start: int, count: int) -> bytes:
    decoded = bytearray()
    value = 0
    for bit in bits[start : start + count * 10 + 32]:
        value = ((value << 1) | int(bit)) & 0xFF
        if value & 0x80:
            decoded.append(value)
            value = 0
            if len(decoded) == count:
                break
    return bytes(decoded)


def _decode_6and2(encoded: bytes) -> tuple[bytes, bool]:
    if len(encoded) < 343:
        return b"", False
    output = bytearray(APPLE2_SECTOR_SIZE)
    checksum = 0
    try:
        for index in range(342):
            checksum ^= APPLE2_GCR_DECODE[encoded[index]]
            if index >= 86:
                output[index - 86] |= (checksum << 2) & 0xFC
            else:
                output[index] = ((checksum >> 1) & 1) | ((checksum << 1) & 2)
                output[index + 86] = ((checksum >> 3) & 1) | ((checksum >> 1) & 2)
                if index + 172 < APPLE2_SECTOR_SIZE:
                    output[index + 172] = ((checksum >> 5) & 1) | ((checksum >> 3) & 2)
        expected = APPLE2_GCR_DECODE[encoded[342]]
    except KeyError:
        return bytes(output), False
    return bytes(output), (checksum & 0x3F) == expected


def _sector(cylinder: int, sector_id: int, data: bytes, checksum_ok: bool, confidence: float) -> Sector:
    return Sector(
        cylinder=cylinder,
        head=0,
        sector_id=sector_id,
        size_code=1,
        data=data,
        crc_ok=checksum_ok,
        confidence=confidence,
        deleted=False,
    )


__all__ = [
    "APPLE2_DO_ORDER",
    "APPLE2_PHYSICAL_ORDER",
    "APPLE2_PO_ORDER",
    "Apple2SectorImage",
    "WOZImage",
    "apple2_sector_image_bytes",
    "decode_apple2_bitstream",
    "decode_apple2_revolutions",
    "load_apple2_tracks",
    "parse_woz",
    "tracks_from_apple2_sector_image",
]
