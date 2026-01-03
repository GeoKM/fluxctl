from fluxctl.exporters.g64 import DEFAULT_TRACK_LENGTH, G64Exporter
from fluxctl.sector.models import TrackNibbles


class FakeNibbleImage:
    def __init__(self, tracks_nibbles):
        self.tracks_nibbles = tracks_nibbles


def test_g64_exporter_builds_tables_and_blobs() -> None:
    track0 = TrackNibbles(track=0, head=0, gcr_bytes=b"\x01\x02", source="rev0", confidence=1.0)
    track1 = TrackNibbles(track=1, head=0, gcr_bytes=b"\xAA", source="rev1", confidence=0.9)
    image = FakeNibbleImage([track0, track1])

    exporter = G64Exporter()
    payload = exporter.export(image)

    assert payload.startswith(b"GCR-1541")
    assert payload[8] == 0x00
    assert payload[9] == exporter.track_count

    header_len = 8 + 1 + 1 + 2
    table_start = header_len
    length_table_start = header_len + exporter.track_count * 4
    data_start = header_len + exporter.track_count * 4 + exporter.track_count * 2

    offset_track0 = int.from_bytes(payload[table_start : table_start + 4], "little")
    offset_track1_half = int.from_bytes(payload[table_start + 4 : table_start + 8], "little")
    offset_track2 = int.from_bytes(payload[table_start + 8 : table_start + 12], "little")

    length_track0 = int.from_bytes(
        payload[length_table_start : length_table_start + 2], "little"
    )
    length_track1_half = int.from_bytes(
        payload[length_table_start + 2 : length_table_start + 4], "little"
    )
    length_track2 = int.from_bytes(
        payload[length_table_start + 4 : length_table_start + 6], "little"
    )

    assert offset_track0 == data_start
    assert offset_track1_half == 0
    assert offset_track2 == data_start + DEFAULT_TRACK_LENGTH
    assert length_track0 == DEFAULT_TRACK_LENGTH
    assert length_track1_half == 0
    assert length_track2 == DEFAULT_TRACK_LENGTH

    assert payload[offset_track0 : offset_track0 + 4] == b"\x01\x02\x01\x02"
    assert payload[offset_track2 : offset_track2 + 2] == b"\xAA\xAA"

    assert len(payload) == offset_track2 + DEFAULT_TRACK_LENGTH
