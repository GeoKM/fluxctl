"""Helpers for detecting encodings and layouts from SCP images."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from .decoding import Decoder
from .exceptions import FluxDecodeError
from .models import Bitstream, LayoutDescriptor, SCPImage
from .plugins import registry


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


def detect_encoding(image: SCPImage, path: Optional[Path] = None) -> Optional[EncodingCandidate]:
    """Return the best matching decoder based on decode confidence."""

    stem = path.stem.lower() if path else ""
    best: Optional[EncodingCandidate] = None
    for key, plugin in registry.encoding.items():
        bitstream = _decode_once(plugin.entry, image)
        if bitstream is None:
            continue
        confidence = bitstream.metrics.confidence or 0.0
        evidence = [f"decoder={plugin.name}", f"confidence={confidence:.2f}"]
        if stem:
            if "gcr" in stem:
                if key == "gcr":
                    confidence += 1.0
                    evidence.append("filename_hint=gcr")
                else:
                    confidence -= 0.5
            if "mfm" in stem and key == "mfm":
                confidence += 0.2
                evidence.append("filename_hint=mfm")
        candidate = EncodingCandidate(encoding=key, confidence=confidence, evidence=evidence)
        if best is None or candidate.confidence > best.confidence:
            best = candidate
    return best


def detect_layout(image: SCPImage, encoding: str, path: Path) -> Optional[LayoutCandidate]:
    """Pick the most likely layout for an image and encoding."""

    layouts = [desc for desc in registry.layout.values() if desc.encoding == encoding]
    if not layouts:
        return None

    track_ids = [track.track for track in image.tracks]
    step = infer_track_step(track_ids)
    logical_tracks = logical_track_count(track_ids, step)
    stem = path.stem.lower()

    best: Optional[LayoutCandidate] = None
    for desc in layouts:
        expected_total = desc.tracks * desc.sides
        diff = abs(expected_total - logical_tracks)
        score = 1.0 - (diff / max(expected_total, logical_tracks, 1))
        evidence = [f"expected_tracks={expected_total}", f"logical_tracks={logical_tracks}"]

        if "cpm" in stem and "cpm" in desc.layout_id:
            score += 0.2
            evidence.append("filename_hint=cpm")
        if "c128" in stem and "1571" in desc.layout_id:
            score += 0.1
            evidence.append("filename_hint=c128")
        if "1541" in stem and "1541" in desc.layout_id:
            score += 0.1
            evidence.append("filename_hint=1541")
        if "1571" in stem and "1571" in desc.layout_id:
            score += 0.1
            evidence.append("filename_hint=1571")
        if "170k" in stem and "170k" in desc.layout_id:
            score += 0.05
            evidence.append("filename_hint=170k")
        if "340k" in stem and "340k" in desc.layout_id:
            score += 0.05
            evidence.append("filename_hint=340k")
        if "341k" in stem and "341k" in desc.layout_id:
            score += 0.05
            evidence.append("filename_hint=341k")

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
