from pathlib import Path

from fluxctl.decoding.mfm import mfm_decoder
from fluxctl.reports.map import (
    apply_c64_cpm_2_2_logical_overlay,
    build_cbm_bam_block_map,
    build_disk_map,
    build_disk_map_from_tracksectors,
    render_ascii,
    render_svg,
)
from fluxctl.scp import parse_scp
from fluxctl.sector.models import Sector, TrackSectors


FIXTURE_GOOD = Path("tests/fixtures/5.25inch/IBM/IBM-Generic-SSDD-MFM-IBMPC-180K.scp")


def test_disk_map_generation_counts_and_ascii() -> None:
    image = parse_scp(FIXTURE_GOOD)
    image.path = Path("misleading-2880K-name.scp")
    disk_map = build_disk_map(image, mfm_decoder)

    assert disk_map.total_tracks == len(image.tracks)
    assert disk_map.max_sectors_per_track == 9

    ascii_map = render_ascii(disk_map)
    lines = ascii_map.splitlines()
    track_lines = [line for line in lines if line.startswith("Track")]
    assert len(track_lines) == disk_map.total_tracks
    assert lines[0].startswith("Legend")
    assert all(line.startswith("Track") for line in track_lines[:3])
    assert any("×" in line for line in lines)


def test_svg_renderer_contains_expected_segments() -> None:
    image = parse_scp(FIXTURE_GOOD)
    disk_map = build_disk_map(image, mfm_decoder)

    svg = render_svg(disk_map)
    assert "<svg" in svg
    expected_paths = sum(len(track) for track in disk_map.tracks)
    assert svg.count("<path") >= expected_paths


def _sector(sector_id: int) -> Sector:
    return Sector(
        cylinder=0,
        head=0,
        sector_id=sector_id,
        size_code=1,
        data=b"x" * 256,
        crc_ok=True,
        confidence=1.0,
        deleted=False,
    )


def test_disk_map_preserves_zoned_sector_counts() -> None:
    disk_map = build_disk_map_from_tracksectors(
        [
            TrackSectors(track=0, head=0, sectors=[_sector(idx) for idx in range(21)]),
            TrackSectors(track=1, head=0, sectors=[_sector(idx) for idx in range(19)]),
            TrackSectors(track=2, head=0, sectors=[_sector(idx) for idx in range(18)]),
            TrackSectors(track=3, head=0, sectors=[_sector(idx) for idx in range(17)]),
        ]
    )

    assert [len(row) for row in disk_map.tracks] == [21, 19, 18, 17]
    assert disk_map.max_sectors_per_track == 21
    assert [len(row) for row in disk_map.sector_details] == [21, 19, 18, 17]


def test_c64_cpm_overlay_marks_logically_unused_physical_sectors() -> None:
    disk_map = build_disk_map_from_tracksectors(
        [
            TrackSectors(track=2, head=0, sectors=[_sector(idx) for idx in range(21)]),
            TrackSectors(track=17, head=0, sectors=[_sector(idx) for idx in range(19)]),
            TrackSectors(track=34, head=0, sectors=[_sector(idx) for idx in range(17)]),
        ]
    )

    apply_c64_cpm_2_2_logical_overlay(disk_map, allocated_blocks={0, 8})

    assert disk_map.tracks[0][:4] == ["good"] * 4
    assert disk_map.tracks[0][4:] == ["unused"] * 17
    assert disk_map.tracks[1] == ["unused"] * 19
    assert disk_map.tracks[2] == ["unused"] * 17


def test_cbm_bam_block_map_uses_grid_style() -> None:
    disk_map = build_cbm_bam_block_map(
        [
            (1, 0, 0, "bam_file"),
            (1, 0, 1, "bam_free"),
            (36, 1, 0, "bam_system"),
        ]
    )

    assert disk_map.render_style == "grid"
    assert disk_map.tracks == [["bam_file", "bam_free"], ["bam_system"]]
    assert disk_map.track_ids == [(1, 0), (36, 1)]
