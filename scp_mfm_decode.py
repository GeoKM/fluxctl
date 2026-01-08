#!/usr/bin/env python3
"""
scp_mfm_decode.py — museum-grade, reproducible IBM-PC MFM decoder and comparator
for SCP (SuperCard Pro flux), IMD (ImageDisk), and raw sector images.

This tool does NOT preserve flux in the output. It reconstructs a raw sector image (.img)
by decoding MFM from SCP flux timings (first revolution by default) and validating CRCs.

Designed for museum-style workflows where flux/IMD are preserved and .img is a derivative.
Tested in ChatGPT sandbox against a 1.2MB (5.25" HD) IBM-PC MFM SCP image and a matching IMD.
"""

from __future__ import annotations
import argparse
import hashlib
import json
import os
import sys
import struct
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


# ---------------- CRC16-CCITT (floppy) ----------------
def crc16_ccitt(data: bytes, poly: int = 0x1021, init: int = 0xFFFF) -> int:
    crc = init
    for b in data:
        crc ^= (b << 8)
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) & 0xFFFF) ^ poly
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


# ---------------- SCP parsing ----------------
@dataclass
class SCPHeader:
    version: int
    disk_type: int
    revolutions: int
    start_track: int
    end_track: int
    flags: int
    bitcell: int
    heads: int
    offsets: Tuple[int, ...]


def parse_scp_header(path: str) -> SCPHeader:
    with open(path, "rb") as f:
        base = f.read(0x10)
        if base[:3] != b"SCP":
            raise ValueError("Not an SCP file (missing 'SCP' signature).")
        version = base[3]
        disk_type = base[4]
        revolutions = base[5]
        start_track = base[6]
        end_track = base[7]
        flags = base[8]
        bitcell = base[9]
        heads = base[10]
        f.seek(0x10)
        offsets = struct.unpack("<168I", f.read(168 * 4))
    return SCPHeader(
        version=version,
        disk_type=disk_type,
        revolutions=revolutions,
        start_track=start_track,
        end_track=end_track,
        flags=flags,
        bitcell=bitcell,
        heads=heads,
        offsets=offsets,
    )


def read_track_block(path: str, track_offset: int, revolutions: int) -> Tuple[int, List[Tuple[int, int, int]]]:
    with open(path, "rb") as f:
        f.seek(track_offset)
        sig = f.read(4)
        if sig[:3] != b"TRK":
            raise ValueError(f"Bad track signature at offset {track_offset}: {sig!r}")
        track_no = sig[3]
        revs: List[Tuple[int, int, int]] = []
        for _ in range(revolutions):
            idx_time, nsamples, data_off = struct.unpack("<III", f.read(12))
            revs.append((idx_time, nsamples, data_off))
    return track_no, revs


def read_flux_samples(path: str, track_offset: int, data_off: int, nsamples: int) -> List[int]:
    """
    Returns list of flux intervals as stored values.
    Handles SCP "extended" encoding where a 0x0000 u16 is followed by a u32 value.
    """
    intervals: List[int] = []
    with open(path, "rb") as f:
        f.seek(track_offset + data_off)
        count = 0
        while count < nsamples:
            raw = f.read(2)
            if len(raw) < 2:
                raise EOFError("Unexpected EOF while reading flux samples.")
            val = struct.unpack("<H", raw)[0]
            count += 1
            if val == 0:
                ext_raw = f.read(4)
                if len(ext_raw) < 4:
                    raise EOFError("Unexpected EOF while reading extended flux sample.")
                intervals.append(struct.unpack("<I", ext_raw)[0])
            else:
                intervals.append(val)
    return intervals


# ---------------- Flux -> bitstream -> sector decode ----------------
def flux_to_bitstream(intervals_raw: List[int], *, scale: float, T_ticks: float, max_bits: int = 6_000_000) -> List[int]:
    """
    Build a raw flux bitstream at 1 'T' time quantum resolution, where '1' indicates a flux transition.
    intervals_raw are SCP stored values. Convert to 25ns ticks by dividing by 'scale'.
    """
    bits: List[int] = [1]
    for val in intervals_raw:
        ticks = val / scale
        nT = int(round(ticks / T_ticks))
        if nT < 1:
            nT = 1
        bits.extend([0] * (nT - 1))
        bits.append(1)
        if len(bits) > max_bits:
            break
    return bits


