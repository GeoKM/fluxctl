import struct
from pathlib import Path

from fluxctl.scp import parse_scp


def _build_revolution_block(track: int, side: int, intervals: list[int]) -> bytes:
    header = bytearray(32)
    header[:3] = b"TRK"
    header[3] = track
    header[4] = side

    flux = struct.pack(f">{len(intervals)}H", *intervals)
    return bytes(header) + flux


def _build_track_block(track: int, side: int, revolutions: list[list[int]], base_offset: int) -> tuple[bytes, int]:
    header = bytearray(32)
    header[:3] = b"TRK"
    header[3] = track
    header[4] = side

    current_offset = base_offset + len(header)
    revolution_blocks = []
    offsets = []

    for rev_intervals in revolutions:
        offsets.append(current_offset)
        block = _build_revolution_block(track, side, rev_intervals)
        revolution_blocks.append(block)
        current_offset += len(block)

    for idx, offset in enumerate(offsets):
        header[6 + idx * 4 : 10 + idx * 4] = offset.to_bytes(4, "little")

    track_data = bytes(header) + b"".join(revolution_blocks)
    return track_data, current_offset


def _build_test_image() -> bytes:
    version = 0x10
    revolutions = 2
    start_track = 0
    end_track = 1
    timebase = 25

    track_count = end_track - start_track + 1
    offsets_table = bytearray(track_count * 4)

    current_offset = 16 + len(offsets_table)
    track0_offset = current_offset
    track0_block, current_offset = _build_track_block(0, 0, [[1, 2], [3]], current_offset)

    track1_offset = current_offset
    track1_block, current_offset = _build_track_block(0, 1, [[4], [5, 6]], current_offset)

    offsets_table[0:4] = track0_offset.to_bytes(4, "little")
    offsets_table[4:8] = track1_offset.to_bytes(4, "little")

    header = bytearray(16)
    header[:3] = b"SCP"
    header[3] = version
    header[5] = revolutions
    header[6] = start_track
    header[7] = end_track
    header[12:14] = timebase.to_bytes(2, "little")

    return bytes(header + offsets_table + track0_block + track1_block)


def test_parse_scp_preserves_per_revolution_flux(tmp_path: Path) -> None:
    image_path = tmp_path / "multi_rev.scp"
    image_path.write_bytes(_build_test_image())

    image = parse_scp(image_path)

    assert image.revolutions_per_track == 2
    assert len(image.tracks) == 2

    track0, track1 = image.tracks

    assert track0.track == 0
    assert track0.side == 0
    assert [rev.interval_ns for rev in track0.revolutions] == [[25, 50], [75]]

    assert track1.track == 0
    assert track1.side == 1
    assert [rev.interval_ns for rev in track1.revolutions] == [[100], [125, 150]]
