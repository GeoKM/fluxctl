"""TRS-80/Tandy emulator disk-image loaders.

The common ``.dsk`` suffix is ambiguous in TRS-80 collections. This module
handles the JV3 sector-table format and the DMK raw-track format, and also
accepts ``.dmk`` explicitly.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from .exceptions import FluxctlError
from .imd import IMDGeometry
from .sector.models import Sector, TrackSectors


JV3_HEADER_COUNT = 2901
JV3_HEADER_BYTES = JV3_HEADER_COUNT * 3
JV3_DATA_OFFSET = JV3_HEADER_BYTES + 1
JV3_FREE = 0xFF
DMK_HEADER_BYTES = 16
DMK_TRACK_HEADER_BYTES = 128


@dataclass(frozen=True, slots=True)
class TRS80ImageMetadata:
    """Container metadata for a decoded TRS-80 image."""

    format: str
    write_protected: bool
    modes_seen: dict[str, int]


def load_trs80_image(path: Path) -> tuple[list[TrackSectors], IMDGeometry, dict]:
    """Load a TRS-80 ``.dsk``/``.dmk`` image as decoded sectors."""

    data = path.read_bytes()
    if _looks_like_dmk(data):
        tracks, geometry, metadata = _load_dmk(data)
    elif _looks_like_jv3(data):
        tracks, geometry, metadata = _load_jv3(data)
    else:
        raise FluxctlError("Unsupported TRS-80 .dsk/.dmk image format")
    return tracks, geometry, {
        "format": metadata.format,
        "write_protected": metadata.write_protected,
        "geometry": asdict(geometry),
        "modes_seen": metadata.modes_seen,
    }


def _size_code_for(size: int) -> int:
    code = 0
    value = 128
    while value < size and code < 7:
        code += 1
        value <<= 1
    return code if value == size else -1


def _geometry_for_tracks(tracks: Iterable[TrackSectors]) -> IMDGeometry:
    tracks_list = list(tracks)
    if not tracks_list:
        return IMDGeometry(tracks=0, heads=1, spt=0, sector_size=512)
    cylinders = {ts.track for ts in tracks_list}
    heads = {ts.head for ts in tracks_list}
    sector_sizes = Counter(len(sec.data) for ts in tracks_list for sec in ts.sectors if sec.data)
    spt = max((len(ts.sectors) for ts in tracks_list), default=0)
    sector_size = sector_sizes.most_common(1)[0][0] if sector_sizes else 512
    return IMDGeometry(
        tracks=max(cylinders) + 1 if cylinders else 0,
        heads=max(heads) + 1 if heads else 1,
        spt=spt,
        sector_size=sector_size,
    )


def _looks_like_jv3(data: bytes) -> bool:
    if len(data) <= JV3_DATA_OFFSET:
        return False
    used = 0
    for offset in range(0, JV3_HEADER_BYTES, 3):
        track, sector, flags = data[offset : offset + 3]
        if track == JV3_FREE and sector == JV3_FREE:
            continue
        if flags == 0xFF:
            continue
        used += 1
        if used >= 4:
            return True
    return False


def _jv3_sector_size(flags: int) -> int:
    return {0: 256, 1: 128, 2: 1024, 3: 512}[flags & 0x03]


def _load_jv3(data: bytes) -> tuple[list[TrackSectors], IMDGeometry, TRS80ImageMetadata]:
    by_track: defaultdict[tuple[int, int], list[Sector]] = defaultdict(list)
    data_offset = JV3_DATA_OFFSET
    modes_seen: Counter[str] = Counter()
    write_protected = data[JV3_HEADER_BYTES] == 0x00
    for offset in range(0, JV3_HEADER_BYTES, 3):
        track, sector_id, flags = data[offset : offset + 3]
        if track == JV3_FREE and sector_id == JV3_FREE:
            continue
        if flags == 0xFF:
            continue
        size = _jv3_sector_size(flags)
        payload = data[data_offset : data_offset + size]
        if len(payload) < size:
            break
        data_offset += size
        head = 1 if flags & 0x10 else 0
        double_density = bool(flags & 0x80)
        modes_seen["mfm" if double_density else "fm"] += 1
        dam = flags & 0x60
        sector = Sector(
            cylinder=track,
            head=head,
            sector_id=sector_id,
            size_code=_size_code_for(size),
            data=payload,
            crc_ok=not bool(flags & 0x08),
            confidence=0.0 if flags & 0x08 else 1.0,
            deleted=dam in {0x20, 0x60},
        )
        by_track[(track, head)].append(sector)

    tracks = [
        TrackSectors(track=track, head=head, sectors=sectors, missing=sum(1 for sec in sectors if not sec.crc_ok))
        for (track, head), sectors in sorted(by_track.items())
    ]
    for ts in tracks:
        ts.sectors.sort(key=lambda sector: sector.sector_id)
    return tracks, _geometry_for_tracks(tracks), TRS80ImageMetadata("jv3", write_protected, dict(modes_seen))


def _looks_like_dmk(data: bytes) -> bool:
    if len(data) < DMK_HEADER_BYTES:
        return False
    tracks = data[1]
    track_len = int.from_bytes(data[2:4], "little")
    if tracks == 0 or track_len <= DMK_TRACK_HEADER_BYTES:
        return False
    return len(data) >= DMK_HEADER_BYTES + tracks * track_len


def _load_dmk(data: bytes) -> tuple[list[TrackSectors], IMDGeometry, TRS80ImageMetadata]:
    tracks_count = data[1]
    track_len = int.from_bytes(data[2:4], "little")
    write_protected = data[0] == 0xFF
    by_track: defaultdict[tuple[int, int], list[Sector]] = defaultdict(list)
    modes_seen: Counter[str] = Counter()
    for track_index in range(tracks_count):
        start = DMK_HEADER_BYTES + track_index * track_len
        raw_track = data[start : start + track_len]
        if len(raw_track) < track_len:
            break
        pointers: list[tuple[bool, int]] = []
        for ptr_offset in range(0, DMK_TRACK_HEADER_BYTES, 2):
            raw_ptr = int.from_bytes(raw_track[ptr_offset : ptr_offset + 2], "little")
            if raw_ptr == 0:
                continue
            pointer = raw_ptr & 0x3FFF
            if DMK_TRACK_HEADER_BYTES <= pointer < len(raw_track):
                pointers.append((bool(raw_ptr & 0x8000), pointer))
        for double_density, pointer in sorted(set(pointers), key=lambda item: item[1]):
            sector = _decode_dmk_sector(raw_track, pointer, double_density)
            if sector is not None:
                modes_seen["mfm" if double_density else "fm"] += 1
                by_track[(sector.cylinder, sector.head)].append(sector)

    tracks = [
        TrackSectors(track=track, head=head, sectors=sectors, missing=sum(1 for sec in sectors if not sec.crc_ok))
        for (track, head), sectors in sorted(by_track.items())
    ]
    for ts in tracks:
        ts.sectors.sort(key=lambda sector: sector.sector_id)
    return tracks, _geometry_for_tracks(tracks), TRS80ImageMetadata("dmk", write_protected, dict(modes_seen))


def _decode_dmk_sector(raw_track: bytes, pointer: int, double_density: bool) -> Sector | None:
    if pointer >= len(raw_track):
        return None
    idam = pointer
    if raw_track[idam] != 0xFE:
        nearby = raw_track.rfind(b"\xFE", max(DMK_TRACK_HEADER_BYTES, pointer - 8), min(len(raw_track), pointer + 8))
        if nearby < 0:
            return None
        idam = nearby
    if idam + 7 > len(raw_track):
        return None
    cylinder, head, sector_id, size_code = raw_track[idam + 1 : idam + 5]
    size = 128 << size_code if size_code <= 6 else 0
    if size <= 0:
        return None
    dam_index = _find_dmk_data_mark(raw_track, idam + 7, double_density)
    if dam_index is None:
        return None
    dam = raw_track[dam_index]
    data_start = dam_index + 1
    payload = raw_track[data_start : data_start + size]
    if len(payload) < size:
        return None
    return Sector(
        cylinder=cylinder,
        head=head,
        sector_id=sector_id,
        size_code=size_code,
        data=payload,
        crc_ok=True,
        confidence=1.0,
        deleted=dam == 0xF8,
    )


def _find_dmk_data_mark(raw_track: bytes, start: int, double_density: bool) -> int | None:
    search_end = min(len(raw_track), start + 512)
    if double_density:
        candidates = [
            idx + 3
            for idx in (
                raw_track.find(b"\xA1\xA1\xA1\xFB", start, search_end),
                raw_track.find(b"\xA1\xA1\xA1\xF8", start, search_end),
            )
            if idx >= 0
        ]
    else:
        candidates = [
            idx
            for idx in (
                raw_track.find(b"\xFB", start, search_end),
                raw_track.find(b"\xF8", start, search_end),
            )
            if idx >= 0
        ]
    return min(candidates) if candidates else None