def find_pattern(bits: List[int], pat: List[int]) -> List[int]:
    m = len(pat)
    res: List[int] = []
    first = pat[0]
    last = pat[-1]
    for i in range(len(bits) - m + 1):
        if bits[i] == first and bits[i + m - 1] == last:
            if bits[i : i + m] == pat:
                res.append(i)
    return res


def find_sync_runs(idxs: List[int], wordlen: int = 16, runlen: int = 3) -> List[int]:
    idxset = set(idxs)
    runs: List[int] = []
    for i in idxs:
        if all((i + j * wordlen) in idxset for j in range(runlen)):
            if (i - wordlen) not in idxset:
                runs.append(i)
    return runs


def mfm_decode_byte_from_bits(bits: List[int], start: int) -> Optional[int]:
    if start + 16 > len(bits):
        return None
    seg = bits[start : start + 16]
    data_bits = seg[1::2]  # odd positions are data bits (clock,data,clock,data,...)
    val = 0
    for b in data_bits:
        val = (val << 1) | b
    return val


def parse_sectors_from_bits(bits: List[int], *, sector_size: int = 512) -> List[Dict]:
    """
    Extract sectors by scanning for 0x4489 sync words (A1 with missing clock) x3,
    then decoding ID Address Marks (0xFE) and Data Address Marks (0xFB/0xF8).
    Validates both ID and DATA CRCs (CRC includes A1 A1 A1).
    """
    pat = [int(b) for b in f"{0x4489:016b}"]  # MSB-first
    idxs = find_pattern(bits, pat)
    sync_starts = find_sync_runs(idxs, 16, 3)
    events = []
    for s in sync_starts:
        marker = mfm_decode_byte_from_bits(bits, s + 48)
        if marker is not None:
            events.append((s, marker))
    events.sort()

    sectors: List[Dict] = []
    current_id: Optional[Dict] = None

    for s, marker in events:
        pos_after_sync = s + 48  # start of marker byte
        if marker == 0xFE:
            # IDAM: FE C H R N CRC1 CRC2
            start = pos_after_sync
            after = []
            for i in range(1, 1 + 4 + 2):
                b = mfm_decode_byte_from_bits(bits, start + i * 16)
                if b is None:
                    break
                after.append(b)
            if len(after) != 6:
                current_id = None
                continue
            C, H, R, N = after[:4]
            crc_stream = (after[4] << 8) | after[5]
            crc_calc = crc16_ccitt(bytes([0xA1, 0xA1, 0xA1, 0xFE, C, H, R, N]))
            current_id = {
                "C": C,
                "H": H,
                "R": R,
                "N": N,
                "id_crc_stream": crc_stream,
                "id_crc_calc": crc_calc,
                "id_crc_ok": (crc_stream == crc_calc),
                "id_bitpos": s,
            }
        elif marker in (0xFB, 0xF8):
            # DAM: FB data... CRC1 CRC2
            start = pos_after_sync
            data = []
            for i in range(1, 1 + sector_size + 2):
                b = mfm_decode_byte_from_bits(bits, start + i * 16)
                if b is None:
                    break
                data.append(b)
            if len(data) != sector_size + 2:
                continue
            payload = bytes(data[:sector_size])
            crc_stream = (data[sector_size] << 8) | data[sector_size + 1]
            crc_calc = crc16_ccitt(bytes([0xA1, 0xA1, 0xA1, marker]) + payload)

            sec = {
                "marker": marker,
                "data_crc_stream": crc_stream,
                "data_crc_calc": crc_calc,
                "data_crc_ok": (crc_stream == crc_calc),
                "data_bitpos": s,
                "data": payload,
            }
            if current_id is not None:
                sec.update(current_id)
            sectors.append(sec)
            current_id = None

    return sectors


