import struct
import os
import subprocess
import sys
from pathlib import Path

from fluxctl.scp import OFFSET_TABLE_ENTRIES, _parse_flux_bytes, parse_scp

FIXTURE_8IN_1200K_FAT = Path("tests/fixtures/8inch/IBM/IBM-Generic-DSDD-MFM-IBMPC-1200K-B.scp")


def _pack_flux_be(intervals: list[int]) -> bytes:
    return struct.pack(f">{len(intervals)}H", *intervals)


def test_parse_flux_bytes_handles_overflow_and_odd_trailing_byte() -> None:
    payload = bytes.fromhex("0000000100000002FFFFAA")

    assert list(_parse_flux_bytes(payload, 1.0)) == [65537, 65538, 65535]


def test_parse_flux_bytes_fallback_matches_native() -> None:
    payload_hex = "0000000100000002FFFFAA"
    native = list(_parse_flux_bytes(bytes.fromhex(payload_hex), 1.0))
    script = (
        "from fluxctl.scp import _parse_flux_bytes; "
        f"print(list(_parse_flux_bytes(bytes.fromhex('{payload_hex}'), 1.0)))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        text=True,
        capture_output=True,
        check=True,
        env={**os.environ, "FLUXCTL_DISABLE_NATIVE": "1"},
    )

    assert completed.stdout.strip() == str(native)


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


def _build_zeroed_trk_block(track_index: int, ticks: list[int], revolutions: int) -> bytes:
    table = bytearray(4 + revolutions * 12)
    table[:3] = b"TRK"
    table[3] = track_index
    return bytes(table + _pack_flux_be(ticks))


def _build_scp_image(
    *,
    version: int,
    raw_timebase: int,
    revolutions_per_track: int,
    start_track: int,
    end_track: int,
    track_blocks: dict[int, bytes],
    offset_table_entries: int = OFFSET_TABLE_ENTRIES,
    explicit_offsets: dict[int, int] | None = None,
) -> bytes:
    header = bytearray(16)
    header[:3] = b"SCP"
    header[3] = version
    header[5] = revolutions_per_track
    header[6] = start_track
    header[7] = end_track
    header[8:12] = raw_timebase.to_bytes(4, "little")

    table_entries = min(offset_table_entries, OFFSET_TABLE_ENTRIES)
    offsets = [0] * table_entries
    current_offset = 16 + table_entries * 4
    payload = bytearray()

    for idx in sorted(track_blocks):
        if idx >= table_entries:
            raise ValueError("Track index outside offset table")
        offsets[idx] = current_offset
        payload += track_blocks[idx]
        current_offset += len(track_blocks[idx])

    if explicit_offsets:
        for idx, value in explicit_offsets.items():
            if idx >= table_entries:
                raise ValueError("Explicit offset outside offset table")
            offsets[idx] = value

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


def test_modern_small_timebase_matches_greaseweazle_default() -> None:
    image = parse_scp(FIXTURE_8IN_1200K_FAT)

    assert image.timebase_ns == 25.0
    assert len(image.tracks) == 156


def test_version_zero_nonstandard_timebase_matches_greaseweazle_default(tmp_path: Path) -> None:
    track_block = _build_trk_block(0, [[1, 2, 3]])

    image_path = tmp_path / "version0_35ns.scp"
    image_path.write_bytes(
        _build_scp_image(
            version=0,
            raw_timebase=35,
            revolutions_per_track=1,
            start_track=0,
            end_track=0,
            track_blocks={0: track_block},
        )
    )

    image = parse_scp(image_path)

    assert image.timebase_ns == 25.0
    assert [list(rev.interval_ns) for rev in image.tracks[0].revolutions] == [[25, 50, 75]]


def test_zeroed_modern_revolution_headers_use_25ns_salvage_timebase(tmp_path: Path) -> None:
    track_block = _build_zeroed_trk_block(0, [1, 1, 1, 1, 1, 1], revolutions=3)

    image_path = tmp_path / "zeroed_modern.scp"
    image_path.write_bytes(
        _build_scp_image(
            version=9,
            raw_timebase=65_537,
            revolutions_per_track=3,
            start_track=0,
            end_track=0,
            track_blocks={0: track_block},
        )
    )

    image = parse_scp(image_path)

    assert image.timebase_ns == 25.0
    assert "Used 25ns salvage timebase" in image.warnings[-1]
    assert [list(rev.interval_ns) for rev in image.tracks[0].revolutions] == [
        [25, 25],
        [25, 25],
        [25, 25],
    ]


def test_long_interval_overflow_round_trips(tmp_path: Path) -> None:
    track_block = _build_trk_block(0, [[0, 1, 0, 0, 2], [3]])

    image_path = tmp_path / "overflow.scp"
    image_path.write_bytes(
        _build_scp_image(
            version=1,
            raw_timebase=1,  # 1ns tick
            revolutions_per_track=2,
            start_track=0,
            end_track=0,
            track_blocks={0: track_block},
            offset_table_entries=4,
        )
    )

    image = parse_scp(image_path)

    assert len(image.tracks) == 1
    intervals = [list(rev.interval_ns) for rev in image.tracks[0].revolutions]
    assert intervals == [[65537, 131074], [3]]


def test_short_offset_table_clamps_to_file_length(tmp_path: Path) -> None:
    track_block = _build_trk_block(1, [[5]])

    image_path = tmp_path / "short_table.scp"
    image_path.write_bytes(
        _build_scp_image(
            version=0,
            raw_timebase=25,
            revolutions_per_track=1,
            start_track=0,
            end_track=2,
            track_blocks={1: track_block},
            offset_table_entries=2,
        )
    )

    image = parse_scp(image_path)

    assert [t.track for t in image.tracks] == [0]
    assert image.warnings == []


def test_out_of_range_offsets_are_ignored(tmp_path: Path) -> None:
    track_block = _build_trk_block(2, [[7]])

    image_path = tmp_path / "range_filter.scp"
    image_path.write_bytes(
        _build_scp_image(
            version=0,
            raw_timebase=25,
            revolutions_per_track=1,
            start_track=1,
            end_track=2,
            track_blocks={2: track_block},
            offset_table_entries=3,
            explicit_offsets={0: 4},
        )
    )

    image = parse_scp(image_path)

    assert [t.track for t in image.tracks] == [1]
    assert all("Track 0" not in warning for warning in image.warnings)


def test_track_index_mismatch_surfaces_warning(tmp_path: Path) -> None:
    track_block = _build_trk_block(9, [[4]])

    image_path = tmp_path / "index_mismatch.scp"
    image_path.write_bytes(
        _build_scp_image(
            version=1,
            raw_timebase=25,
            revolutions_per_track=1,
            start_track=0,
            end_track=0,
            track_blocks={0: track_block},
            offset_table_entries=8,
        )
    )

    image = parse_scp(image_path)

    assert image.tracks[0].track == 0
    assert any("mismatches TRK byte" in warning for warning in image.warnings)
