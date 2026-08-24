"""Native deterministic SuperCard Pro exporter."""
from __future__ import annotations

import struct
from typing import Any, Dict

from ..encoding.synthetic_flux import supported_scp_layout, synthesize_track_flux
from ..exceptions import ExportError
from ..filesystems import TrackSectorImage
from . import Exporter


OFFSET_ENTRIES = 168
OFFSET_TABLE_SIZE = OFFSET_ENTRIES * 4


class SCPExporter(Exporter):
    """Encode reconstructed logical sectors as synthetic indexed SCP flux."""

    extensions = (".scp",)

    def supports(self, image) -> bool:
        if not isinstance(image, TrackSectorImage):
            return False
        supported, _ = supported_scp_layout(getattr(image, "layout", None))
        return supported

    def export(self, image) -> bytes:
        if not isinstance(image, TrackSectorImage):
            raise ExportError("Native SCP export requires reconstructed track sectors")
        layout = getattr(image, "layout", None)
        supported, reason = supported_scp_layout(layout)
        if not supported:
            raise ExportError(reason)
        tracks = sorted(image.tracks, key=lambda item: (item.track, item.head))
        if not tracks:
            raise ExportError("Native SCP export requires at least one decoded track")

        encoded: dict[int, bytes] = {}
        interval_count = 0
        for track in tracks:
            track_index = int(track.track) * 2 + int(track.head)
            if not 0 <= track_index < OFFSET_ENTRIES:
                raise ExportError(f"Track T{track.track} H{track.head} exceeds the SCP track table")
            synthetic = synthesize_track_flux(track, layout)
            flux_data = _encode_tick_words(synthetic.intervals_ticks)
            interval_count += len(synthetic.intervals_ticks)
            descriptor = struct.pack("<III", synthetic.index_ticks, len(flux_data) // 2, 16)
            encoded[track_index] = struct.pack("<3sB", b"TRK", track_index) + descriptor + flux_data

        first_index = min(encoded)
        last_index = max(encoded)
        table = bytearray(OFFSET_TABLE_SIZE)
        track_data = bytearray()
        cursor = 16 + OFFSET_TABLE_SIZE
        for track_index in range(OFFSET_ENTRIES):
            block = encoded.get(track_index)
            if block is None:
                continue
            struct.pack_into("<I", table, track_index * 4, cursor + len(track_data))
            track_data.extend(block)
        data = bytes(table + track_data)
        flags = 0x01 | 0x02 | 0x08 | 0x80  # indexed, 96TPI, normalised, flux creator
        if int(layout.rpm_nominal or 300) == 360:
            flags |= 0x04
        heads = {int(track.head) for track in tracks}
        single_sided = 1 if heads == {0} else 2 if heads == {1} else 0
        header = struct.pack(
            "<3s9BI",
            b"SCP", 0, 0x80, 1, first_index, last_index, flags, 0,
            single_sided, 0, sum(data) & 0xFFFFFFFF,
        )
        self._metadata = {
            "synthetic_flux": True,
            "revolutions": 1,
            "timebase_ns": 25,
            "tracks": len(encoded),
            "flux_intervals": interval_count,
            "preservation_limit": "logical flux only; analogue timing, weak bits, write splices, and copy protection are not preserved",
        }
        return header + data

    def metadata(self) -> Dict[str, Any]:
        return {"name": "Native SCP exporter", "version": "0.1", **getattr(self, "_metadata", {})}


def _encode_tick_words(intervals: tuple[int, ...]) -> bytes:
    payload = bytearray()
    for original in intervals:
        ticks = max(1, int(original))
        while ticks >= 0x10000:
            payload.extend(b"\x00\x00")
            ticks -= 0x10000
        if ticks == 0:
            ticks = 1
        payload.extend(struct.pack(">H", ticks))
    return bytes(payload)


__all__ = ["SCPExporter"]