# ---------------- Auto-parameter helpers ----------------
def auto_detect_scale_and_T(path: str, revolution: int = 0) -> Tuple[float, float]:
    """
    Best-effort auto-detection for common SCP fixed-point scales and MFM T_ticks.
    We try a small set of scales and pick one that yields plausible MFM intervals.
    """
    hdr = parse_scp_header(path)
    off0 = hdr.offsets[0]
    _, revs = read_track_block(path, off0, hdr.revolutions)
    rev = revs[min(revolution, hdr.revolutions - 1)]
    intervals = read_flux_samples(path, off0, rev[2], rev[1])
    candidates = [1.0, 16.0, 256.0, 512.0]
    best = None

    uniq_raw = sorted(set(intervals[:5000]))
    for scale in candidates:
        ticks = sorted(set([v / scale for v in uniq_raw if v > 0]))
        if not ticks:
            continue
        mn = ticks[0]
        mx = ticks[min(len(ticks) - 1, 10)]
        # Plausible MFM in 25ns ticks:
        # 2us,3us,4us => 80,120,160 (HD); 4us,6us,8us => 160,240,320 (DD)
        if 40 <= mn <= 400 and mx <= 2000:
            # estimate T from smallest common interval as ~2T
            T_ticks = mn / 2.0
            best = (scale, T_ticks)
            # Prefer scales that produce mn in [60..200] and T in [20..200]
            if 60 <= mn <= 200 and 20 <= T_ticks <= 200:
                return scale, T_ticks

    if best is None:
        # fallback: most common in practice for SCP fixed-point
        return 256.0, 40.0
    return best


# ---------------- High-level decode ----------------
def decode_scp_to_img(
    path: str,
    out_path: Optional[str],
    *,
    tracks: int,
    heads: int,
    spt: int,
    sector_size: int,
    revolution: int,
    scale: Optional[float],
    T_ticks: Optional[float],
    strict_crc: bool,
    json_report_path: Optional[str],
) -> Tuple[bytes, Dict]:
    hdr = parse_scp_header(path)

    if scale is None or T_ticks is None:
        auto_scale, auto_T = auto_detect_scale_and_T(path, revolution=revolution)
        if scale is None:
            scale = auto_scale
        if T_ticks is None:
            T_ticks = auto_T

    img = bytearray(tracks * heads * spt * sector_size)
    missing = []
    bad_crc = []
    decoded = 0

    for cyl in range(tracks):
        for head in range(heads):
            table_idx = cyl * 2 + head
            track_offset = hdr.offsets[table_idx]
            track_no, revs = read_track_block(path, track_offset, hdr.revolutions)
            rev = revs[min(revolution, hdr.revolutions - 1)]
            intervals = read_flux_samples(path, track_offset, rev[2], rev[1])
            bits = flux_to_bitstream(intervals, scale=scale, T_ticks=T_ticks)
            sectors = parse_sectors_from_bits(bits, sector_size=sector_size)

            # Map decoded sectors by sector number (R)
            secmap = {}
            for s in sectors:
                R = s.get("R")
                if R is None:
                    continue
                # Accept only sectors with good data CRC; ID CRC can be optionally enforced
                ok_data = s.get("data_crc_ok", False)
                ok_id = s.get("id_crc_ok", True)
                if ok_data and (ok_id or not strict_crc):
                    secmap[R] = s

            for r in range(1, spt + 1):
                off = ((cyl * heads + head) * spt + (r - 1)) * sector_size
                if r in secmap:
                    img[off : off + sector_size] = secmap[r]["data"]
                    decoded += 1
                else:
                    # leave zeros; record
                    missing.append({"C": cyl, "H": head, "R": r, "reason": "missing_or_crc_bad"})
                    # check if it existed but CRC bad
                    for s in sectors:
                        if s.get("R") == r and not s.get("data_crc_ok", False):
                            bad_crc.append({"C": cyl, "H": head, "R": r, "reason": "data_crc_bad"})
                            break

        img_bytes = bytes(img)
    if out_path:
        os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
        with open(out_path, "wb") as f:
            f.write(img_bytes)


    sha = hashlib.sha256(img_bytes).hexdigest()
    result = {
        "input_path": os.path.abspath(path),
        "input_type": "scp",
        "input_scp": os.path.abspath(path),
        "output_img": (os.path.abspath(out_path) if out_path else None),
        "sha256_img": sha,
        "decoded_sectors": decoded,
        "expected_sectors": tracks * heads * spt,
        "missing": missing[:2000],  # cap to keep reports reasonable
        "bad_crc": bad_crc[:2000],
        "params": {
            "tracks": tracks,
            "heads": heads,
            "spt": spt,
            "sector_size": sector_size,
            "revolution": revolution,
            "scale": scale,
            "T_ticks_25ns": T_ticks,
        },
        "scp_header": {
            "version": hdr.version,
            "disk_type": hdr.disk_type,
            "revolutions": hdr.revolutions,
            "start_track": hdr.start_track,
            "end_track": hdr.end_track,
            "flags": hdr.flags,
            "bitcell": hdr.bitcell,
            "heads": hdr.heads,
        },
    }

    if json_report_path:
        with open(json_report_path, "w", encoding="utf-8") as jf:
            json.dump(result, jf, indent=2, sort_keys=True)

    return img_bytes, result


