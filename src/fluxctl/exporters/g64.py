"""G64 exporter for Commodore 1541 nibble images."""
from __future__ import annotations

from typing import Any, Dict, List, Sequence

from ..exceptions import ExportError
from ..sector.models import TrackNibbles
from . import Exporter

G64_SIGNATURE = b"GCR-1541"
DEFAULT_TRACK_COUNT = 84  # 0-83 half-tracks
DEFAULT_TRACK_LENGTH = 7928  # bytes per track payload, used by VICE/cc1541


class G64Exporter(Exporter):
    """Serialize decoded GCR nibbles into a G64 container.

    The header/offset layout matches the variant accepted by VICE and cc1541
    ("GCR-1541" signature, 32-bit offset table, 16-bit length table).
    """

    extensions = (".g64",)

    def __init__(self, track_count: int = DEFAULT_TRACK_COUNT, target_length: int = DEFAULT_TRACK_LENGTH) -> None:
        self.track_count = track_count
        self.target_length = target_length

    def supports(self, image) -> bool:
        return bool(getattr(image, "tracks_nibbles", None))

    def _normalize_stream(self, payload: bytes) -> bytes:
        if not payload:
            return b""
        if len(payload) < self.target_length:
            repeats = (self.target_length + len(payload) - 1) // len(payload)
            return (payload * repeats)[: self.target_length]
        return payload[: self.target_length]

    def _build_header(self) -> bytearray:
        header = bytearray(G64_SIGNATURE)
        header.append(0x00)  # version
        header.append(self.track_count)  # half-track count
        header.extend(b"\x00\x00")  # reserved/track spacing placeholder
        return header

    def _map_tracks(self, tracks: Sequence[TrackNibbles]) -> dict[int, TrackNibbles]:
        mapping: dict[int, TrackNibbles] = {}
        for entry in tracks:
            # Map logical track numbers onto half-track indices (0-based) used by G64.
            index = entry.track * 2
            if index not in mapping:
                mapping[index] = entry
        return mapping

    def export(self, image) -> bytes:
        tracks: Sequence[TrackNibbles] = getattr(image, "tracks_nibbles", None) or []
        if not tracks:
            raise ExportError("G64 exporter requires decoded nibble streams")

        header = self._build_header()
        offsets: List[int] = [0] * self.track_count
        lengths: List[int] = [0] * self.track_count
        blobs: List[bytes] = []

        lookup = self._map_tracks(tracks)
        current_offset = len(header) + self.track_count * 4 + self.track_count * 2
        written_tracks = 0

        for halftrack in range(self.track_count):
            nibble = lookup.get(halftrack)
            if nibble is None:
                continue
            normalized = self._normalize_stream(nibble.gcr_bytes)
            if not normalized:
                continue
            offsets[halftrack] = current_offset
            lengths[halftrack] = len(normalized)
            blobs.append(normalized)
            current_offset += len(normalized)
            written_tracks += 1

        payload = bytearray(header)
        for offset in offsets:
            payload.extend(offset.to_bytes(4, byteorder="little"))
        for length in lengths:
            payload.extend(length.to_bytes(2, byteorder="little"))
        for blob in blobs:
            payload.extend(blob)

        self._metadata = {
            "name": "G64 exporter",
            "version": "0.1",
            "tracks_written": written_tracks,
            "target_length": self.target_length,
        }
        return bytes(payload)

    def metadata(self) -> Dict[str, Any]:
        return getattr(self, "_metadata", {"name": "G64 exporter", "version": "0.1"})
