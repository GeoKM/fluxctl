"""Core data models for fluxctl."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

# Sector-level models are defined in ``fluxctl.sector.models`` but re-exported
# here for convenience to avoid cascading import changes.
from .sector.models import Sector, TrackNibbles, TrackSectors


@dataclass
class ProvenanceRecord:
    """Structured record describing how an artefact was produced."""

    tool_name: str
    tool_version: str
    operation: str
    input_path: Optional[Path]
    input_sha256: str
    output_path: Optional[Path]
    output_sha256: Optional[str]
    parameters: Dict[str, str]
    plugins: Dict[str, str]
    decoder: Optional[str] = None
    encoder: Optional[str] = None
    timestamp: Optional[str] = None
    evidence: List[str] = field(default_factory=list)

    @staticmethod
    def sha256_bytes(payload: bytes) -> str:
        """Return the SHA-256 hash for ``payload``.

        The helper is intentionally kept small to make provenance tests easy to
        follow and to avoid scattering hashing helpers across modules.
        """

        import hashlib

        return hashlib.sha256(payload).hexdigest()

    @staticmethod
    def sha256_file(path: Path) -> str:
        """Return SHA-256 hash of a file located at ``path``."""

        import hashlib

        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()


@dataclass
class RevolutionFlux:
    index: int
    interval_ns: Sequence[int]
    index_time_ns: Optional[int] = None
    data_length_bytes: Optional[int] = None
    data_offset: Optional[int] = None


@dataclass
class TrackFlux:
    track: int
    side: int
    revolutions: List[RevolutionFlux] = field(default_factory=list)


@dataclass
class SCPImage:
    path: Path
    version: int
    revolutions_per_track: int
    timebase_ns: float
    tracks: List[TrackFlux]
    warnings: List[str] = field(default_factory=list)


@dataclass
class BitDecodeMetrics:
    pll_lock_score: Optional[float] = None
    rpm_estimate: Optional[float] = None
    confidence: Optional[float] = None


@dataclass
class Bitstream:
    bits: List[int]
    metrics: BitDecodeMetrics
    source_revs: List[int] = field(default_factory=list)


@dataclass
class CandidateFormat:
    candidate_id: str
    encoding: Optional[str]
    layout_id: Optional[str]
    filesystem: Optional[str]
    score: float
    evidence: List[str]


@dataclass
class QCReport:
    schema_version: str
    tool: Dict[str, str]
    input: Dict[str, object]
    scp: Dict[str, object]
    analysis_params: Dict[str, object]
    summary: Dict[str, object]
    track_metrics: List[Dict[str, object]]
    sector_table: Optional[List[Dict[str, object]]] = None
    evidence: List[str] = field(default_factory=list)
    extensions: Dict[str, object] = field(default_factory=dict)


@dataclass
class LayoutDescriptor:
    schema_version: str
    layout_id: str
    name: str
    encoding: str
    rpm_nominal: int
    sides: int
    tracks: int
    sectors_per_track: int
    sector_size: int
    gap3_hint: Optional[int]
    id_rules: Dict[str, object]
    crc: Dict[str, object]
    address_marks: Dict[str, object]
    track_sectors: Optional[List[int]] = None
    sector_sizes: Optional[List[int]] = None
    track_overrides: Optional[List[Dict[str, object]]] = None

    def expected_sectors_for_track(self, track_index: int, head_index: Optional[int] = None) -> int:
        """Return the expected sector count for a logical track."""

        if self.track_overrides:
            override = self._match_override(track_index, head_index)
            if override and "sectors_per_track" in override:
                return int(override["sectors_per_track"])
        if self.track_sectors:
            if 0 <= track_index < len(self.track_sectors):
                return self.track_sectors[track_index]
            return self.track_sectors[-1]
        return self.sectors_per_track

    def expected_sector_sizes_for_track(
        self, track_index: int, head_index: Optional[int] = None
    ) -> Optional[List[int]]:
        """Return per-sector sizes when a track uses mixed sector sizes."""

        if self.track_overrides:
            override = self._match_override(track_index, head_index)
            if override and "sector_sizes" in override:
                return list(override["sector_sizes"])
        if self.sector_sizes:
            return list(self.sector_sizes)
        return None

    def _match_override(
        self, track_index: int, head_index: Optional[int]
    ) -> Optional[Dict[str, object]]:
        if not self.track_overrides:
            return None
        for override in self.track_overrides:
            head = override.get("head")
            if head is not None and head_index is None:
                continue
            if head is not None and head_index is not None and int(head) != head_index:
                continue
            track_range = override.get("track_range")
            if track_range is None or self._track_range_matches(track_range, track_index):
                return override
        return None

    @staticmethod
    def _track_range_matches(track_range: str, track_index: int) -> bool:
        if track_range == "*":
            return True
        for part in track_range.split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                start_str, end_str = part.split("-", 1)
                start = int(start_str)
                end = int(end_str)
                if start <= track_index <= end:
                    return True
            else:
                if int(part) == track_index:
                    return True
        return False