# ---------------- IMD (ImageDisk) decode ----------------
def _read_until_eof_1a(f) -> bytes:
    """Read bytes until ASCII 0x1A (EOF marker used by IMD to terminate header/comment)."""
    out = bytearray()
    while True:
        b = f.read(1)
        if not b:
            break
        if b == b"\x1a":
            break
        out += b
    return bytes(out)


def decode_imd_to_img(
    path: str,
    out_path: Optional[str],
    *,
    tracks: int,
    heads: int,
    spt: int,
    sector_size: int,
    fill: int = 0x00,
    strict_geom: bool = True,
) -> Tuple[bytes, Dict]:
    """
    Decode an ImageDisk .IMD into a raw CHS-ordered sector image of size:
      tracks * heads * spt * sector_size

    This is a derivative. IMD is already a decoded/structured format (not flux).
    """
    fill &= 0xFF
    expected_size = tracks * heads * spt * sector_size
    img = bytearray([fill]) * expected_size

    tracks_seen = 0
    sectors_written = 0
    sectors_unavailable = 0
    sectors_outside = 0
    geom_mismatch = 0
    modes_seen = {}

    with open(path, "rb") as f:
        header_comment = _read_until_eof_1a(f)
        # Best-effort split: first line is usually the IMD header; remainder is comment.
        hc_txt = header_comment.decode("latin-1", errors="replace")
        header_line = hc_txt.splitlines()[0] if hc_txt.splitlines() else ""
        comment_text = "\n".join(hc_txt.splitlines()[1:]) if len(hc_txt.splitlines()) > 1 else ""

        while True:
            trk_hdr = f.read(5)
            if len(trk_hdr) == 0:
                break
            if len(trk_hdr) < 5:
                raise ValueError("Truncated IMD track header.")

            mode, cyl, head_byte, nsec, ssize_code = trk_hdr[0], trk_hdr[1], trk_hdr[2], trk_hdr[3], trk_hdr[4]
            tracks_seen += 1
            modes_seen[mode] = modes_seen.get(mode, 0) + 1

            if nsec == 0:
                # Valid: empty/unread track placeholder
                continue

            has_cyl_map = bool(head_byte & 0x80)
            has_head_map = bool(head_byte & 0x40)
            head_phys = head_byte & 0x01  # side 0/1; upper bits are flags

            sec_nums = list(f.read(nsec))
            if len(sec_nums) < nsec:
                raise ValueError("Truncated IMD sector numbering map.")

            if ssize_code == 0xFF:
                # Per-sector sizes table (little-endian 16-bit)
                size_table = []
                raw = f.read(nsec * 2)
                if len(raw) < nsec * 2:
                    raise ValueError("Truncated IMD per-sector size table.")
                for i in range(nsec):
                    size_table.append(struct.unpack_from("<H", raw, i * 2)[0])
            else:
                if ssize_code > 6:
                    raise ValueError(f"Unsupported IMD sector size code: {ssize_code}")
                size_table = [128 << ssize_code] * nsec

            cyl_map = [cyl] * nsec
            if has_cyl_map:
                raw = f.read(nsec)
                if len(raw) < nsec:
                    raise ValueError("Truncated IMD sector cylinder map.")
                cyl_map = list(raw)

            head_map = [head_phys] * nsec
            if has_head_map:
                raw = f.read(nsec)
                if len(raw) < nsec:
                    raise ValueError("Truncated IMD sector head map.")
                head_map = list(raw)

            # Sector data records in the order of sec_nums
            for i in range(nsec):
                rec_type_b = f.read(1)
                if not rec_type_b:
                    raise ValueError("Truncated IMD sector data record.")
                rec_type = rec_type_b[0]
                this_size = size_table[i]

                if strict_geom and this_size != sector_size:
                    geom_mismatch += 1
                    raise ValueError(
                        f"IMD sector size {this_size} does not match --sector-size {sector_size} (track C{cyl} H{head_phys} S{sec_nums[i]})"
                    )

                # Decode record data into bytes (len == this_size)
                if rec_type == 0x00:
                    # Unavailable: leave filled
                    sectors_unavailable += 1
                    data = None
                elif rec_type in (0x01, 0x03, 0x05, 0x07):
                    raw = f.read(this_size)
                    if len(raw) < this_size:
                        raise ValueError("Truncated IMD normal sector payload.")
                    data = raw
                elif rec_type in (0x02, 0x04, 0x06, 0x08):
                    fill_val_b = f.read(1)
                    if not fill_val_b:
                        raise ValueError("Truncated IMD compressed sector fill byte.")
                    data = bytes([fill_val_b[0]]) * this_size
                else:
                    raise ValueError(f"Unknown IMD sector record type: 0x{rec_type:02X}")

                C = int(cyl_map[i])
                H = int(head_map[i])
                R = int(sec_nums[i])

                if not (0 <= C < tracks and 0 <= H < heads and 1 <= R <= spt):
                    sectors_outside += 1
                    continue

                if data is not None:
                    # Place into CHS-ordered output
                    off = ((C * heads + H) * spt + (R - 1)) * sector_size
                    img[off : off + sector_size] = data[:sector_size]
                    sectors_written += 1

    img_bytes = bytes(img)
    sha = hashlib.sha256(img_bytes).hexdigest()

    if out_path:
        os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
        with open(out_path, "wb") as wf:
            wf.write(img_bytes)

    result = {
        "input_path": os.path.abspath(path),
        "input_type": "imd",
        "output_img": (os.path.abspath(out_path) if out_path else None),
        "sha256_img": sha,
        "geometry": {"tracks": tracks, "heads": heads, "spt": spt, "sector_size": sector_size},
        "imd_header": header_line,
        "imd_comment_bytes": len(header_comment) - len(header_line.encode("latin-1", errors="ignore")),
        "tracks_seen": tracks_seen,
        "sectors_written": sectors_written,
        "sectors_unavailable": sectors_unavailable,
        "sectors_outside_geometry": sectors_outside,
        "sector_size_mismatch_count": geom_mismatch,
        "modes_seen": {str(k): v for k, v in sorted(modes_seen.items())},
    }
    return img_bytes, result


