"""Lightweight ImageDisk (.imd) parsing helpers.

These utilities read IMD headers and sector records into :class:`TrackSectors`
structures so the rest of the tooling can treat IMD inputs like any other
decoded image. Geometry is inferred from the file contents; IMD records marked
as unavailable are filled with a caller-provided byte (default ``0x00``).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

from .exceptions import FluxctlError
from .sector.models import Sector, TrackSectors


IMD_FILL_DEFAULT = 0x00


@dataclass(slots=True)
class IMDGeometry:
    tracks: int
    heads: int
    spt: int
    sector_size: int


def _read_until_eof_1a(f) -> bytes:
    """Read bytes until ASCII 0x1A (IMD header/comment terminator)."""

    out = bytearray()
    while True:
        b = f.read(1)
        if not b or b == b"\x1a":
            break
        out += b
    return bytes(out)


def _size_code_for(size: int) -> int:
    """Return IBM size code for a sector size (128 << code)."""

    code = 0
    val = 128
    while val < size and code < 7:
        code += 1
        val <<= 1
    return code if val == size else -1


def _iter_imd_tracks(path: Path) -> Iterable[Tuple[Dict, List[Tuple[int, int, int, int, int]]]]:
    """Yield per-track metadata and sector tuples without decoding them twice."""

    with path.open("rb") as fh:
        header_comment = _read_until_eof_1a(fh)
        header_txt = header_comment.decode("latin-1", errors="replace")
        header_line = header_txt.splitlines()[0] if header_txt else ""

        while True:
            hdr = fh.read(5)
            if len(hdr) == 0:
                break
            if len(hdr) < 5:
                raise FluxctlError("Truncated IMD track header")

            mode, cyl, head_byte, nsec, ssize_code = hdr
            meta = {
                "mode": mode,
                "cyl": cyl,
                "head_flag": head_byte,
                "nsec": nsec,
                "ssize_code": ssize_code,
                "header": header_line,
            }

            if nsec == 0:
                yield meta, []
                continue

            has_cyl_map = bool(head_byte & 0x80)
            has_head_map = bool(head_byte & 0x40)
            head_phys = head_byte & 0x01

            sec_nums = list(fh.read(nsec))
            if len(sec_nums) < nsec:
                raise FluxctlError("Truncated IMD sector numbering map")

            if ssize_code == 0xFF:
                raw = fh.read(nsec * 2)
                if len(raw) < nsec * 2:
                    raise FluxctlError("Truncated IMD per-sector size table")
                size_table = [int.from_bytes(raw[i * 2 : i * 2 + 2], "little") for i in range(nsec)]
            else:
                if ssize_code > 6:
                    raise FluxctlError(f"Unsupported IMD sector size code: {ssize_code}")
                size_table = [128 << ssize_code] * nsec

            cyl_map = [cyl] * nsec
            if has_cyl_map:
                raw = fh.read(nsec)
                if len(raw) < nsec:
                    raise FluxctlError("Truncated IMD sector cylinder map")
                cyl_map = list(raw)

            head_map = [head_phys] * nsec
            if has_head_map:
                raw = fh.read(nsec)
                if len(raw) < nsec:
                    raise FluxctlError("Truncated IMD sector head map")
                head_map = list(raw)

            sectors: List[Tuple[int, int, int, int, int]] = []
            for i in range(nsec):
                rec_type_b = fh.read(1)
                if not rec_type_b:
                    raise FluxctlError("Truncated IMD sector data record")
                rec_type = rec_type_b[0]
                sectors.append((int(cyl_map[i]), int(head_map[i]), int(sec_nums[i]), size_table[i], rec_type))

                this_size = size_table[i]
                if rec_type in (0x01, 0x03, 0x05, 0x07):
                    skip = fh.read(this_size)
                    if len(skip) < this_size:
                        raise FluxctlError("Truncated IMD normal sector payload")
                elif rec_type in (0x02, 0x04, 0x06, 0x08):
                    fill = fh.read(1)
                    if not fill:
                        raise FluxctlError("Truncated IMD compressed sector fill byte")
                elif rec_type == 0x00:
                    # missing sector, nothing to skip
                    continue
                else:
                    raise FluxctlError(f"Unknown IMD sector record type: 0x{rec_type:02X}")

            yield meta, sectors


def load_imd_image(path: Path, *, fill: int = IMD_FILL_DEFAULT) -> Tuple[List[TrackSectors], IMDGeometry, Dict]:
    """Decode an IMD into TrackSectors plus geometry and metadata."""

    tracks_data: defaultdict[Tuple[int, int], List[Sector]] = defaultdict(list)
    cylinders = set()
    heads = set()
    sectors_per_track = Counter()
    sector_sizes = Counter()
    modes_seen = Counter()
    header_line = ""

    with path.open("rb") as fh:
        header_comment = _read_until_eof_1a(fh)
        header_txt = header_comment.decode("latin-1", errors="replace")
        header_line = header_txt.splitlines()[0] if header_txt else ""

        while True:
            hdr = fh.read(5)
            if len(hdr) == 0:
                break
            if len(hdr) < 5:
                raise FluxctlError("Truncated IMD track header")

            mode, cyl, head_byte, nsec, ssize_code = hdr
            modes_seen[mode] += 1
            if nsec == 0:
                continue

            has_cyl_map = bool(head_byte & 0x80)
            has_head_map = bool(head_byte & 0x40)
            head_phys = head_byte & 0x01

            sec_nums = list(fh.read(nsec))
            if len(sec_nums) < nsec:
                raise FluxctlError("Truncated IMD sector numbering map")

            if ssize_code == 0xFF:
                raw = fh.read(nsec * 2)
                if len(raw) < nsec * 2:
                    raise FluxctlError("Truncated IMD per-sector size table")
                size_table = [int.from_bytes(raw[i * 2 : i * 2 + 2], "little") for i in range(nsec)]
            else:
                if ssize_code > 6:
                    raise FluxctlError(f"Unsupported IMD sector size code: {ssize_code}")
                size_table = [128 << ssize_code] * nsec

            cyl_map = [cyl] * nsec
            if has_cyl_map:
                raw = fh.read(nsec)
                if len(raw) < nsec:
                    raise FluxctlError("Truncated IMD sector cylinder map")
                cyl_map = list(raw)

            head_map = [head_phys] * nsec
            if has_head_map:
                raw = fh.read(nsec)
                if len(raw) < nsec:
                    raise FluxctlError("Truncated IMD sector head map")
                head_map = list(raw)

            for i in range(nsec):
                rec_type_b = fh.read(1)
                if not rec_type_b:
                    raise FluxctlError("Truncated IMD sector data record")
                rec_type = rec_type_b[0]

                C = int(cyl_map[i])
                H = int(head_map[i])
                R = int(sec_nums[i])
                this_size = size_table[i]
                cylinders.add(C)
                heads.add(H)
                sector_sizes[this_size] += 1
                sectors_per_track[(C, H)] = max(sectors_per_track[(C, H)], nsec)

                data = b""
                if rec_type == 0x00:
                    data = bytes([fill]) * this_size
                    crc_ok = False
                elif rec_type in (0x01, 0x03, 0x05, 0x07):
                    raw = fh.read(this_size)
                    if len(raw) < this_size:
                        raise FluxctlError("Truncated IMD normal sector payload")
                    data = raw
                    crc_ok = True
                elif rec_type in (0x02, 0x04, 0x06, 0x08):
                    fill_b = fh.read(1)
                    if not fill_b:
                        raise FluxctlError("Truncated IMD compressed sector fill byte")
                    data = bytes([fill_b[0]]) * this_size
                    crc_ok = True
                else:
                    raise FluxctlError(f"Unknown IMD sector record type: 0x{rec_type:02X}")

                sector = Sector(
                    cylinder=C,
                    head=H,
                    sector_id=R,
                    size_code=_size_code_for(this_size),
                    data=data,
                    crc_ok=crc_ok,
                    confidence=1.0 if crc_ok else 0.0,
                    deleted=rec_type in (0x05, 0x06, 0x07, 0x08),
                )
                tracks_data[(C, H)].append(sector)

    tracks_count = (max(cylinders) + 1) if cylinders else 0
    heads_count = (max(heads) + 1) if heads else 1
    spt = max(sectors_per_track.values()) if sectors_per_track else 0
    sector_size = sector_sizes.most_common(1)[0][0] if sector_sizes else 512

    tracks: List[TrackSectors] = []
    for cyl in range(tracks_count):
        for head in range(heads_count):
            sectors = tracks_data.get((cyl, head), [])
            sectors.sort(key=lambda s: s.sector_id)
            missing = sum(1 for s in sectors if not s.crc_ok)
            tracks.append(TrackSectors(track=cyl, head=head, sectors=sectors, missing=missing))

    geometry = IMDGeometry(tracks=tracks_count, heads=heads_count, spt=spt, sector_size=sector_size)
    meta = {"geometry": asdict(geometry), "modes_seen": dict(modes_seen), "header": header_line}
    return tracks, geometry, meta
