#!/usr/bin/env python3
"""
scp_gcr_tool.py — Museum-grade *best-effort* decoder/QC tool for Commodore 1541/1571
SuperCard Pro (.scp) flux captures into:
  - D64 (1541 single-sided sector image; 35/40/42-track variants)
  - D71 (1571 double-sided sector image; 35 tracks per side)
  - IMG (raw 256-byte sectors in the same track/sector order as D64/D71)

It also provides:
  - compare: hash + byte-diff comparisons between any supported images,
             and SCP->(decoded)->D64/D71/IMG comparisons
  - qc: structural validation of SCP decode (per-track/per-sector),
        and D64/D71/IMG filesystem+geometry checks

Important preservation note:
  - SCP is flux-level; D64/D71/IMG are *derived* sector-level artifacts.
  - This tool targets *standard Commodore DOS* GCR (0x08 headers, 0x07 data).
  - It will not fully preserve or reliably decode many copy-protected / custom-format disks.

References:
  - SCP format specification v2.5 (Jim Drew): https://www.cbmstuff.com/downloads/scp/scp_image_specs.txt
  - Commodore 1541 sector layout (sectors/track zones): various refs including D64 format docs
  - GCR code table: standard Commodore 4-to-5 table

License: MIT (you may embed/modify for museum workflows)
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import io
import json
import math
import os
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Iterable

# -----------------------------
# Utilities
# -----------------------------

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def md5_file(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def first_diff(a: bytes, b: bytes) -> Optional[int]:
    n = min(len(a), len(b))
    for i in range(n):
        if a[i] != b[i]:
            return i
    if len(a) != len(b):
        return n
    return None



def petscii_to_str(bs: bytes) -> str:
    """
    Minimal PETSCII-to-displayable conversion for disk labels/IDs.
    This is not a full PETSCII implementation; it is sufficient for QC reporting.
    """
    out = []
    for b in bs:
        if b == 0xA0:
            out.append(" ")
        elif 0x20 <= b <= 0x7E:
            out.append(chr(b))
        else:
            out.append(".")
    return "".join(out)
# -----------------------------
# D64/D71 geometry
# -----------------------------

def sectors_per_track_1541(track_1based: int) -> int:
    # Standard DOS zones; tracks 36+ are "non-standard" but commonly use zone 3 (17 sectors).
    if 1 <= track_1based <= 17:
        return 21
    if 18 <= track_1based <= 24:
        return 19
    if 25 <= track_1based <= 30:
        return 18
    if track_1based >= 31:
        return 17
    raise ValueError(f"Illegal track {track_1based}")

def d64_expected_size(tracks: int) -> int:
    # D64 is raw 256-byte sectors concatenated in track order.
    # Common: 35 tracks => 174848; 40 => 196608; 42 => 205312.
    total_sectors = 0
    for t in range(1, tracks + 1):
        total_sectors += sectors_per_track_1541(t)
    return total_sectors * 256

def d71_expected_size() -> int:
    # 35 tracks per side, 2 sides => 349696
    return d64_expected_size(35) * 2

def d64_offset_for_ts(track_1based: int, sector: int, tracks: int) -> int:
    # Compute linear sector index as sum of sectors in preceding tracks + sector.
    if track_1based < 1 or track_1based > tracks:
        raise ValueError(f"Track {track_1based} out of range 1..{tracks}")
    spt = sectors_per_track_1541(track_1based)
    if sector < 0 or sector >= spt:
        raise ValueError(f"Sector {sector} out of range 0..{spt-1} for track {track_1based}")
    idx = 0
    for t in range(1, track_1based):
        idx += sectors_per_track_1541(t)
    idx += sector
    return idx * 256

# -----------------------------
# CBM GCR (4-to-5) decode
# -----------------------------

# Standard Commodore GCR code table: 4-bit nibble -> 5-bit code.
# We'll build the inverse: 5-bit code -> 4-bit nibble.
_NIBBLE_TO_CODE = {
    0x0: 0x0A, 0x1: 0x0B, 0x2: 0x12, 0x3: 0x13,
    0x4: 0x0E, 0x5: 0x0F, 0x6: 0x16, 0x7: 0x17,
    0x8: 0x09, 0x9: 0x19, 0xA: 0x1A, 0xB: 0x1B,
    0xC: 0x0D, 0xD: 0x1D, 0xE: 0x1E, 0xF: 0x15,
}
_CODE_TO_NIBBLE = {v: k for k, v in _NIBBLE_TO_CODE.items()}

def gcr_decode_5bytes_to_4bytes(gcr5: bytes) -> Optional[bytes]:
    """
    Decode 5 GCR bytes (40 bits) into 4 decoded bytes (32 bits).
    Returns None if any 5-bit code is invalid.
    """
    if len(gcr5) != 5:
        raise ValueError("Need exactly 5 bytes")
    # Pack into 40-bit integer big-endian.
    v = int.from_bytes(gcr5, "big")
    # Extract eight 5-bit groups, MSB-first.
    nibbles: List[int] = []
    for i in range(8):
        shift = 35 - i * 5
        code5 = (v >> shift) & 0x1F
        if code5 not in _CODE_TO_NIBBLE:
            return None
        nibbles.append(_CODE_TO_NIBBLE[code5])
    # Combine into 4 bytes: high nibble then low nibble per byte.
    out = bytearray()
    for i in range(0, 8, 2):
        out.append((nibbles[i] << 4) | nibbles[i + 1])
    return bytes(out)

def gcr_decode_stream(gcr_bytes: bytes, want_decoded_len: int) -> Optional[bytes]:
    """
    Decode a stream of GCR bytes into decoded bytes until want_decoded_len reached.
    We decode in 5-byte groups -> 4 bytes.
    Returns None if an invalid GCR code is encountered.
    """
    out = bytearray()
    i = 0
    while len(out) < want_decoded_len:
        if i + 5 > len(gcr_bytes):
            return None
        chunk = gcr_bytes[i:i+5]
        dec4 = gcr_decode_5bytes_to_4bytes(chunk)
        if dec4 is None:
            return None
        out.extend(dec4)
        i += 5
    return bytes(out[:want_decoded_len])

# -----------------------------
# SCP parsing (v2.x)
# -----------------------------

@dataclass
class ScpRev:
    duration_ticks_25ns: int  # units of 25ns per spec, regardless of capture resolution multiplier
    flux_words: int           # number of flux intervals (bitcells), not bytes
    flux_offset_rel: int      # offset from start of TDH ("T" in "TRK") to flux data

@dataclass
class ScpTrack:
    tdh_track_number: int     # 0..167
    revs: List[ScpRev]
    flux_data: bytes          # concatenated per-rev flux streams (16-bit big-endian words)

@dataclass
class ScpImage:
    version_byte: int
    disk_type: int
    num_revs: int
    start_track: int
    end_track: int
    flags: int
    bitcell_width_bits: int   # 0 means 16-bit
    heads: int                # 0=both, 1=side0, 2=side1
    resolution_mult: int      # 0=25ns, 1=50ns, ...
    tdh_table_offset: int     # 0x10 normally, 0x80 if extended mode
    track_offsets: List[int]  # 168 entries
    tracks: Dict[int, ScpTrack]  # key = tdh track number (0..167)

def parse_scp(path: Path) -> ScpImage:
    data = path.read_bytes()
    if len(data) < 0x2B0:
        raise ValueError("File too small to be SCP")
    if data[0:3] != b"SCP":
        raise ValueError("Missing SCP magic")
    ver = data[0x03]
    disk_type = data[0x04]
    num_revs = data[0x05]
    start = data[0x06]
    end = data[0x07]
    flags = data[0x08]
    enc = data[0x09]
    heads = data[0x0A]
    res = data[0x0B]
    extended = (flags >> 6) & 1
    tdh_table_offset = 0x80 if extended else 0x10
    # Track offset table for floppy images is 168 longwords.
    table = []
    for i in range(168):
        off = struct.unpack_from("<I", data, tdh_table_offset + i * 4)[0]
        table.append(off)
    tracks: Dict[int, ScpTrack] = {}
    # Parse each present track TDH
    for tdh_track_num, off in enumerate(table):
        if off == 0:
            continue
        if off + 4 > len(data):
            continue
        if data[off:off+3] != b"TRK":
            # corrupt pointer/table; skip
            continue
        trkno = data[off+3]
        # Each rev has 3 longwords: duration, length, offset
        revs: List[ScpRev] = []
        base = off + 4
        for r in range(num_revs):
            dur = struct.unpack_from("<I", data, base + r*12 + 0)[0]
            length_words = struct.unpack_from("<I", data, base + r*12 + 4)[0]
            flux_off_rel = struct.unpack_from("<I", data, base + r*12 + 8)[0]
            revs.append(ScpRev(dur, length_words, flux_off_rel))
        # Flux data is stored sequentially, but offsets are authoritative.
        # Read the full span covering all revs.
        # Determine min/max relative offset across revs.
        min_rel = min(rv.flux_offset_rel for rv in revs)
        max_end_rel = max(rv.flux_offset_rel + (rv.flux_words * (2 if enc == 0 else enc//8)) for rv in revs)
        tdh_len = max_end_rel
        if off + tdh_len > len(data):
            # clamp
            tdh_len = max(0, len(data) - off)
        flux_blob = data[off:off+tdh_len]
        tracks[trkno] = ScpTrack(trkno, revs, flux_blob)
    return ScpImage(
        version_byte=ver, disk_type=disk_type, num_revs=num_revs,
        start_track=start, end_track=end, flags=flags,
        bitcell_width_bits=enc, heads=heads, resolution_mult=res,
        tdh_table_offset=tdh_table_offset, track_offsets=table, tracks=tracks
    )

def scp_resolution_ns(scp: ScpImage) -> int:
    # Spec: base 25ns; multiplier value adds 25ns each step (0=>25ns,1=>50ns,...)
    return 25 * (1 + scp.resolution_mult)

def iter_flux_intervals_16be(track: ScpTrack, rev: ScpRev) -> List[int]:
    """
    Return flux intervals as integer ticks in units of capture resolution (not ns),
    handling SCP overflow semantics (0 means 65536, and multiple 0 can chain).
    """
    start = rev.flux_offset_rel
    # Only 16-bit supported here.
    byte_count = rev.flux_words * 2
    blob = track.flux_data
    if start + byte_count > len(blob):
        byte_count = max(0, len(blob) - start)
    raw = blob[start:start+byte_count]
    intervals: List[int] = []
    acc = 0
    for i in range(0, len(raw), 2):
        w = struct.unpack_from(">H", raw, i)[0]
        if w == 0:
            acc += 65536
        else:
            intervals.append(acc + w)
            acc = 0
    if acc:
        # trailing overflow without terminal word -> keep as last interval
        intervals.append(acc)
    return intervals

# -----------------------------
# Flux -> bitstream -> raw bytes (best-effort PLL)
# -----------------------------

def estimate_bitcell_ticks(intervals: List[int]) -> float:
    """
    Estimate the fundamental bitcell period (in tick units) from flux intervals by
    testing candidates derived from the most frequent intervals.

    For Commodore 1541/1571 standard GCR, flux intervals tend to cluster near
    1x/2x/3x of the bitcell.
    """
    if not intervals:
        return 1.0
    from collections import Counter
    cnt = Counter(intervals)
    top = [v for v, _ in cnt.most_common(8)]
    cands = set()
    for v in top:
        for k in range(1, 5):
            cands.add(v / float(k))
    cands.add(float(min(intervals)))

    sample = intervals[: min(len(intervals), 8000)]
    best_err = None
    best_cell = None
    for cell in cands:
        if cell <= 0:
            continue
        err = 0.0
        for dt in sample:
            n = round(dt / cell)
            if n < 1:
                n = 1
            if n > 8:
                n = 8
            err += abs(dt - n * cell)
        if best_err is None or err < best_err:
            best_err = err
            best_cell = cell
    return float(best_cell if best_cell is not None else 1.0)

def flux_to_bits(intervals: List[int]) -> List[int]:
    """
    Convert flux intervals (ticks) to a recovered bitstream where '1' corresponds to a transition.

    This uses:
      1) a histogram-derived initial bitcell estimate
      2) a gentle adaptive loop to track drift

    This is intentionally conservative and tuned for **standard CBM DOS GCR**.
    """
    if not intervals:
        return []
    cell = estimate_bitcell_ticks(intervals)
    alpha = 0.002  # adaptation rate
    bits: List[int] = []
    for dt in intervals:
        if dt <= 0:
            continue
        n = int(round(dt / cell))
        if n < 1:
            n = 1
        if n > 8:
            n = 8
        if n > 1:
            bits.extend([0] * (n - 1))
        bits.append(1)
        # Adapt cell based on observed average cell length for this interval
        target = dt / n
        cell += alpha * (target - cell)
    return bits

def bits_to_bytes(bits: List[int], msb_first: bool, bit_offset: int) -> bytes:
    """
    Convert bits to bytes with given bit_offset (0..7).
    bit_offset means skip that many initial bits before grouping into bytes.
    """
    if bit_offset:
        bits = bits[bit_offset:]
    out = bytearray()
    for i in range(0, len(bits) - 7, 8):
        b = 0
        if msb_first:
            for j in range(8):
                b = (b << 1) | (bits[i+j] & 1)
        else:
            for j in range(8):
                b |= (bits[i+j] & 1) << j
        out.append(b)
    return bytes(out)

def find_best_byte_alignment(bits: List[int]) -> Tuple[bytes, bool, int]:
    """
    Choose msb_first and bit_offset that yields plausible CBM block-sync bytes (0x52/0x55)
    following a sync region (lots of 1s).
    Returns (byte_stream, msb_first, bit_offset).
    """
    # Find candidate sync end positions by locating long runs of 1s.
    # We'll test alignment around the earliest long run.
    best = None
    for msb_first in (True, False):
        for off in range(8):
            stream = bits_to_bytes(bits, msb_first=msb_first, bit_offset=off)
            # Heuristic score: count occurrences of 0x52/0x55 that are preceded by 0xFF
            score = 0
            for i in range(1, min(len(stream), 20000)):
                if stream[i] in (0x52, 0x55) and stream[i-1] == 0xFF:
                    score += 2
                if stream[i] == 0xFF and i+1 < len(stream) and stream[i+1] == 0xFF:
                    score += 1
            if best is None or score > best[0]:
                best = (score, stream, msb_first, off)
    assert best is not None
    return best[1], best[2], best[3]

# -----------------------------
# Track decode (CBM DOS sectors)
# -----------------------------

@dataclass
class DecodedSector:
    track: int
    sector: int
    data: bytes
    header_id1: Optional[int] = None
    header_id2: Optional[int] = None
    header_checksum_ok: Optional[bool] = None
    data_checksum_ok: Optional[bool] = None
    source_rev: Optional[int] = None

@dataclass
class SectorStatus:
    side: int
    track: int
    sector: int
    status: str  # ok | missing | header_checksum_bad | data_checksum_bad | multi_read_disagreement
    chosen_rev: Optional[int] = None
    header_checksum_ok: Optional[bool] = None
    data_checksum_ok: Optional[bool] = None
    inconsistent_reads: bool = False  # True if some revs missing/bad while others ok
    revs_seen: List[int] = dataclasses.field(default_factory=list)
    unique_payloads: int = 0
    payload_sha1s: List[str] = dataclasses.field(default_factory=list)
    header_ids: List[Tuple[Optional[int], Optional[int]]] = dataclasses.field(default_factory=list)

def _sha1_bytes(b: bytes) -> str:
    return hashlib.sha1(b).hexdigest()
def decode_cbm_track_from_flux(intervals: List[int], rev_index: int) -> Tuple[Dict[Tuple[int,int], DecodedSector], Dict]:
    """
    Decode CBM DOS sectors from one track revolution flux.
    Returns (sectors_by_ts, stats).
    """
    bits = flux_to_bits(intervals)
    if not bits:
        return {}, {"errors":["no_bits"], "rev": rev_index}
    stream, msb_first, bit_off = find_best_byte_alignment(bits)

    # Scan for sync (0xFF runs), then attempt to decode blocks.
    sectors: Dict[Tuple[int,int], DecodedSector] = {}
    pending_header: Optional[Tuple[int,int,int,int,bool]] = None  # (trk, sec, id1,id2,ck_ok)
    stats = {
        "rev": rev_index,
        "msb_first": msb_first,
        "bit_offset": bit_off,
        "header_blocks": 0,
        "data_blocks": 0,
        "header_ck_bad": 0,
        "data_ck_bad": 0,
        "gcr_decode_fail": 0,
        "paired": 0,
    }

    i = 0
    n = len(stream)
    # treat track as circular-ish by scanning once; good enough for QC/standard disks
    while i < n-20:
        # look for sync run
        if stream[i] != 0xFF:
            i += 1
            continue
        j = i
        while j < n and stream[j] == 0xFF:
            j += 1
        if j - i < 5:
            i = j
            continue
        # Candidate block starts at j
        if j >= n:
            break
        if stream[j] not in (0x52, 0x55):
            i = j
            continue

        # Attempt to decode enough bytes to identify block type.
        # Decode first 4 decoded bytes (needs 5 gcr bytes).
        gcr0 = stream[j:j+5]
        dec4 = gcr_decode_5bytes_to_4bytes(gcr0)
        if dec4 is None:
            stats["gcr_decode_fail"] += 1
            i = j + 1
            continue
        block_type = dec4[0]
        if block_type == 0x08:
            # Header: decode 6 bytes minimum (code, checksum, sector, track, id1, id2)
            need = 6
            gcr_need = math.ceil(need / 4) * 5
            gcr_bytes = stream[j:j+gcr_need]
            dec = gcr_decode_stream(gcr_bytes, need)
            if dec is None:
                stats["gcr_decode_fail"] += 1
                i = j + 1
                continue
            stats["header_blocks"] += 1
            code, cks, sec, trk, id1, id2 = dec
            ck_calc = sec ^ trk ^ id1 ^ id2
            ck_ok = (ck_calc & 0xFF) == cks
            if not ck_ok:
                stats["header_ck_bad"] += 1
            pending_header = (trk, sec, id1, id2, ck_ok)
            i = j + gcr_need
            continue
        elif block_type == 0x07:
            # Data block: decode 1 + 256 + 1 = 258 bytes
            need = 258
            gcr_need = math.ceil(need / 4) * 5
            gcr_bytes = stream[j:j+gcr_need]
            dec = gcr_decode_stream(gcr_bytes, need)
            if dec is None:
                stats["gcr_decode_fail"] += 1
                i = j + 1
                continue
            stats["data_blocks"] += 1
            code = dec[0]
            payload = dec[1:257]
            data_ck = dec[257]
            ck_calc = 0
            for b in payload:
                ck_calc ^= b
            ck_ok = (ck_calc & 0xFF) == data_ck
            if not ck_ok:
                stats["data_ck_bad"] += 1
            if pending_header is not None:
                trk, sec, id1, id2, h_ok = pending_header
                key = (trk, sec)
                # If multiple occurrences, keep first good checksum, else overwrite with good
                if key not in sectors or (sectors[key].data_checksum_ok is False and ck_ok is True):
                    sectors[key] = DecodedSector(
                        track=trk, sector=sec, data=bytes(payload),
                        header_id1=id1, header_id2=id2,
                        header_checksum_ok=h_ok,
                        data_checksum_ok=ck_ok,
                        source_rev=rev_index
                    )
                stats["paired"] += 1
            i = j + gcr_need
            continue
        else:
            # Not a DOS header/data block.
            i = j + 1
            continue

    return sectors, stats

# -----------------------------
# Build D64/D71/IMG from decoded sectors
# -----------------------------

@dataclass
class DecodeResult:
    geometry: str
    tracks: int
    sides: int
    image_bytes: bytes
    sectors_present: Dict[Tuple[int,int,int], bool]  # (side,track,sector)
    per_track_stats: List[dict]
    sector_status_map: List[SectorStatus]
    sector_status_summary: Dict[str, int]

def decode_scp_to_sector_image(
    scp_path: Path,
    out_format: str,
    prefer_rev: str = "best",
    assume_tracks: Optional[int] = None,
) -> DecodeResult:
    """
    Decode SCP as Commodore 1541/1571 DOS sectors.
    out_format: d64 | d71 | img

    Returns:
      - image_bytes: reconstructed sector image (derived artifact)
      - sector_status_map: per T/S status records suitable for archival QC
      - sector_status_summary: counts by status
    """
    scp = parse_scp(scp_path)
    if scp.bitcell_width_bits not in (0, 16):
        raise ValueError(f"Unsupported SCP bitcell width {scp.bitcell_width_bits}; only 16-bit supported")

    # Determine sides: heads byte (0=both,1=side0,2=side1)
    sides = 2 if scp.heads == 0 else 1

    # Map TDH track numbers -> (track_1based, side)
    decoded_by_side: List[Dict[Tuple[int,int], DecodedSector]] = [dict() for _ in range(sides)]
    per_track_stats: List[dict] = []

    # Evidence for sector-status map (filled per (side,track,sector))
    evidence: Dict[Tuple[int,int,int], Dict] = {}

    for tdh_trkno, trk in sorted(scp.tracks.items()):
        phys_track0 = tdh_trkno // 2
        head = tdh_trkno % 2

        if scp.heads == 0:
            side = head
        else:
            side = 0

        ctrack = phys_track0 + 1  # Commodore-style 1-based track number

        # Decode each rev, then merge (prefer good checksums)
        rev_sectors_list: List[Dict[Tuple[int,int], DecodedSector]] = []
        rev_stats_list: List[dict] = []

        for r, rv in enumerate(trk.revs):
            intervals = iter_flux_intervals_16be(trk, rv)
            secmap, st = decode_cbm_track_from_flux(intervals, rev_index=r)
            st.update({"tdh_track": tdh_trkno, "track": ctrack, "side": side})
            rev_sectors_list.append(secmap)
            rev_stats_list.append(st)

        # Merge to a best-effort single view for constructing the derived image
        merged: Dict[Tuple[int,int], DecodedSector] = {}
        for secmap in rev_sectors_list:
            for k, v in secmap.items():
                if k not in merged or (merged[k].data_checksum_ok is False and v.data_checksum_ok is True):
                    merged[k] = v

        # Save into side map with only matching track number
        for (trkno, secno), sec in merged.items():
            if trkno != ctrack:
                continue
            decoded_by_side[side][(trkno, secno)] = sec

        # Record evidence for sector-status reporting for this track
        try:
            exp_spt = sectors_per_track_1541(ctrack)
        except Exception:
            exp_spt = 17  # conservative fallback

        for secno in range(exp_spt):
            key = (side, ctrack, secno)
            cand = []
            for r, secmap in enumerate(rev_sectors_list):
                ds = secmap.get((ctrack, secno))
                if ds is None:
                    continue
                cand.append({
                    "rev": r,
                    "header_checksum_ok": ds.header_checksum_ok,
                    "data_checksum_ok": ds.data_checksum_ok,
                    "id1": ds.header_id1,
                    "id2": ds.header_id2,
                    "payload_sha1": _sha1_bytes(ds.data),
                })
            chosen = merged.get((ctrack, secno))
            evidence[key] = {
                "expected": True,
                "candidates": cand,
                "chosen": {
                    "rev": chosen.source_rev,
                    "header_checksum_ok": chosen.header_checksum_ok,
                    "data_checksum_ok": chosen.data_checksum_ok,
                    "id1": chosen.header_id1,
                    "id2": chosen.header_id2,
                    "payload_sha1": _sha1_bytes(chosen.data),
                } if chosen is not None else None
            }

        # summarize per-track
        per_track_stats.append({
            "side": side,
            "track": ctrack,
            "tdh_track": tdh_trkno,
            "revs": rev_stats_list,
            "sectors_decoded": len({k for k in merged.keys() if k[0] == ctrack}),
        })

    # Determine track count for output
    max_track = 0
    for side_map in decoded_by_side:
        for (t, _s) in side_map.keys():
            max_track = max(max_track, t)

    if assume_tracks is not None:
        tracks = assume_tracks
    else:
        if max_track <= 35:
            tracks = 35
        elif max_track <= 40:
            tracks = 40
        else:
            tracks = max_track

    # Build the derived image
    if out_format.lower() == "d71":
        if sides != 2:
            raise ValueError("Cannot create D71: SCP does not contain both sides (heads=0 required)")
        tracks = 35  # D71 is defined as 35 per side
        out_size = d71_expected_size()
        img = bytearray(b"\x00" * out_size)
        present: Dict[Tuple[int,int,int], bool] = {}
        for side in (0, 1):
            for (t, s), sec in decoded_by_side[side].items():
                if t < 1 or t > 35:
                    continue
                off = d64_offset_for_ts(t, s, 35) + side * d64_expected_size(35)
                img[off:off+256] = sec.data
                present[(side, t, s)] = True
        geom = "D71 (1571 DS)"
        final_sides = 2
    else:
        out_size = d64_expected_size(tracks)
        img = bytearray(b"\x00" * out_size)
        present = {}
        for (t, s), sec in decoded_by_side[0].items():
            if t < 1 or t > tracks:
                continue
            off = d64_offset_for_ts(t, s, tracks)
            img[off:off+256] = sec.data
            present[(0, t, s)] = True
        geom = f"{out_format.upper()} (1541 SS) tracks={tracks}"
        final_sides = 1

    # Build sector-status map for the *declared* output geometry
    sector_status: List[SectorStatus] = []
    summary: Dict[str, int] = {}
    num_revs = scp.num_revs

    def push_status(ss: SectorStatus) -> None:
        sector_status.append(ss)
        summary[ss.status] = summary.get(ss.status, 0) + 1

    for side in range(final_sides):
        for t in range(1, tracks + 1):
            exp = sectors_per_track_1541(t) if (out_format.lower() != "d71" or t <= 35) else sectors_per_track_1541(t)
            for s in range(exp):
                key = (side, t, s)
                ev = evidence.get(key)
                cands = ev["candidates"] if ev else []
                chosen = ev["chosen"] if ev else None

                if not cands and not chosen:
                    push_status(SectorStatus(side=side, track=t, sector=s, status="missing"))
                    continue

                sha1s = []
                revs_seen = []
                header_ids = []
                any_bad = False
                for c in cands:
                    sha1s.append(c["payload_sha1"])
                    revs_seen.append(c["rev"])
                    header_ids.append((c.get("id1"), c.get("id2")))
                    if (c.get("header_checksum_ok") is False) or (c.get("data_checksum_ok") is False):
                        any_bad = True

                uniq_sha1s = sorted(set(sha1s))
                disagreement = len(uniq_sha1s) > 1

                hdr_ok = chosen["header_checksum_ok"] if chosen else (cands[0].get("header_checksum_ok") if cands else None)
                dat_ok = chosen["data_checksum_ok"] if chosen else (cands[0].get("data_checksum_ok") if cands else None)
                chosen_rev = chosen["rev"] if chosen else None

                inconsistent = any_bad or (len(revs_seen) != len(set(revs_seen))) or (len(set(revs_seen)) < num_revs)

                if disagreement:
                    status = "multi_read_disagreement"
                else:
                    if hdr_ok is False:
                        status = "header_checksum_bad"
                    elif dat_ok is False:
                        status = "data_checksum_bad"
                    else:
                        status = "ok"

                push_status(SectorStatus(
                    side=side,
                    track=t,
                    sector=s,
                    status=status,
                    chosen_rev=chosen_rev,
                    header_checksum_ok=hdr_ok,
                    data_checksum_ok=dat_ok,
                    inconsistent_reads=inconsistent,
                    revs_seen=sorted(set(revs_seen)),
                    unique_payloads=len(uniq_sha1s),
                    payload_sha1s=uniq_sha1s,
                    header_ids=sorted(set(header_ids)),
                ))

    return DecodeResult(
        geometry=geom,
        tracks=tracks,
        sides=sides,
        image_bytes=bytes(img),
        sectors_present=present,
        per_track_stats=per_track_stats,
        sector_status_map=sector_status,
        sector_status_summary=summary,
    )

# -----------------------------
# QC for D64/D71/IMG (geometry + CBM DOS filesystem)
# -----------------------------

@dataclass
class QcIssue:
    severity: str  # INFO/WARN/ERROR
    message: str

def qc_dxx_image(path: Path) -> Dict:
    """
    QC geometry + basic CBM DOS filesystem structure (BAM+dir) if present.
    """
    data = path.read_bytes()
    size = len(data)
    issues: List[QcIssue] = []
    fmt = path.suffix.lower().lstrip(".")
    geom = None

    if size == d64_expected_size(35):
        geom = "D64/IMG 35-track"
        tracks = 35
        sides = 1
    elif size == d64_expected_size(40):
        geom = "D64/IMG 40-track"
        tracks = 40
        sides = 1
    elif size == d64_expected_size(42):
        geom = "D64/IMG 42-track"
        tracks = 42
        sides = 1
    elif size == d71_expected_size():
        geom = "D71 35x2"
        tracks = 35
        sides = 2
    else:
        issues.append(QcIssue("ERROR", f"Unrecognized image size {size} bytes"))
        tracks = None
        sides = None

    report = {
        "path": str(path),
        "size": size,
        "geometry": geom,
        "issues": [dataclasses.asdict(x) for x in issues],
        "filesystem": None,
    }

    if tracks is None:
        return report

    # Helper to get sector bytes
    def get_sector(side: int, track: int, sector: int) -> bytes:
        if sides == 2:
            base = side * d64_expected_size(35)
            off = base + d64_offset_for_ts(track, sector, 35)
        else:
            off = d64_offset_for_ts(track, sector, tracks)
        return data[off:off+256]

    # BAM expected at 18/0 on side 0
    try:
        bam = get_sector(0, 18, 0)
    except Exception as e:
        issues.append(QcIssue("ERROR", f"Cannot read BAM sector 18/0: {e}"))
        report["issues"] = [dataclasses.asdict(x) for x in issues]
        return report

    # Parse disk name, id, dos type (per common docs)
    disk_name = petscii_to_str(bam[0x90:0xA0])
    disk_id = petscii_to_str(bam[0xA2:0xA4])
    dos_type = petscii_to_str(bam[0xA5:0xA7])
    # Directory first sector pointer at 0x00/0x01 should be 18/1
    dir_t = bam[0]
    dir_s = bam[1]
    if (dir_t, dir_s) != (18, 1):
        issues.append(QcIssue("WARN", f"BAM dir pointer is {dir_t}/{dir_s}, expected 18/1"))

    # Walk directory chain (track 18)
    dir_entries = []
    seen = set()
    t, s = dir_t, dir_s
    max_steps = 200
    steps = 0
    while t != 0 and steps < max_steps:
        if (t, s) in seen:
            issues.append(QcIssue("ERROR", f"Directory chain loops at {t}/{s}"))
            break
        seen.add((t, s))
        try:
            sec = get_sector(0, t, s)
        except Exception as e:
            issues.append(QcIssue("ERROR", f"Directory sector read failed at {t}/{s}: {e}"))
            break
        next_t, next_s = sec[0], sec[1]
        # 8 entries of 32 bytes from offset 2
        for i in range(8):
            ent = sec[2+i*32:2+(i+1)*32]
            if len(ent) < 32:
                continue
            # Directory entry layout (32 bytes, but last 2 are typically unused):
            # 0: file type (bit 7 indicates properly closed, low nibble is type)
            # 1-2: start track/sector
            # 3-18: filename (16 bytes)
            # 28-29: block count (lo/hi)
            ftype_raw = ent[0]
            ftype = ftype_raw & 0x0F
            if ftype == 0:
                continue
            start_t, start_s = ent[1], ent[2]
            name = petscii_to_str(ent[3:19]).rstrip()
            blocks = ent[28] + (ent[29] << 8)
            dir_entries.append({
                "type": ftype,
                "type_raw": ftype_raw,
                "closed": bool(ftype_raw & 0x80),
                "start": (start_t, start_s),
                "name": name,
                "blocks": blocks
            })
        t, s = next_t, next_s
        steps += 1

    if steps >= max_steps:
        issues.append(QcIssue("WARN", "Directory chain exceeded max steps; possibly corrupt"))

    # Basic file chain validation (for sequential-like files; we don't interpret REL records deeply)
    used = set()
    file_chain_issues = 0
    for ent in dir_entries:
        t, s = ent["start"]
        blocks_seen = 0
        chain_seen = set()
        while t != 0:
            if (t, s) in chain_seen:
                issues.append(QcIssue("ERROR", f"File '{ent['name']}' chain loops at {t}/{s}"))
                file_chain_issues += 1
                break
            chain_seen.add((t, s))
            try:
                sec = get_sector(0, t, s)
            except Exception as e:
                issues.append(QcIssue("ERROR", f"File '{ent['name']}' sector read failed at {t}/{s}: {e}"))
                file_chain_issues += 1
                break
            if (t, s) in used:
                issues.append(QcIssue("ERROR", f"File '{ent['name']}' overlaps at {t}/{s}"))
                file_chain_issues += 1
                break
            used.add((t, s))
            blocks_seen += 1
            t, s = sec[0], sec[1]
        # Compare block count loosely (directory counts blocks; might differ for REL)
        if ent["type"] in (1,2,3):  # DEL, SEQ, PRG
            if blocks_seen != ent["blocks"]:
                issues.append(QcIssue("WARN", f"File '{ent['name']}' dir blocks={ent['blocks']} but chain={blocks_seen}"))
    fs = {
        "disk_name_raw": disk_name,
        "disk_id_raw": disk_id,
        "dos_type_raw": dos_type,
        "dir_entries": dir_entries,
        "dir_entry_count": len(dir_entries),
    }
    report["filesystem"] = fs
    report["issues"] = [dataclasses.asdict(x) for x in issues]
    return report

# -----------------------------
# QC for SCP (decode coverage report)
# -----------------------------

def qc_scp_decode(path: Path, assume_tracks: Optional[int]=None) -> Dict:
    scp = parse_scp(path)
    # Decode to a "virtual" D64-like map but primarily report per track/sector counts.
    res = decode_scp_to_sector_image(path, out_format="d64", assume_tracks=assume_tracks)
    issues = []
    # Evaluate per track: expected sector count vs present
    present_by_track = {}
    for (side, t, s) in res.sectors_present.keys():
        present_by_track.setdefault((side, t), set()).add(s)
    for side in range(min(res.sides,1)):
        for t in range(1, res.tracks+1):
            exp = sectors_per_track_1541(t)
            got = len(present_by_track.get((0, t), set()))
            if got == 0:
                issues.append({"severity":"WARN", "message":f"Track {t}: no sectors decoded"})
            elif got != exp:
                issues.append({"severity":"WARN", "message":f"Track {t}: decoded {got}/{exp} sectors"})
    return {
        "path": str(path),
        "scp_header": {
            "version_byte": scp.version_byte,
            "disk_type": scp.disk_type,
            "num_revs": scp.num_revs,
            "start_track": scp.start_track,
            "end_track": scp.end_track,
            "flags": scp.flags,
            "heads": scp.heads,
            "resolution_ns": scp_resolution_ns(scp),
        },
        "decode_geometry": {"tracks": res.tracks, "sides": res.sides, "geometry": res.geometry},
        "issues": issues,
        "per_track_stats": res.per_track_stats,
        "sector_status_summary": res.sector_status_summary,
    }

# -----------------------------
# Compare
# -----------------------------

def compare_images(path_a: Path, path_b: Path) -> Dict:
    a = path_a.read_bytes()
    b = path_b.read_bytes()
    sha_a = hashlib.sha256(a).hexdigest()
    sha_b = hashlib.sha256(b).hexdigest()
    md5_a = hashlib.md5(a).hexdigest()
    md5_b = hashlib.md5(b).hexdigest()
    diff = first_diff(a, b)
    return {
        "a": str(path_a),
        "b": str(path_b),
        "size_a": len(a),
        "size_b": len(b),
        "sha256_a": sha_a,
        "sha256_b": sha_b,
        "md5_a": md5_a,
        "md5_b": md5_b,
        "byte_identical": (diff is None and len(a)==len(b)),
        "first_diff_offset": diff,
    }

# -----------------------------
# Sector-status map output
# -----------------------------

def write_sector_status_json(path: Path, sector_status: List[SectorStatus], summary: Dict[str,int], meta: Dict) -> None:
    payload = {
        "meta": meta,
        "summary": summary,
        "sectors": [dataclasses.asdict(s) for s in sector_status],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

def write_sector_status_csv(path: Path, sector_status: List[SectorStatus]) -> None:
    import csv
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "side","track","sector","status","chosen_rev",
            "header_checksum_ok","data_checksum_ok","inconsistent_reads",
            "revs_seen","unique_payloads","payload_sha1s","header_ids"
        ])
        for s in sector_status:
            w.writerow([
                s.side, s.track, s.sector, s.status, s.chosen_rev,
                s.header_checksum_ok, s.data_checksum_ok, s.inconsistent_reads,
                ",".join(str(x) for x in s.revs_seen),
                s.unique_payloads,
                ",".join(s.payload_sha1s),
                ";".join(f"{a:02X}{b:02X}" if (a is not None and b is not None) else "" for (a,b) in s.header_ids),
            ])

# -----------------------------
# CLI
# -----------------------------

def cmd_decode(args: argparse.Namespace) -> int:
    inp = Path(args.input)
    out = Path(args.output)
    fmt = args.format.lower()
    if fmt not in ("d64", "d71", "img"):
        raise SystemExit("format must be d64|d71|img")

    # IMG uses the same raw sector concatenation as D64/D71 in this tool.
    res = decode_scp_to_sector_image(inp, out_format=("d71" if fmt == "d71" else "d64"), assume_tracks=args.tracks)
    out.write_bytes(res.image_bytes)

    # Write JSON decode report if requested
    if args.report_json:
        report = {
            "input": str(inp),
            "output": str(out),
            "output_sha256": hashlib.sha256(res.image_bytes).hexdigest(),
            "output_md5": hashlib.md5(res.image_bytes).hexdigest(),
            "geometry": res.geometry,
            "tracks": res.tracks,
            "sides": res.sides,
            "per_track_stats": res.per_track_stats,
            "sector_status_summary": res.sector_status_summary,
        }
        Path(args.report_json).write_text(json.dumps(report, indent=2), encoding="utf-8")

    # Write sector-status map if requested
    if args.sector_map_json or args.sector_map_csv:
        meta = {
            "input_scp": str(inp),
            "output_image": str(out),
            "geometry": res.geometry,
            "tracks": res.tracks,
            "sides_in_scp": res.sides,
        }
        if args.sector_map_json:
            write_sector_status_json(Path(args.sector_map_json), res.sector_status_map, res.sector_status_summary, meta)
        if args.sector_map_csv:
            write_sector_status_csv(Path(args.sector_map_csv), res.sector_status_map)

    print(f"Wrote {fmt.upper()} to {out} ({len(res.image_bytes)} bytes)")
    print(f"SHA-256: {hashlib.sha256(res.image_bytes).hexdigest()}")
    return 0

def cmd_qc(args: argparse.Namespace) -> int:
    inp = Path(args.input)
    if inp.suffix.lower() == ".scp":
        rep = qc_scp_decode(inp, assume_tracks=args.tracks)

        # Optionally emit sector-status map from SCP decode
        if args.sector_map_json or args.sector_map_csv:
            dec = decode_scp_to_sector_image(inp, out_format="d64", assume_tracks=args.tracks)
            meta = {
                "input_scp": str(inp),
                "geometry": dec.geometry,
                "tracks": dec.tracks,
                "sides_in_scp": dec.sides,
            }
            if args.sector_map_json:
                write_sector_status_json(Path(args.sector_map_json), dec.sector_status_map, dec.sector_status_summary, meta)
            if args.sector_map_csv:
                write_sector_status_csv(Path(args.sector_map_csv), dec.sector_status_map)
    else:
        rep = qc_dxx_image(inp)

    txt = json.dumps(rep, indent=2)
    if args.output:
        Path(args.output).write_text(txt, encoding="utf-8")
        print(f"Wrote QC report to {args.output}")
    else:
        print(txt)
    return 0

def cmd_compare(args: argparse.Namespace) -> int:
    a = Path(args.a)
    b = Path(args.b)
    # If one side is SCP, decode to temp and compare
    if a.suffix.lower() == ".scp" and b.suffix.lower() != ".scp":
        tmp = Path(args.temp_output) if args.temp_output else Path(str(b) + ".decoded_from_scp")
        fmt = b.suffix.lower().lstrip(".")
        if fmt not in ("d64","d71","img"):
            raise SystemExit("When comparing SCP to image, .b must be .d64/.d71/.img")
        dec = decode_scp_to_sector_image(a, out_format=("d71" if fmt=="d71" else "d64"), assume_tracks=args.tracks)
        tmp.write_bytes(dec.image_bytes)
        rep = compare_images(tmp, b)
        rep["decoded_temp"] = str(tmp)
    elif b.suffix.lower() == ".scp" and a.suffix.lower() != ".scp":
        tmp = Path(args.temp_output) if args.temp_output else Path(str(a) + ".decoded_from_scp")
        fmt = a.suffix.lower().lstrip(".")
        if fmt not in ("d64","d71","img"):
            raise SystemExit("When comparing SCP to image, .a must be .d64/.d71/.img")
        dec = decode_scp_to_sector_image(b, out_format=("d71" if fmt=="d71" else "d64"), assume_tracks=args.tracks)
        tmp.write_bytes(dec.image_bytes)
        rep = compare_images(a, tmp)
        rep["decoded_temp"] = str(tmp)
    else:
        rep = compare_images(a, b)

    txt = json.dumps(rep, indent=2)
    if args.output:
        Path(args.output).write_text(txt, encoding="utf-8")
        print(f"Wrote compare report to {args.output}")
    else:
        print(txt)
    return 0

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="scp_gcr_tool", description="Decode/QC/compare SCP (CBM GCR) and D64/D71/IMG images")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("decode", help="Decode .scp flux to D64/D71/IMG (standard CBM DOS GCR only)")
    d.add_argument("-i","--input", required=True, help="Input .scp file")
    d.add_argument("-o","--output", required=True, help="Output image file (.d64/.d71/.img)")
    d.add_argument("-f","--format", required=True, choices=["d64","d71","img"], help="Output format")
    d.add_argument("--tracks", type=int, default=None, help="Force track count for SS images (e.g., 35,40,42)")
    d.add_argument("--report-json", default=None, help="Write a JSON decode report to this path")
    d.add_argument("--sector-map-json", default=None, help="Write per T/S sector-status map (JSON) to this path")
    d.add_argument("--sector-map-csv", default=None, help="Write per T/S sector-status map (CSV) to this path")
    d.set_defaults(func=cmd_decode)

    q = sub.add_parser("qc", help="Quality check an image (.scp or .d64/.d71/.img)")
    q.add_argument("-i","--input", required=True, help="Input file (.scp/.d64/.d71/.img)")
    q.add_argument("-o","--output", default=None, help="Write JSON QC report to this path")
    q.add_argument("--tracks", type=int, default=None, help="Force track count when QC-decoding SCP")
    q.add_argument("--sector-map-json", default=None, help="(SCP only) Write per T/S sector-status map (JSON) to this path")
    q.add_argument("--sector-map-csv", default=None, help="(SCP only) Write per T/S sector-status map (CSV) to this path")
    q.set_defaults(func=cmd_qc)

    c = sub.add_parser("compare", help="Compare two images (or SCP vs D64/D71/IMG via decode+compare)")
    c.add_argument("--a", required=True, help="First file")
    c.add_argument("--b", required=True, help="Second file")
    c.add_argument("-o","--output", default=None, help="Write JSON compare report to this path")
    c.add_argument("--tracks", type=int, default=None, help="Force track count when decoding SCP")
    c.add_argument("--temp-output", default=None, help="Where to write decoded SCP temporary image (optional)")
    c.set_defaults(func=cmd_compare)

    return p

def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)

if __name__ == "__main__":
    raise SystemExit(main())