def read_raw_image(
    path: str,
    *,
    expected_size: int,
    out_path: Optional[str],
    allow_size_mismatch: bool,
) -> Tuple[bytes, Dict]:
    with open(path, "rb") as f:
        data = f.read()

    sha = hashlib.sha256(data).hexdigest()
    size = len(data)

    if (size != expected_size) and not allow_size_mismatch:
        raise ValueError(f"Raw image size {size} does not match expected {expected_size}. Use --allow-size-mismatch to override.")

    if out_path:
        os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
        with open(out_path, "wb") as wf:
            wf.write(data)

    return data, {
        "input_path": os.path.abspath(path),
        "input_type": "raw",
        "output_img": (os.path.abspath(out_path) if out_path else None),
        "sha256_img": sha,
        "size": size,
        "expected_size": expected_size,
    }


def _first_diff_offset(a: bytes, b: bytes) -> Optional[int]:
    n = min(len(a), len(b))
    # Chunked scan for speed without huge memory overhead
    step = 1024 * 1024
    for base in range(0, n, step):
        aa = a[base : base + step]
        bb = b[base : base + step]
        if aa == bb:
            continue
        # Find exact position within this chunk
        for i in range(len(aa)):
            if aa[i] != bb[i]:
                return base + i
    if len(a) != len(b):
        return n
    return None


