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

    def expected_sectors_for_track(self, track_index: int) -> int:
        """Return the expected sector count for a logical track."""

        if self.track_sectors:
            if 0 <= track_index < len(self.track_sectors):
                return self.track_sectors[track_index]
            return self.track_sectors[-1]
        return self.sectors_per_track
