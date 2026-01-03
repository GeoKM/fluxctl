import struct
from pathlib import Path

from fluxctl.scp import parse_scp


def _pack_flux_be(intervals: list[int]) -> bytes:
    return struct.pack(f">{len(intervals)}H", *intervals)


def _build_trk_block(
    track_index: int,
    revolutions: list[list[int]],
    *,
    index_tick_overrides: list[int] | None = None,
) -> bytes:
    """Return a TRK block using the real per-revolution layout.

    The first 16 bytes hold the ``TRK`` prologue plus revolution 0's header.
    Each additional revolution adds a 12-byte record. Flux blobs are packed
    after the header area, and offsets point into this combined block.
    """

    header_length = 16 + max(0, len(revolutions) - 1) * 12
    table = bytearray(header_length)
    table[:3] = b"TRK"
    table[3] = track_index

    flux_segments: list[bytes] = []
    current_offset = header_length
    for rev_index, ticks in enumerate(revolutions):
        flux_bytes = _pack_flux_be(ticks)
        index_ticks = (
            index_tick_overrides[rev_index]
            if index_tick_overrides is not None
            else sum(ticks)
        )
        entry_offset = 4 if rev_index == 0 else 16 + (rev_index - 1) * 12
        struct.pack_into(
            "<III", table, entry_offset, index_ticks, len(ticks), current_offset
        )
        flux_segments.append(flux_bytes)
        current_offset += len(flux_bytes)

    return bytes(table + b"".join(flux_segments))


def _build_scp_image(
    *,
    version: int,
    raw_timebase: int,
    revolutions_per_track: int,
    start_track: int,
    end_track: int,
    track_blocks: dict[int, bytes],
) -> bytes:
    header = bytearray(16)
    header[:3] = b"SCP"
    header[3] = version
    header[5] = revolutions_per_track
    header[6] = start_track
    header[7] = end_track
    header[8:12] = raw_timebase.to_bytes(4, "little")

    offsets = [0] * 168
    current_offset = 16 + 168 * 4
    payload = bytearray()

    for idx in sorted(track_blocks):
        offsets[idx] = current_offset
        payload += track_blocks[idx]
        current_offset += len(track_blocks[idx])

    offsets_blob = b"".join(struct.pack("<I", off) for off in offsets)
    return bytes(header + offsets_blob + payload)


def test_parse_scp_preserves_per_revolution_flux(tmp_path: Path) -> None:
    track0 = _build_trk_block(0, [[1, 2, 3], [4, 5]])
    track1 = _build_trk_block(1, [[0x0102], [6]])

    image_path = tmp_path / "multi_rev.scp"
    image_path.write_bytes(
        _build_scp_image(
            version=0,
            raw_timebase=20_000,  # version 0 => default to 25ns
            revolutions_per_track=2,
            start_track=0,
            end_track=1,
            track_blocks={0: track0, 1: track1},
        )
    )

    image = parse_scp(image_path)

    assert image.timebase_ns == 25.0
    assert image.revolutions_per_track == 2
    assert len(image.tracks) == 2

    track0_parsed, track1_parsed = image.tracks

    assert track0_parsed.track == 0
    assert track0_parsed.side == 0
    assert [list(rev.interval_ns) for rev in track0_parsed.revolutions] == [
        [25, 50, 75],
        [100, 125],
    ]
    assert [rev.data_offset for rev in track0_parsed.revolutions] == [716, 722]
    for rev in track0_parsed.revolutions:
        assert rev.index_time_ns == sum(rev.interval_ns)

    assert track1_parsed.track == 0
    assert track1_parsed.side == 1
    assert [list(rev.interval_ns) for rev in track1_parsed.revolutions] == [
        [6450],
        [150],
    ]
    assert track1_parsed.revolutions[0].data_offset != track1_parsed.revolutions[1].data_offset
    for rev in track1_parsed.revolutions:
        assert rev.index_time_ns == sum(rev.interval_ns)


def test_timebase_and_single_sided_track_mapping(tmp_path: Path) -> None:
    track_even = _build_trk_block(99, [[2], [2, 2], [3]])

    image_path = tmp_path / "single_sided.scp"
    image_path.write_bytes(
        _build_scp_image(
            version=1,
            raw_timebase=2_000,  # 2000 kHz capture clock => 500ns
            revolutions_per_track=3,
            start_track=0,
            end_track=2,
            track_blocks={2: track_even},
        )
    )

    image = parse_scp(image_path)

    assert image.timebase_ns == 500.0
    assert len(image.tracks) == 1

    track = image.tracks[0]
    assert track.track == 1  # index 2 => cylinder 1
    assert track.side == 0  # even index => head 0

    expected_intervals = [[1000], [1000, 1000], [1500]]
    assert [list(rev.interval_ns) for rev in track.revolutions] == expected_intervals
    assert len({rev.data_offset for rev in track.revolutions}) == len(track.revolutions)
    for rev in track.revolutions:
        assert rev.index_time_ns == sum(rev.interval_ns)
