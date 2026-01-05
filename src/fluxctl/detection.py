"""Helpers for detecting encodings and layouts from SCP images."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from .decoding import Decoder
from .exceptions import FluxDecodeError
from .models import Bitstream, LayoutDescriptor, SCPImage
from .plugins import registry
from .sector.reconstruct import build_track_sectors
from .sector.models import TrackSectors
from .filesystems import TrackSectorImage
from .filesystems.cpm import CPMFilesystem


@dataclass(slots=True)
class EncodingCandidate:
    encoding: str
    confidence: float
    evidence: list[str]


@dataclass(slots=True)
class LayoutCandidate:
    layout: LayoutDescriptor
    score: float
    evidence: list[str]


def infer_track_step(track_ids: Iterable[int]) -> int:
    """Infer the logical track step from observed track IDs."""

    ids = sorted(set(track_ids))
    if len(ids) < 2:
        return 1
    deltas = [b - a for a, b in zip(ids, ids[1:]) if b - a > 0]
    return min(deltas) if deltas else 1


def logical_track_count(track_ids: Iterable[int], step: int) -> int:
    ids = sorted(set(track_ids))
    if not ids:
        return 0
    return ids[-1] // max(step, 1) + 1


def _decode_once(decoder: Decoder, image: SCPImage) -> Optional[Bitstream]:
    for track in image.tracks:
        if not track.revolutions:
            continue
        try:
            if getattr(decoder, "encoding", None) == "gcr" and hasattr(decoder, "set_track"):
                decoder.set_track(track.track)
            return decoder.decode_revolution(track.revolutions[0])
        except FluxDecodeError:
            continue
    return None


def _average_confidence(decoder: Decoder, image: SCPImage, sample_tracks: int = 4) -> Optional[float]:
    """Average decoder confidence across a handful of tracks."""

    confidences: list[float] = []
    for track in image.tracks:
        if not track.revolutions:
            continue
        try:
            if getattr(decoder, "encoding", None) == "gcr" and hasattr(decoder, "set_track"):
                decoder.set_track(track.track)
            bitstream = decoder.decode_revolution(track.revolutions[0])
        except FluxDecodeError:
            continue
        confidences.append(bitstream.metrics.confidence or 0.0)
        if len(confidences) >= sample_tracks:
            break
    if not confidences:
        return None
    return sum(confidences) / len(confidences)


def detect_encoding(image: SCPImage, path: Optional[Path] = None) -> Optional[EncodingCandidate]:
    """Return the best matching decoder based on layout scoring and confidence."""

    layout_candidate = detect_layout_any(image, path or Path(""))
    if layout_candidate:
        return EncodingCandidate(
            encoding=layout_candidate.layout.encoding,
            confidence=layout_candidate.score,
            evidence=layout_candidate.evidence + ["layout_selected=1"],
        )

    best: Optional[EncodingCandidate] = None
    for key, plugin in registry.encoding.items():
        avg_confidence = _average_confidence(plugin.entry, image)
        if avg_confidence is None:
            continue
        candidate = EncodingCandidate(
            encoding=key,
            confidence=avg_confidence,
            evidence=[f"decoder={plugin.name}", f"confidence={avg_confidence:.2f}"],
        )
        if best is None or candidate.confidence > best.confidence:
            best = candidate
    return best


def _estimate_geometry(image: SCPImage, decoder: Decoder, sample_tracks: int = 6) -> dict:
    """Estimate geometry hints from a small set of reconstructed tracks."""

    sector_counts: list[int] = []
    sector_sizes: list[int] = []
    track_samples = 0
    tracks_with_sectors = 0
    for track_flux in image.tracks:
        if not track_flux.revolutions:
            continue
        track_samples += 1
        try:
            track_sectors = build_track_sectors(
                track_flux.revolutions[0],
                decoder,
                cylinder=track_flux.track,
                head=track_flux.side,
            )
        except FluxDecodeError:
            continue
        if not track_sectors.sectors:
            continue
        tracks_with_sectors += 1
        sector_ids = [sector.sector_id for sector in track_sectors.sectors if sector.sector_id is not None]
        if sector_ids:
            min_id = min(sector_ids)
            max_id = max(sector_ids)
            if min_id == 0:
                sector_counts.append(max_id + 1)
            else:
                sector_counts.append(max_id)
        else:
            sector_counts.append(len(track_sectors.sectors))
        sector_sizes.extend([sector.size for sector in track_sectors.sectors if sector.data])
        if len(sector_counts) >= sample_tracks:
            break

    geometry: dict = {}
    if sector_counts:
        geometry["sectors_per_track"] = Counter(sector_counts).most_common(1)[0][0]
    if sector_sizes:
        geometry["sector_size"] = Counter(sector_sizes).most_common(1)[0][0]
    if track_samples:
        geometry["track_samples"] = track_samples
        geometry["tracks_with_sectors"] = tracks_with_sectors
    return geometry


def _layout_geometry_score(
    desc: LayoutDescriptor, image: SCPImage, logical_tracks: int, heads_present: set[int]
) -> float:
    """Score how well a layout matches the observed track/head counts."""

    expected_entries = desc.tracks * desc.sides
    entries_diff = abs(expected_entries - len(image.tracks))
    entries_score = 1.0 - (entries_diff / max(expected_entries, len(image.tracks), 1))
    cyl_diff = abs(desc.tracks - logical_tracks)
    cyl_score = 1.0 - (cyl_diff / max(desc.tracks, logical_tracks, 1))
    heads_score = 1.0 if len(heads_present) == desc.sides else 0.7 if len(heads_present) == 1 else 0.4
    score = 0.6 * entries_score + 0.4 * cyl_score
    return max(0.0, min(1.0, score + 0.05 * heads_score))


def _sectors_match(desc: LayoutDescriptor, observed: int) -> bool:
    if desc.track_sectors:
        return observed in desc.track_sectors
    return observed == desc.sectors_per_track


def _estimate_bitstream_length(image: SCPImage, decoder: Decoder, sample_tracks: int = 4) -> Optional[float]:
    """Estimate average bitstream length from decoded revolutions."""

    lengths: list[int] = []
    for track_flux in image.tracks:
        if not track_flux.revolutions:
            continue
        try:
            bitstream = decoder.decode_revolution(track_flux.revolutions[0])
        except FluxDecodeError:
            continue
        if bitstream.bits:
            lengths.append(len(bitstream.bits))
        if len(lengths) >= sample_tracks:
            break
    if not lengths:
        return None
    return sum(lengths) / len(lengths)


def _estimate_flux_median(image: SCPImage, sample_tracks: int = 3) -> Optional[float]:
    """Estimate median flux interval from raw revolutions."""

    medians: list[float] = []
    for track_flux in image.tracks:
        if not track_flux.revolutions:
            continue
        intervals = list(track_flux.revolutions[0].interval_ns)
        if not intervals:
            continue
        intervals.sort()
        medians.append(float(intervals[len(intervals) // 2]))
        if len(medians) >= sample_tracks:
            break
    if not medians:
        return None
    medians.sort()
    return medians[len(medians) // 2]


def detect_layout(image: SCPImage, encoding: str, path: Path) -> Optional[LayoutCandidate]:
    """Pick the most likely layout for an image and encoding."""

    layouts = [desc for desc in registry.layout.values() if desc.encoding == encoding]
    if not layouts:
        return None

    plugin = registry.encoding.get(encoding)
    if plugin is None:
        return None

    track_ids = [track.track for track in image.tracks]
    heads_present = {track.side for track in image.tracks}
    step = infer_track_step(track_ids)
    logical_tracks = logical_track_count(track_ids, step)
    geometry = _estimate_geometry(image, plugin.entry)
    bitstream_len = _estimate_bitstream_length(image, plugin.entry)
    flux_median = _estimate_flux_median(image)

    best: Optional[LayoutCandidate] = None
    for desc in layouts:
        expected_entries = desc.tracks * desc.sides
        entries_diff = abs(expected_entries - len(image.tracks))
        score = 1.0 - (entries_diff / max(expected_entries, len(image.tracks), 1))
        expected_cylinders = desc.tracks
        cyl_diff = abs(expected_cylinders - logical_tracks)
        score = (score * 0.6) + (1.0 - (cyl_diff / max(expected_cylinders, logical_tracks, 1))) * 0.4
        evidence = [
            f"expected_entries={expected_entries}",
            f"observed_entries={len(image.tracks)}",
            f"expected_cylinders={expected_cylinders}",
            f"logical_cylinders={logical_tracks}",
        ]

        observed_heads = len(heads_present)
        if observed_heads == desc.sides:
            score += 0.2
            evidence.append("heads_match=1")
        elif observed_heads > 1:
            score -= 0.2
            evidence.append(f"heads_mismatch={observed_heads}")
        else:
            evidence.append("single_head_capture=1")

        if geometry.get("sectors_per_track") is not None:
            observed_sectors = geometry["sectors_per_track"]
            if _sectors_match(desc, observed_sectors):
                score += 0.2
                evidence.append("sectors_per_track_match=1")
            else:
                score -= 0.2
                evidence.append(
                    f"sectors_per_track_mismatch={observed_sectors}"
                )

        if geometry.get("sector_size") is not None:
            observed_size = geometry["sector_size"]
            if observed_size == desc.sector_size:
                score += 0.1
                evidence.append("sector_size_match=1")
            else:
                score -= 0.1
                evidence.append(f"sector_size_mismatch={observed_size}")

        if bitstream_len is not None and encoding == "mfm":
            if logical_tracks <= 77 and desc.tracks >= 80:
                candidate = LayoutCandidate(layout=desc, score=score, evidence=evidence)
                if best is None or candidate.score > best.score:
                    best = candidate
                continue
            if desc.sector_size <= 128 and desc.sectors_per_track >= 20 and logical_tracks >= 79:
                candidate = LayoutCandidate(layout=desc, score=score, evidence=evidence)
                if best is None or candidate.score > best.score:
                    best = candidate
                continue
            rate_factor = 1.0
            if desc.sectors_per_track >= 15 and not desc.layout_id.startswith("amiga_"):
                rate_factor = 0.5
            expected_bits = int(desc.sectors_per_track * desc.sector_size * 16 * rate_factor)
            diff = abs(expected_bits - bitstream_len)
            score += 0.15 * (1.0 - (diff / max(expected_bits, bitstream_len, 1)))
            evidence.append(f"bitstream_len={bitstream_len:.0f}")
            evidence.append(f"expected_bits={expected_bits}")
            if rate_factor != 1.0:
                evidence.append(f"rate_factor={rate_factor}")
            apply_bitstream_bonus = geometry.get("sectors_per_track") is None and not desc.layout_id.startswith("amiga_")
            if apply_bitstream_bonus:
                if bitstream_len >= 100_000:
                    if desc.sectors_per_track >= 18:
                        score += 0.2
                        evidence.append("bitstream_spt_bonus=high")
                    elif desc.sectors_per_track <= 10:
                        score -= 0.2
                        evidence.append("bitstream_spt_penalty=high")
                elif bitstream_len <= 50_000:
                    if desc.sectors_per_track <= 10:
                        score += 0.15
                        evidence.append("bitstream_spt_bonus=low")
                    elif desc.sectors_per_track >= 15:
                        score -= 0.15
                        evidence.append("bitstream_spt_penalty=low")
                if 60_000 <= bitstream_len <= 80_000:
                    if desc.tracks >= 70 and desc.sectors_per_track in {9, 15}:
                        score += 0.15
                        evidence.append("bitstream_spt_bonus=mid")
                    elif desc.sectors_per_track >= 18:
                        score -= 0.15
                        evidence.append("bitstream_spt_penalty=mid")
                if bitstream_len <= 50_000 and desc.sectors_per_track == 9:
                    score += 0.1
                    evidence.append("bitstream_spt_bonus_9=1")
            if desc.layout_id.startswith("amiga_") and bitstream_len >= 90_000 and logical_tracks >= 79:
                score += 0.2
                evidence.append("amiga_bitstream_bonus=1")
            if (
                desc.sector_size == 128
                and desc.sectors_per_track == 26
                and desc.tracks <= 77
                and logical_tracks <= 77
            ):
                score += 0.25
                evidence.append("mfm_8inch_128_bonus=1")
            # Flux median can be misleading across controllers; omit it for MFM scoring.

        if "cpm" in desc.layout_id and desc.encoding == "mfm":
            if logical_tracks > desc.tracks and (logical_tracks - desc.tracks) <= 5:
                score += 0.15
                evidence.append("cpm_track_bonus=1")
            if _probe_cpm_filesystem(image, plugin.entry, desc):
                score += 0.25
                evidence.append("cpm_fs_probe=1")

        candidate = LayoutCandidate(layout=desc, score=score, evidence=evidence)
        if best is None or candidate.score > best.score:
            best = candidate

    return best


def _probe_cpm_filesystem(image: SCPImage, decoder: Decoder, desc: LayoutDescriptor) -> bool:
    tracks: list[TrackSectors] = []
    for track_flux in image.tracks[: min(6, len(image.tracks))]:
        if not track_flux.revolutions:
            continue
        try:
            track_sectors = build_track_sectors(
                track_flux.revolutions[0],
                decoder,
                cylinder=track_flux.track,
                head=track_flux.side,
                expected_sectors=desc.sectors_per_track,
                encoding=desc.encoding,
            )
        except FluxDecodeError:
            continue
        if track_sectors.sectors:
            tracks.append(track_sectors)
        if len(tracks) >= 3:
            break
    if not tracks:
        return False
    try:
        image_view = TrackSectorImage(tracks, bytes_per_sector=desc.sector_size)
        image_view.set_geometry(desc.sectors_per_track, desc.sides)
    except Exception:
        return False
    return CPMFilesystem().probe(image_view)


def detect_layout_any(image: SCPImage, path: Path) -> Optional[LayoutCandidate]:
    """Pick the most likely layout across all encodings."""

    if not registry.layout:
        return None

    track_ids = [track.track for track in image.tracks]
    heads_present = {track.side for track in image.tracks}
    step = infer_track_step(track_ids)
    logical_tracks = logical_track_count(track_ids, step)

    mfm_decoder = registry.encoding.get("mfm").entry if "mfm" in registry.encoding else None
    fm_decoder = registry.encoding.get("fm").entry if "fm" in registry.encoding else None
    gcr_decoder = registry.encoding.get("gcr").entry if "gcr" in registry.encoding else None
    mfm_bits = _estimate_bitstream_length(image, mfm_decoder) if mfm_decoder else None
    mfm_conf = _average_confidence(mfm_decoder, image) if mfm_decoder else None
    gcr_conf = _average_confidence(gcr_decoder, image) if gcr_decoder else None
    flux_median = _estimate_flux_median(image)
    mfm_geometry = _estimate_geometry(image, mfm_decoder) if mfm_decoder else {}
    fm_bits = _estimate_bitstream_length(image, fm_decoder) if fm_decoder else None
    fm_conf = _average_confidence(fm_decoder, image) if fm_decoder else None
    fm_geometry = _estimate_geometry(image, fm_decoder) if fm_decoder else {}
    gcr_geometry = _estimate_geometry(image, gcr_decoder) if gcr_decoder else {}
    fm_ratio = None
    if fm_bits is not None and mfm_bits is not None and mfm_bits > 0:
        fm_ratio = fm_bits / mfm_bits

    best: Optional[LayoutCandidate] = None
    for desc in registry.layout.values():
        if desc.encoding not in registry.encoding:
            continue
        score = _layout_geometry_score(desc, image, logical_tracks, heads_present)
        evidence = [
            f"expected_entries={desc.tracks * desc.sides}",
            f"observed_entries={len(image.tracks)}",
            f"expected_cylinders={desc.tracks}",
            f"logical_cylinders={logical_tracks}",
        ]

        if desc.encoding == "mfm":
            geometry = mfm_geometry
            if geometry.get("track_samples") and geometry.get("tracks_with_sectors") == 0:
                score -= 0.4
                evidence.append("mfm_no_sectors_penalty=1")
            if geometry.get("sectors_per_track") is not None:
                observed_sectors = geometry["sectors_per_track"]
                if _sectors_match(desc, observed_sectors):
                    score += 0.2
                    evidence.append("sectors_per_track_match=1")
                else:
                    score -= 0.2
                    evidence.append(f"sectors_per_track_mismatch={observed_sectors}")
            if geometry.get("sector_size") is not None:
                observed_size = geometry["sector_size"]
                if observed_size == desc.sector_size:
                    score += 0.1
                    evidence.append("sector_size_match=1")
                else:
                    score -= 0.1
                    evidence.append(f"sector_size_mismatch={observed_size}")
            if (
                "cpm" in desc.layout_id
                and logical_tracks > desc.tracks
                and (logical_tracks - desc.tracks) <= 5
                and (gcr_conf is None or gcr_conf < 0.9)
            ):
                score += 0.3
                evidence.append("cpm_track_bonus=1")
        if desc.encoding == "gcr":
            geometry = gcr_geometry
            if geometry.get("track_samples") and geometry.get("tracks_with_sectors") == 0:
                score -= 0.4
                evidence.append("gcr_no_sectors_penalty=1")
        if desc.encoding == "mfm" and mfm_bits is not None:
            if logical_tracks <= 77 and desc.tracks >= 80:
                candidate = LayoutCandidate(layout=desc, score=score, evidence=evidence)
                if best is None or candidate.score > best.score:
                    best = candidate
                continue
            if desc.sector_size <= 128 and desc.sectors_per_track >= 20 and logical_tracks >= 79:
                candidate = LayoutCandidate(layout=desc, score=score, evidence=evidence)
                if best is None or candidate.score > best.score:
                    best = candidate
                continue
            rate_factor = 1.0
            if desc.sectors_per_track >= 15 and not desc.layout_id.startswith("amiga_"):
                rate_factor = 0.5
            expected_bits = int(desc.sectors_per_track * desc.sector_size * 16 * rate_factor)
            diff = abs(expected_bits - mfm_bits)
            score += 0.15 * (1.0 - (diff / max(expected_bits, mfm_bits, 1)))
            evidence.append(f"bitstream_len={mfm_bits:.0f}")
            evidence.append(f"expected_bits={expected_bits}")
            apply_bitstream_bonus = mfm_geometry.get("sectors_per_track") is None and not desc.layout_id.startswith("amiga_")
            if apply_bitstream_bonus:
                if 60_000 <= mfm_bits <= 80_000:
                    if desc.tracks >= 70 and desc.sectors_per_track in {9, 15}:
                        score += 0.15
                        evidence.append("bitstream_spt_bonus=mid")
                    elif desc.sectors_per_track >= 18:
                        score -= 0.15
                        evidence.append("bitstream_spt_penalty=mid")
                if mfm_bits <= 50_000 and desc.sectors_per_track == 9:
                    score += 0.1
                    evidence.append("bitstream_spt_bonus_9=1")
                if desc.layout_id.startswith("amiga_") and mfm_bits >= 90_000 and logical_tracks >= 79:
                    score += 0.2
                    evidence.append("amiga_bitstream_bonus=1")
                if (
                    desc.sector_size == 128
                    and desc.sectors_per_track == 26
                    and desc.tracks <= 77
                    and logical_tracks <= 77
                ):
                    score += 0.25
                    evidence.append("mfm_8inch_128_bonus=1")
            # Flux median can be misleading across controllers; omit it for MFM scoring.
        if desc.encoding == "gcr":
            geometry = gcr_geometry if (gcr_conf is None or gcr_conf >= 0.6) else {}
            if geometry.get("sectors_per_track") is not None:
                observed_sectors = geometry["sectors_per_track"]
                if _sectors_match(desc, observed_sectors):
                    score += 0.2
                    evidence.append("sectors_per_track_match=1")
                else:
                    score -= 0.2
                    evidence.append(f"sectors_per_track_mismatch={observed_sectors}")
            if geometry.get("sector_size") is not None:
                observed_size = geometry["sector_size"]
                if observed_size == desc.sector_size:
                    score += 0.1
                    evidence.append("sector_size_match=1")
                else:
                    score -= 0.1
                    evidence.append(f"sector_size_mismatch={observed_size}")
            if geometry.get("sectors_per_track") is not None and not mfm_geometry.get("sectors_per_track"):
                score += 0.2
                evidence.append("gcr_geometry_bonus=1")
            track_samples = geometry.get("track_samples")
            tracks_with_sectors = geometry.get("tracks_with_sectors")
            if track_samples:
                coverage = tracks_with_sectors / track_samples
                if coverage < 0.5 and (gcr_conf is None or gcr_conf < 0.9):
                    if gcr_conf is not None or mfm_conf is not None:
                        score -= 0.4
                        evidence.append("gcr_coverage_penalty=1")
        if desc.encoding == "gcr" and gcr_conf is not None:
            track_samples = gcr_geometry.get("track_samples")
            tracks_with_sectors = gcr_geometry.get("tracks_with_sectors")
            coverage = (tracks_with_sectors / track_samples) if track_samples else 1.0
            coverage_factor = coverage if coverage < 0.5 and gcr_conf < 0.9 else 1.0
            score += 0.4 * gcr_conf * coverage_factor
            if gcr_conf < 0.6 and coverage < 0.9:
                score -= 0.2
                evidence.append("gcr_conf_penalty=1")
            if mfm_conf is not None and 0.42 <= gcr_conf < 0.6 and mfm_conf >= 0.7:
                score -= 0.3
                evidence.append("gcr_vs_mfm_penalty=1")
            if coverage_factor != 1.0:
                evidence.append(f"gcr_coverage_factor={coverage_factor:.2f}")
            evidence.append(f"gcr_conf={gcr_conf:.2f}")

        if desc.encoding == "fm" and fm_bits is not None:
            if logical_tracks > 77:
                continue
            rate_factor = 0.5 if desc.sectors_per_track >= 15 else 1.0
            expected_bits = int(desc.sectors_per_track * desc.sector_size * 16 * rate_factor)
            diff = abs(expected_bits - fm_bits)
            score += 0.15 * (1.0 - (diff / max(expected_bits, fm_bits, 1)))
            evidence.append(f"bitstream_len={fm_bits:.0f}")
            evidence.append(f"expected_bits={expected_bits}")
            if fm_ratio is not None:
                if fm_ratio < 0.4:
                    score += 0.2
                    evidence.append("fm_ratio_bonus=low")
                elif fm_ratio > 0.42:
                    score -= 0.2
                    evidence.append("fm_ratio_penalty=high")
                if (
                    fm_ratio > 0.42
                    and mfm_conf is not None
                    and mfm_conf >= 0.9
                    and fm_conf is not None
                    and fm_conf < 0.3
                ):
                    score -= 0.3
                    evidence.append("fm_vs_mfm_penalty=1")
            if fm_conf is not None:
                score += 0.2 * fm_conf
                evidence.append(f"fm_conf={fm_conf:.2f}")
            if fm_geometry.get("sectors_per_track") is not None:
                observed_sectors = fm_geometry["sectors_per_track"]
                if _sectors_match(desc, observed_sectors):
                    score += 0.2
                    evidence.append("sectors_per_track_match=1")
                else:
                    score -= 0.2
                    evidence.append(f"sectors_per_track_mismatch={observed_sectors}")
            if fm_geometry.get("sector_size") is not None:
                observed_size = fm_geometry["sector_size"]
                if observed_size == desc.sector_size:
                    score += 0.1
                    evidence.append("sector_size_match=1")
                else:
                    score -= 0.1
                    evidence.append(f"sector_size_mismatch={observed_size}")
            if flux_median is not None:
                if flux_median >= 500 and desc.sector_size <= 256:
                    score += 0.05
                    evidence.append("flux_rate_bonus=low")

        candidate = LayoutCandidate(layout=desc, score=score, evidence=evidence)
        if best is None or candidate.score > best.score:
            best = candidate

    return best


__all__ = [
    "EncodingCandidate",
    "LayoutCandidate",
    "detect_encoding",
    "detect_layout",
    "infer_track_step",
    "logical_track_count",
]
