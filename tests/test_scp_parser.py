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
    """Return a TRK block matching the SuperCard Pro per-revolution layout."""

    table = bytearray(4 + len(revolutions) * 12)
    table[:3] = b"TRK"
    table[3] = track_index

    flux_segments: list[bytes] = []
    current_offset = len(table)
    for rev_index, ticks in enumerate(revolutions):
        flux_bytes = _pack_flux_be(ticks)
        index_ticks = (
            index_tick_overrides[rev_index]
            if index_tick_overrides is not None
            else sum(ticks)
        )
        struct.pack_into(
            "<III", table, 4 + rev_index * 12, index_ticks, len(ticks), current_offset
        )
        flux_segments.append(flux_bytes)
        current_offset += len(flux_bytes)

    return bytes(table + b"".join(flux_segments))


def _build_test_image() -> bytes:
    version = 0x10
    revolutions = 2
    start_track = 0
    end_track = 1

    track0 = _build_trk_block(0, [[1, 2], [3]])
    # Use a tick pattern that would decode differently if misread as little-endian
    # to verify byte order.
    track1 = _build_trk_block(1, [[0x0102], [5, 6]])

    track_offsets = [16 + 4 * (end_track - start_track + 1)]
    track_offsets.append(track_offsets[0] + len(track0))

    header = bytearray(16)
    header[:3] = b"SCP"
    header[3] = version
    header[5] = revolutions
    header[6] = start_track
    header[7] = end_track

    offsets_blob = b"".join(struct.pack("<I", off) for off in track_offsets)
    return bytes(header + offsets_blob + track0 + track1)


def _build_index_time_image(index_ticks: int) -> bytes:
    version = 0x10
    revolutions = 1
    start_track = 0
    end_track = 0

    track0 = _build_trk_block(0, [[1, 2]], index_tick_overrides=[index_ticks])

    track_offsets = [16 + 4 * (end_track - start_track + 1)]

    header = bytearray(16)
    header[:3] = b"SCP"
    header[3] = version
    header[5] = revolutions
    header[6] = start_track
    header[7] = end_track

    offsets_blob = b"".join(struct.pack("<I", off) for off in track_offsets)
    return bytes(header + offsets_blob + track0)


def test_parse_scp_preserves_per_revolution_flux(tmp_path: Path) -> None:
    image_path = tmp_path / "multi_rev.scp"
    image_path.write_bytes(_build_test_image())

    image = parse_scp(image_path)

    assert image.revolutions_per_track == 2
    assert len(image.tracks) == 2

    track0, track1 = image.tracks

    assert track0.track == 0
    assert track0.side == 0
    assert [list(rev.interval_ns) for rev in track0.revolutions] == [[25, 50], [75]]
    assert [rev.data_offset for rev in track0.revolutions] == [28, 32]
    assert any(rev.interval_ns for rev in track0.revolutions)

    assert track1.track == 0
    assert track1.side == 1
    assert [list(rev.interval_ns) for rev in track1.revolutions] == [[6450], [125, 150]]
    assert track1.revolutions[0].data_offset != track1.revolutions[1].data_offset


def test_tick_words_use_big_endian(tmp_path: Path) -> None:
    image_path = tmp_path / "endian.scp"
    image_path.write_bytes(_build_test_image())

    image = parse_scp(image_path)

    first_tick = image.tracks[1].revolutions[0].interval_ns[0]
    # If the tick word were read as little-endian, the interval would be much
    # larger (0x0201 * 25ns). Confirm the parser keeps the big-endian ordering.
    assert first_tick == 0x0102 * 25


def test_index_time_reports_nanoseconds(tmp_path: Path) -> None:
    image_path = tmp_path / "index_time.scp"
    image_path.write_bytes(_build_index_time_image(8_000_000))

    image = parse_scp(image_path)

    assert image.tracks[0].revolutions[0].index_time_ns == 200_000_000