def compare_images(a: bytes, b: bytes, *, allow_size_mismatch: bool) -> Dict:
    sha_a = hashlib.sha256(a).hexdigest()
    sha_b = hashlib.sha256(b).hexdigest()
    equal = (a == b) if (len(a) == len(b) or allow_size_mismatch) else False

    if allow_size_mismatch:
        n = min(len(a), len(b))
        equal = (a[:n] == b[:n]) and (len(a) == len(b))
        # If sizes differ, we still report diff offset as the first size mismatch if prefix equal.
        diff = _first_diff_offset(a, b)
    else:
        diff = _first_diff_offset(a, b)

    return {
        "equal": (sha_a == sha_b) if (len(a) == len(b)) else (diff is None),
        "sha256_a": sha_a,
        "sha256_b": sha_b,
        "len_a": len(a),
        "len_b": len(b),
        "first_diff_offset": diff,
    }


def decode_any_to_bytes(
    in_path: str,
    *,
    out_path: Optional[str],
    tracks: int,
    heads: int,
    spt: int,
    sector_size: int,
    revolution: int,
    scale: Optional[float],
    T_ticks: Optional[float],
    strict_crc: bool,
    fill: int,
    allow_size_mismatch: bool,
) -> Tuple[bytes, Dict]:
    ext = os.path.splitext(in_path)[1].lower()
    expected_size = tracks * heads * spt * sector_size

    if ext == ".scp":
        img, rep = decode_scp_to_img(
            in_path,
            out_path,
            tracks=tracks,
            heads=heads,
            spt=spt,
            sector_size=sector_size,
            revolution=revolution,
            scale=scale,
            T_ticks=T_ticks,
            strict_crc=strict_crc,
            json_report_path=None,
        )
        return img, rep

    if ext == ".imd":
        img, rep = decode_imd_to_img(
            in_path,
            out_path,
            tracks=tracks,
            heads=heads,
            spt=spt,
            sector_size=sector_size,
            fill=fill,
            strict_geom=True,
        )
        return img, rep

    if ext in (".img", ".bin", ".raw"):
        return read_raw_image(in_path, expected_size=expected_size, out_path=out_path, allow_size_mismatch=allow_size_mismatch)

    raise ValueError(f"Unsupported input extension '{ext}'. Expected .scp, .imd, or .img/.bin/.raw")




