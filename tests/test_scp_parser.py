import struct
from pathlib import Path

from fluxctl.scp import parse_scp


def _pack_flux(intervals: list[int]) -> bytes:
    return struct.pack(f"<{len(intervals)}H", *intervals)


def _build_track_header(track_index: int, side: int, revolution_blobs: list[bytes], flux_offset: int) -> tuple[bytes, int]:
    header = bytearray(16 + 8 * len(revolution_blobs))
    header[:3] = b"TRK"
    header[3] = track_index
    header[4] = side

    current_offset = flux_offset
    for idx, blob in enumerate(revolution_blobs):
        header[16 + idx * 4 : 20 + idx * 4] = current_offset.to_bytes(4, "little")
        header[16 + 4 * len(revolution_blobs) + idx * 4 : 20 + 4 * len(revolution_blobs) + idx * 4] = len(blob).to_bytes(4, "little")
        current_offset += len(blob)

    return bytes(header), current_offset


def _build_test_image() -> bytes:
    version = 0x10
    revolutions = 2
    start_track = 0
    end_track = 1
    timebase = 25

    flux_side0 = [_pack_flux([1, 2]), _pack_flux([3])]
    flux_side1 = [_pack_flux([4]), _pack_flux([5, 6])]

    track_headers = []
    flux_blobs: list[bytes] = []

    track_count = end_track - start_track + 1
    offsets_table = bytearray(track_count * 4)

    header_size = 16 + 8 * revolutions
    header_start = 16 + len(offsets_table)
    flux_start = header_start + header_size * track_count

    current_flux_offset = flux_start

    # Build headers for each track then append the flux blobs after all headers.
    for idx, (track_index, side, revolutions_blob) in enumerate(
        (
            (0, 0, flux_side0),
            (1, 1, flux_side1),
        )
    ):
        header, current_flux_offset = _build_track_header(track_index, side, revolutions_blob, current_flux_offset)
        track_headers.append(header)
        offsets_table[idx * 4 : (idx + 1) * 4] = (header_start + idx * header_size).to_bytes(4, "little")
        flux_blobs.extend(revolutions_blob)

    header = bytearray(16)
    header[:3] = b"SCP"
    header[3] = version
    header[5] = revolutions
    header[6] = start_track
    header[7] = end_track
    header[12:14] = timebase.to_bytes(2, "little")

    return bytes(header + offsets_table + b"".join(track_headers) + b"".join(flux_blobs))


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

    assert track1.track == 0
    assert track1.side == 1
    assert [list(rev.interval_ns) for rev in track1.revolutions] == [[100], [125, 150]]

    assert track0.revolutions[0].data_offset != track0.revolutions[1].data_offset
