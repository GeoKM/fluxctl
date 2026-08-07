"""Helpers for detecting encodings and layouts from SCP images."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from .decoding import Decoder
from .exceptions import FluxDecodeError
from .geohints import LayoutHint
from .models import Bitstream, LayoutDescriptor, SCPImage
from .plugins import registry
from .sector.reconstruct import build_track_sectors_from_revolutions
from .sector.models import TrackSectors
from .filesystems import TrackSectorImage
from .filesystems.cpm import CPMFilesystem, cpm_directory_score_for_layout, cpm_disk_parameters_for_layout


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


def _tracks_with_flux(image: SCPImage):
    return [
        track
        for track in image.tracks
        if any(getattr(rev, "interval_ns", None) for rev in track.revolutions)
    ]


def _geometry_tracks_for_encoding(image: SCPImage, encoding: str | None):
    active_tracks = _tracks_with_flux(image)
    if encoding == "fm" and active_tracks:
        return active_tracks
    return image.tracks


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


def detect_encoding(
    image: SCPImage, path: Optional[Path] = None, hint: LayoutHint | None = None
) -> Optional[EncodingCandidate]:
    """Return the best matching decoder based on layout scoring and confidence."""

    layout_candidate = detect_layout_any(image, path or Path(""), hint=hint)
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
    min_sector_ids: list[int] = []
    max_sector_ids: list[int] = []
    track_samples = 0
    tracks_with_sectors = 0
    for track_flux in _tracks_with_flux(image):
        if not track_flux.revolutions:
            continue
        track_samples += 1
        try:
            if getattr(decoder, "encoding", None) == "gcr" and hasattr(decoder, "set_track"):
                decoder.set_track(track_flux.track)
            track_sectors = build_track_sectors_from_revolutions(
                track_flux.revolutions,
                decoder,
                cylinder=track_flux.track,
                head=track_flux.side,
                encoding=getattr(decoder, "encoding", None),
                timebase_ns=image.timebase_ns,
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
            min_sector_ids.append(min_id)
            max_sector_ids.append(max_id)
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
    if min_sector_ids:
        geometry["min_sector_id"] = Counter(min_sector_ids).most_common(1)[0][0]
    if max_sector_ids:
        geometry["max_sector_id"] = Counter(max_sector_ids).most_common(1)[0][0]
    if track_samples:
        geometry["track_samples"] = track_samples
        geometry["tracks_with_sectors"] = tracks_with_sectors
    return geometry


def _layout_geometry_score(
    desc: LayoutDescriptor, observed_entries: int, logical_tracks: int, heads_present: set[int]
) -> float:
    """Score how well a layout matches the observed track/head counts."""

    expected_entries = desc.tracks * desc.sides
    entries_diff = abs(expected_entries - observed_entries)
    entries_score = 1.0 - (entries_diff / max(expected_entries, observed_entries, 1))
    cyl_diff = abs(desc.tracks - logical_tracks)
    cyl_score = 1.0 - (cyl_diff / max(desc.tracks, logical_tracks, 1))
    heads_score = 1.0 if len(heads_present) == desc.sides else 0.7 if len(heads_present) == 1 else 0.4
    score = 0.6 * entries_score + 0.4 * cyl_score
    return max(0.0, min(1.0, score + 0.05 * heads_score))


def _apply_layout_hint(desc: LayoutDescriptor, hint: LayoutHint | None, score: float, evidence: list[str]) -> float:
    """Adjust matching score based on externally provided geometry hints."""

    if not hint:
        return score
    if hint.tracks is not None:
        if desc.tracks == hint.tracks:
            score += 0.25
            evidence.append(f"hint_tracks_match={hint.tracks}")
        else:
            score -= 0.05
            evidence.append(f"hint_tracks_mismatch={hint.tracks}")
    if hint.sides is not None:
        if desc.sides == hint.sides:
            score += 0.15
            evidence.append(f"hint_sides_match={hint.sides}")
        else:
            score -= 0.05
            evidence.append(f"hint_sides_mismatch={hint.sides}")
    if hint.interface:
        evidence.append(f"hint_interface={hint.interface}")
    if hint.loader:
        evidence.append(f"hint_loader={hint.loader}")
    if hint.total_size is not None:
        evidence.append(f"hint_total_size={hint.total_size}")
    if hint.total_sectors is not None:
        evidence.append(f"hint_total_sectors={hint.total_sectors}")
    for key, value in hint.metadata.items():
        evidence.append(f"{key}={value}")
    return score


def _sectors_match(desc: LayoutDescriptor, observed: int) -> bool:
    if desc.track_sectors:
        return observed in desc.track_sectors
    return observed == desc.sectors_per_track


def _sector_base_matches(desc: LayoutDescriptor, geometry: dict) -> bool:
    observed = geometry.get("min_sector_id")
    if observed is None:
        return True
    return int(desc.id_rules.get("sector_number_base", 1)) == observed


def _apply_tandy_mfm_bonus(
    desc: LayoutDescriptor,
    geometry: dict,
    logical_tracks: int,
    heads_present: set[int],
    evidence: list[str],
) -> float:
    if not desc.layout_id.startswith("tandy_"):
        return 0.0
    if logical_tracks != 40 or len(heads_present) != 1:
        return 0.0
    observed_sectors = geometry.get("sectors_per_track")
    observed_size = geometry.get("sector_size")
    if observed_sectors is None or observed_size is None:
        return 0.0
    if not _sectors_match(desc, observed_sectors) or observed_size != desc.sector_size:
        return 0.0
    if not _sector_base_matches(desc, geometry):
        evidence.append("tandy_sector_base_mismatch=1")
        return -0.2
    evidence.append("tandy_mfm_geometry_bonus=1")
    return 0.35


def _apply_mfm_raw_density_bonus(
    desc: LayoutDescriptor,
    geometry: dict,
    logical_tracks: int,
    heads_present: set[int],
    bitstream_len: float,
    evidence: list[str],
) -> float:
    """Tie-break raw 3.5-inch HD captures when no sector headers decode."""

    if geometry.get("sectors_per_track") is not None:
        return 0.0
    if not (79 <= logical_tracks <= 82 and len(heads_present) == 2):
        return 0.0
    if desc.layout_id == "amiga_mfm_880k" and 90_000 <= bitstream_len <= 115_000:
        evidence.append("amiga_raw_dd_bonus=1")
        return 0.45
    if bitstream_len < 120_000:
        return 0.0
    if desc.layout_id == "ibm_mfm_1440k":
        evidence.append("mfm_raw_pc_hd_bonus=1")
        return 0.25
    if desc.sector_size < 512 and desc.sectors_per_track >= 20:
        evidence.append("mfm_raw_small_sector_penalty=1")
        return -0.08
    return 0.0


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


def detect_layout(
    image: SCPImage, encoding: str, path: Path, hint: LayoutHint | None = None
) -> Optional[LayoutCandidate]:
    """Pick the most likely layout for an image and encoding."""

    layouts = [desc for desc in registry.layout.values() if desc.encoding == encoding]
    if not layouts:
        return None

    plugin = registry.encoding.get(encoding)
    if plugin is None:
        return None

    geometry_tracks = _geometry_tracks_for_encoding(image, encoding)
    track_ids = [track.track for track in geometry_tracks]
    heads_present = {track.side for track in geometry_tracks}
    step = infer_track_step(track_ids)
    logical_tracks = logical_track_count(track_ids, step)
    geometry = _estimate_geometry(image, plugin.entry)
    bitstream_len = _estimate_bitstream_length(image, plugin.entry)
    flux_median = _estimate_flux_median(image)
    decoder_conf = _average_confidence(plugin.entry, image)

    best: Optional[LayoutCandidate] = None
    for desc in layouts:
        expected_entries = desc.tracks * desc.sides
        observed_entries = len(geometry_tracks)
        entries_diff = abs(expected_entries - observed_entries)
        score = 1.0 - (entries_diff / max(expected_entries, observed_entries, 1))
        expected_cylinders = desc.tracks
        cyl_diff = abs(expected_cylinders - logical_tracks)
        score = (score * 0.6) + (1.0 - (cyl_diff / max(expected_cylinders, logical_tracks, 1))) * 0.4
        evidence = [
            f"expected_entries={expected_entries}",
            f"observed_entries={observed_entries}",
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

        if desc.encoding == "mfm":
            score += _apply_tandy_mfm_bonus(desc, geometry, logical_tracks, heads_present, evidence)

        if encoding == "gcr":
            if geometry.get("track_samples") and geometry.get("tracks_with_sectors") == 0:
                score -= 0.4
                evidence.append("gcr_no_sectors_penalty=1")
            if logical_tracks and logical_tracks <= 40 and desc.sides == 1:
                score += 0.2
                evidence.append("gcr_low_density_bonus=1")
            if desc.layout_id.startswith("commodore_gcr_1541") and 30 <= logical_tracks <= 42:
                score += 0.25
                evidence.append("commodore_1541_bonus=1")
            if desc.layout_id == "commodore_gcr_1541_cpm_170k":
                score += 0.2
                evidence.append("commodore_cpm_bonus=1")
            if desc.layout_id == "apple2_gcr_nofs_140_140k" and geometry.get("track_samples") and geometry.get("tracks_with_sectors") == 0:
                score -= 0.2
                evidence.append("apple2_no_sector_penalty=1")
            if desc.layout_id.startswith("commodore_gcr_1541") and geometry.get("track_samples") and geometry.get("tracks_with_sectors") == 0:
                score += 0.2
                evidence.append("commodore_no_sector_bonus=1")
            if decoder_conf is not None:
                score += 0.2 * decoder_conf
                evidence.append(f"gcr_conf={decoder_conf:.2f}")

        if bitstream_len is not None and encoding == "mfm":
            if logical_tracks <= 77 and desc.tracks >= 80:
                adjusted_score = _apply_layout_hint(desc, hint, score, evidence)
                candidate = LayoutCandidate(layout=desc, score=adjusted_score, evidence=evidence)
                if best is None or candidate.score > best.score:
                    best = candidate
                continue
            if desc.sector_size <= 128 and desc.sectors_per_track >= 20 and logical_tracks >= 79:
                adjusted_score = _apply_layout_hint(desc, hint, score, evidence)
                candidate = LayoutCandidate(layout=desc, score=adjusted_score, evidence=evidence)
                if best is None or candidate.score > best.score:
                    best = candidate
                continue
            expected_bits = int(desc.sectors_per_track * desc.sector_size * 16)
            diff = abs(expected_bits - bitstream_len)
            score += 0.15 * (1.0 - (diff / max(expected_bits, bitstream_len, 1)))
            evidence.append(f"bitstream_len={bitstream_len:.0f}")
            evidence.append(f"expected_bits={expected_bits}")
            score += _apply_mfm_raw_density_bonus(
                desc, geometry, logical_tracks, heads_present, bitstream_len, evidence
            )
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

        if ("cpm" in desc.layout_id or _is_modelled_cpm_layout(desc)) and desc.encoding == "mfm":
            if logical_tracks > desc.tracks and (logical_tracks - desc.tracks) <= 5:
                score += 0.15
                evidence.append("cpm_track_bonus=1")
            cpm_layout_score = _cpm_directory_score_for_layout(image, plugin.entry, desc)
            if cpm_layout_score >= 2:
                score += 0.4
                evidence.append(f"cpm_layout_directory_entries={cpm_layout_score}")
            elif _probe_cpm_filesystem(image, plugin.entry, desc):
                score += 0.25
                evidence.append("cpm_fs_probe=1")

        adjusted_score = _apply_layout_hint(desc, hint, score, evidence)
        candidate = LayoutCandidate(layout=desc, score=adjusted_score, evidence=evidence)
        if best is None or candidate.score > best.score:
            best = candidate

    return best


def _probe_cpm_filesystem(image: SCPImage, decoder: Decoder, desc: LayoutDescriptor) -> bool:
    if cpm_disk_parameters_for_layout(desc.layout_id) is not None:
        return _cpm_directory_score_for_layout(image, decoder, desc) >= 2
    return _probe_generic_cpm_filesystem(image, decoder, desc)


def _cpm_directory_score_for_layout(image: SCPImage, decoder: Decoder, desc: LayoutDescriptor) -> int:
    params = cpm_disk_parameters_for_layout(desc.layout_id)
    if params is None:
        return 0
    directory_track = params.reserved_tracks
    tracks: list[TrackSectors] = []
    sector_base = int(desc.id_rules.get("sector_number_base", 1))
    for track_flux in image.tracks:
        if track_flux.track < directory_track:
            continue
        if track_flux.track > directory_track + 1:
            break
        if not track_flux.revolutions:
            continue
        try:
            track_sectors = build_track_sectors_from_revolutions(
                track_flux.revolutions,
                decoder,
                cylinder=track_flux.track,
                head=track_flux.side,
                expected_sectors=desc.sectors_per_track,
                encoding=desc.encoding,
                timebase_ns=image.timebase_ns,
            )
        except FluxDecodeError:
            continue
        if track_sectors.sectors:
            tracks.append(track_sectors)
    if not tracks:
        return 0
    try:
        image_view = TrackSectorImage(tracks, bytes_per_sector=desc.sector_size)
        image_view.layout = desc
        image_view.set_geometry(desc.sectors_per_track, desc.sides, sector_base)
    except Exception:
        return 0
    return cpm_directory_score_for_layout(image_view, desc.layout_id)


def _is_modelled_cpm_layout(desc: LayoutDescriptor) -> bool:
    return cpm_disk_parameters_for_layout(desc.layout_id) is not None


def _probe_generic_cpm_filesystem(image: SCPImage, decoder: Decoder, desc: LayoutDescriptor) -> bool:
    tracks: list[TrackSectors] = []
    for track_flux in image.tracks[: min(6, len(image.tracks))]:
        if not track_flux.revolutions:
            continue
        try:
            track_sectors = build_track_sectors_from_revolutions(
                track_flux.revolutions,
                decoder,
                cylinder=track_flux.track,
                head=track_flux.side,
                expected_sectors=desc.sectors_per_track,
                encoding=desc.encoding,
                timebase_ns=image.timebase_ns,
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


def detect_layout_any(image: SCPImage, path: Path, hint: LayoutHint | None = None) -> Optional[LayoutCandidate]:
    """Pick the most likely layout across all encodings."""

    if not registry.layout:
        return None

    active_tracks = _tracks_with_flux(image)
    default_geometry_tracks = active_tracks or image.tracks
    track_ids = [track.track for track in default_geometry_tracks]
    heads_present = {track.side for track in default_geometry_tracks}
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
        geometry_tracks = _geometry_tracks_for_encoding(image, desc.encoding)
        track_ids = [track.track for track in geometry_tracks]
        heads_present = {track.side for track in geometry_tracks}
        step = infer_track_step(track_ids)
        logical_tracks = logical_track_count(track_ids, step)
        score = _layout_geometry_score(desc, len(geometry_tracks), logical_tracks, heads_present)
        evidence = [
            f"expected_entries={desc.tracks * desc.sides}",
            f"observed_entries={len(geometry_tracks)}",
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
            score += _apply_tandy_mfm_bonus(desc, geometry, logical_tracks, heads_present, evidence)
            if (
                ("cpm" in desc.layout_id or _is_modelled_cpm_layout(desc))
                and logical_tracks > desc.tracks
                and (logical_tracks - desc.tracks) <= 5
                and (gcr_conf is None or gcr_conf < 0.9)
            ):
                score += 0.3
                evidence.append("cpm_track_bonus=1")
            if _is_modelled_cpm_layout(desc) and mfm_decoder is not None:
                cpm_layout_score = _cpm_directory_score_for_layout(image, mfm_decoder, desc)
                if cpm_layout_score >= 2:
                    score += 0.45
                    evidence.append(f"cpm_layout_directory_entries={cpm_layout_score}")
                else:
                    score -= 0.15
                    evidence.append("cpm_layout_directory_miss=1")
        if desc.encoding == "gcr":
            geometry = gcr_geometry
            if geometry.get("track_samples") and geometry.get("tracks_with_sectors") == 0:
                score -= 0.4
                evidence.append("gcr_no_sectors_penalty=1")
        if desc.encoding == "mfm" and mfm_bits is not None:
            if logical_tracks <= 77 and desc.tracks >= 80:
                adjusted_score = _apply_layout_hint(desc, hint, score, evidence)
                candidate = LayoutCandidate(layout=desc, score=adjusted_score, evidence=evidence)
                if best is None or candidate.score > best.score:
                    best = candidate
                continue
            if desc.sector_size <= 128 and desc.sectors_per_track >= 20 and logical_tracks >= 79:
                adjusted_score = _apply_layout_hint(desc, hint, score, evidence)
                candidate = LayoutCandidate(layout=desc, score=adjusted_score, evidence=evidence)
                if best is None or candidate.score > best.score:
                    best = candidate
                continue
            expected_bits = int(desc.sectors_per_track * desc.sector_size * 16)
            diff = abs(expected_bits - mfm_bits)
            score += 0.15 * (1.0 - (diff / max(expected_bits, mfm_bits, 1)))
            evidence.append(f"bitstream_len={mfm_bits:.0f}")
            evidence.append(f"expected_bits={expected_bits}")
            score += _apply_mfm_raw_density_bonus(
                desc, mfm_geometry, logical_tracks, heads_present, mfm_bits, evidence
            )
            if desc.layout_id.startswith("amiga_") and mfm_bits >= 90_000 and logical_tracks >= 79:
                score += 0.2
                evidence.append("amiga_bitstream_bonus=1")
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
            if logical_tracks and logical_tracks <= 40 and desc.sides == 1:
                score += 0.2
                evidence.append("gcr_low_density_bonus=1")
            if desc.layout_id.startswith("commodore_gcr_1541") and 30 <= logical_tracks <= 42:
                score += 0.25
                evidence.append("commodore_1541_bonus=1")
            if desc.layout_id == "commodore_gcr_1541_cpm_170k":
                score += 0.2
                evidence.append("commodore_cpm_bonus=1")
            if desc.layout_id == "apple2_gcr_nofs_140_140k" and track_samples and tracks_with_sectors == 0:
                score -= 0.2
                evidence.append("apple2_no_sector_penalty=1")
            if desc.layout_id.startswith("commodore_gcr_1541") and track_samples and tracks_with_sectors == 0:
                score += 0.2
                evidence.append("commodore_no_sector_bonus=1")
            if logical_tracks and logical_tracks <= 40 and desc.sides == 1:
                score += 0.2
                evidence.append("gcr_low_density_bonus=1")
            if desc.layout_id.startswith("commodore_gcr_1541") and 30 <= logical_tracks <= 42:
                score += 0.25
                evidence.append("commodore_1541_bonus=1")
            if desc.layout_id == "commodore_gcr_1541_cpm_170k":
                score += 0.2
                evidence.append("commodore_cpm_bonus=1")
        if desc.encoding == "gcr" and gcr_conf is not None:
            track_samples = gcr_geometry.get("track_samples")
            tracks_with_sectors = gcr_geometry.get("tracks_with_sectors")
            coverage = (tracks_with_sectors / track_samples) if track_samples else 1.0
            coverage_factor = coverage if coverage < 0.5 and gcr_conf < 0.9 else 1.0
            score += 0.4 * gcr_conf * coverage_factor
            if gcr_conf < 0.3:
                score -= 0.5
                evidence.append("gcr_low_conf_penalty=1")
            if gcr_conf < 0.6 and coverage < 0.9:
                score -= 0.2
                evidence.append("gcr_conf_penalty=1")
            if mfm_conf is not None and 0.42 <= gcr_conf < 0.6 and mfm_conf >= 0.7:
                score -= 0.3
                evidence.append("gcr_vs_mfm_penalty=1")
            if coverage_factor != 1.0:
                evidence.append(f"gcr_coverage_factor={coverage_factor:.2f}")
            evidence.append(f"gcr_conf={gcr_conf:.2f}")

        if desc.encoding == "fm":
            geometry = fm_geometry
            if geometry.get("track_samples") and geometry.get("tracks_with_sectors") == 0:
                score -= 0.4
                evidence.append("fm_no_sectors_penalty=1")
        if desc.encoding == "fm" and fm_bits is not None:
            if logical_tracks > 77:
                continue
            if (
                mfm_conf is not None
                and mfm_conf >= 0.95
                and mfm_geometry.get("tracks_with_sectors")
                and fm_conf is not None
                and fm_conf < 0.85
            ):
                score -= 0.45
                evidence.append("fm_vs_strong_mfm_penalty=1")
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

        adjusted_score = _apply_layout_hint(desc, hint, score, evidence)
        candidate = LayoutCandidate(layout=desc, score=adjusted_score, evidence=evidence)
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