def main() -> None:
    ap = argparse.ArgumentParser(
        description=(
            "Decode IBM-PC-style MFM images to a raw sector .img and/or compare two images.\n"
            "Accepted inputs: .scp (SuperCard Pro flux), .imd (ImageDisk), or raw .img/.bin/.raw.\n"
            "NOTE: Outputs are derivatives; keep original flux/IMD unmodified for archival storage."
        )
    )
    ap.add_argument("input", help="Input image: .scp, .imd, or raw .img/.bin/.raw")
    ap.add_argument("-o", "--out", default=None, help="Write decoded raw sector image (.img). Required unless using --compare.")
    ap.add_argument("--compare", default=None, help="Reference image to compare against (.scp/.imd/.img/.bin/.raw)")

    ap.add_argument("--tracks", type=int, default=80, help="Cylinders/tracks per side (default: 80)")
    ap.add_argument("--heads", type=int, default=2, help="Number of heads/sides (default: 2)")
    ap.add_argument("--spt", type=int, default=15, help="Sectors per track (default: 15)")
    ap.add_argument("--sector-size", type=int, default=512, help="Bytes per sector (default: 512)")

    ap.add_argument("--revolution", type=int, default=0, help="Which captured revolution to decode from SCP (0-based)")
    ap.add_argument("--scale", type=float, default=None, help="Flux stored-value scale for SCP (auto if omitted)")
    ap.add_argument("--T-ticks", type=float, default=None, help="Bitcell T in 25ns ticks for SCP (auto if omitted)")
    ap.add_argument("--strict-crc", action="store_true", help="For SCP: require both ID and DATA CRCs to be valid")

    ap.add_argument("--fill", type=lambda s: int(s, 0), default=0x00,
                    help="For IMD unavailable sectors: fill byte (e.g. 0x00 or 0xE5). Default: 0x00")
    ap.add_argument("--allow-size-mismatch", action="store_true",
                    help="Allow comparing raw images of different sizes (reports first mismatch/extra bytes).")

    ap.add_argument("--json-report", default=None, help="Write a JSON report (decode + optional compare) to this path")

    args = ap.parse_args()

    if args.out is None and args.compare is None:
        ap.error("Either specify --out to write a decoded .img, or use --compare to compare against a reference.")

    # Decode primary input
    img_a, rep_a = decode_any_to_bytes(
        args.input,
        out_path=args.out,
        tracks=args.tracks,
        heads=args.heads,
        spt=args.spt,
        sector_size=args.sector_size,
        revolution=args.revolution,
        scale=args.scale,
        T_ticks=args.T_ticks,
        strict_crc=args.strict_crc,
        fill=args.fill,
        allow_size_mismatch=args.allow_size_mismatch,
    )

    compare_rep = None
    rep_b = None

    if args.compare:
        img_b, rep_b = decode_any_to_bytes(
            args.compare,
            out_path=None,
            tracks=args.tracks,
            heads=args.heads,
            spt=args.spt,
            sector_size=args.sector_size,
            revolution=args.revolution,
            scale=args.scale,
            T_ticks=args.T_ticks,
            strict_crc=args.strict_crc,
            fill=args.fill,
            allow_size_mismatch=args.allow_size_mismatch,
        )
        compare_rep = compare_images(img_a, img_b, allow_size_mismatch=args.allow_size_mismatch)

        print("COMPARE")
        print(f"  A: {os.path.abspath(args.input)}")
        print(f"     type={rep_a.get('input_type')} sha256={compare_rep['sha256_a']} len={compare_rep['len_a']}")
        print(f"  B: {os.path.abspath(args.compare)}")
        print(f"     type={rep_b.get('input_type')} sha256={compare_rep['sha256_b']} len={compare_rep['len_b']}")
        if compare_rep["equal"]:
            print("  RESULT: MATCH (byte-identical)")
        else:
            print("  RESULT: DIFFER")
            if compare_rep["first_diff_offset"] is not None:
                print(f"  First difference at offset: {compare_rep['first_diff_offset']}")
        # Exit status: 0 match, 1 differ
        exit_code = 0 if compare_rep["equal"] else 1
    else:
        # Decode-only
        print(f"Wrote: {rep_a.get('output_img')}")
        print(f"SHA-256: {rep_a.get('sha256_img')}")
        exit_code = 0

    # Optional JSON report
    if args.json_report:
        out = {
            "input": rep_a,
            "reference": rep_b,
            "compare": compare_rep,
            "args": {
                "tracks": args.tracks,
                "heads": args.heads,
                "spt": args.spt,
                "sector_size": args.sector_size,
                "revolution": args.revolution,
                "scale": args.scale,
                "T_ticks": args.T_ticks,
                "strict_crc": bool(args.strict_crc),
                "fill": int(args.fill) & 0xFF,
                "allow_size_mismatch": bool(args.allow_size_mismatch),
            },
        }
        os.makedirs(os.path.dirname(os.path.abspath(args.json_report)) or ".", exist_ok=True)
        with open(args.json_report, "w", encoding="utf-8") as jf:
            json.dump(out, jf, indent=2, sort_keys=True)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
