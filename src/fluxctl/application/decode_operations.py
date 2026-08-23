"""Application-owned sector decoding and flat-image reconstruction helpers."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..apple2 import tracks_from_apple2_sector_image
from ..decoding import load_builtin_decoders
from ..decoding.mfm import mfm_decoder
from ..detection import detect_encoding
from ..layouts.loader import ensure_layout_loaded
from ..models import Bitstream, LayoutDescriptor
from ..scp import parse_scp
from ..sector.models import Sector, TrackNibbles, TrackSectors
from ..sector.reconstruct import build_track_sectors_from_revolutions
from ..sector.reconstruct_gcr import extract_best_gcr_nibble_stream, score_gcr_alignment
from ..plugins import registry

SECTOR_SIZE_TO_CODE = {128: 0, 256: 1, 512: 2, 1024: 3, 2048: 4, 4096: 5}


def decoder_for(encoding: str):
    load_builtin_decoders()
    if encoding == "mfm":
        return mfm_decoder
    plugin = registry.encoding.get(encoding)
    if plugin:
        return plugin.entry
    raise ValueError(f"Unknown encoding '{encoding}'")


def _best_gcr(bitstreams: list[Bitstream], track: int, head: int) -> Optional[TrackNibbles]:
    best = None
    score = (-1, -1.0, 0)
    for stream in bitstreams:
        data = extract_best_gcr_nibble_stream(stream)
        valid, _ = score_gcr_alignment(stream.bits)
        candidate_score = (valid, stream.metrics.confidence or 0.0, len(data))
        if candidate_score > score:
            best = TrackNibbles(track, head, data, ",".join(map(str, stream.source_revs)) or "rev0", stream.metrics.confidence or 0.0)
            score = candidate_score
    return best


def decode_tracks(path: Path, layout_id: Optional[str], *, encoding: Optional[str] = None, capture_nibbles: bool = False):
    scp = parse_scp(path)
    layout = ensure_layout_loaded(layout_id) if layout_id else None
    selected = layout.encoding if layout else (encoding or "mfm")
    decoder = decoder_for(selected)
    tracks: list[TrackSectors] = []
    nibbles: list[TrackNibbles] = []
    for raw in scp.tracks:
        if layout and (raw.track >= layout.tracks or raw.side >= layout.sides):
            continue
        revolutions = [rev for rev in raw.revolutions if getattr(rev, "interval_ns", None)]
        if not revolutions:
            continue
        expected = None
        if layout:
            try:
                expected = layout.expected_sectors_for_track(raw.track, raw.side)
            except Exception:
                expected = layout.sectors_per_track
        if selected == "gcr" and hasattr(decoder, "set_track"):
            decoder.set_track(raw.track)
        primary = decoder.decode_revolution(revolutions[0])
        tracks.append(build_track_sectors_from_revolutions(
            revolutions, decoder, cylinder=raw.track, head=raw.side,
            expected_sectors=expected, encoding=selected,
            timebase_ns=scp.timebase_ns if selected == "gcr" else None,
        ))
        if capture_nibbles and selected == "gcr":
            streams = [primary]
            for rev in revolutions[1:]:
                decoder.set_track(raw.track)
                streams.append(decoder.decode_revolution(rev))
            candidate = _best_gcr(streams, raw.track, raw.side)
            if candidate:
                nibbles.append(candidate)
    return (tracks, nibbles) if capture_nibbles and selected == "gcr" else tracks


def sectors_from_blob(layout: LayoutDescriptor, data: bytes, *, allow_pad: bool = False, allow_prefix: bool = False):
    if layout.sector_size <= 0 or layout.sector_size not in SECTOR_SIZE_TO_CODE:
        return None
    counts = list(layout.track_sectors) if layout.track_sectors else [layout.sectors_per_track] * layout.tracks
    counts += [layout.sectors_per_track] * max(0, layout.tracks - len(counts))
    expected = sum(counts) * layout.sides * layout.sector_size
    if len(data) != expected:
        if allow_prefix and layout.sides == 1:
            total = 0
            prefix = 0
            for count in counts:
                total += count * layout.sector_size
                if total == len(data):
                    prefix = prefix + 1
                    counts = counts[:prefix]
                    expected = len(data)
                    break
                prefix += 1
        if len(data) < expected and allow_pad and expected and len(data) / expected >= 0.4:
            data = data.ljust(expected, b"\0")
        elif len(data) != expected:
            return None
    order = ((c, h) for c in range(len(counts)) for h in range(layout.sides))
    if layout.layout_id == "commodore_gcr_1571_341k":
        order = ((c, h) for h in range(layout.sides) for c in range(len(counts)))
    tracks = []
    offset = 0
    base = int(layout.id_rules.get("sector_number_base", 1))
    for cylinder, head in order:
        count = counts[cylinder]
        size = layout.sector_size
        sizes = list(layout.sector_sizes) if layout.sector_sizes else [size] * count
        sectors = []
        for index, sector_size in enumerate(sizes):
            chunk = data[offset:offset + sector_size]
            if len(chunk) != sector_size or sector_size not in SECTOR_SIZE_TO_CODE:
                return None
            sectors.append(Sector(cylinder, head, base + index, SECTOR_SIZE_TO_CODE[sector_size], chunk, True, 1.0, False))
            offset += sector_size
        tracks.append(TrackSectors(cylinder, head, sectors))
    return tracks


def image_from_tracks(tracks, geometry, layout=None):
    from ..filesystems import TrackSectorImage
    image = TrackSectorImage(tracks, bytes_per_sector=getattr(geometry, "sector_size", None))
    ids = [sector.sector_id for track in tracks for sector in track.sectors]
    base = min(ids) if ids else 1
    base = int(layout.id_rules.get("sector_number_base", base)) if layout else base
    image.set_geometry(layout.sectors_per_track if layout else (geometry.spt or geometry.tracks), geometry.heads, base)
    if layout:
        image.layout = layout
    return image
