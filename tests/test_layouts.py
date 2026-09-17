from pathlib import Path

from fluxctl.layouts.loader import ensure_layout_loaded
from fluxctl.application.decode_operations import sectors_from_blob


def test_layout_loads():
    desc = ensure_layout_loaded("ibm_mfm_1440k")
    assert desc.layout_id == "ibm_mfm_1440k"
    assert desc.sectors_per_track == 18


def test_generic_cpm_8inch_fm_layout_loads():
    desc = ensure_layout_loaded("generic_fm_8inch_cpm_256k")
    assert desc.encoding == "fm"
    assert desc.sides == 1
    assert desc.tracks == 77
    assert desc.sectors_per_track == 26
    assert desc.sector_size == 128


def test_osborne_cpm_5inch_mfm_layout_loads():
    desc = ensure_layout_loaded("osborne_mfm_ssdd_200k")
    assert desc.encoding == "mfm"
    assert desc.sides == 1
    assert desc.tracks == 40
    assert desc.sectors_per_track == 5
    assert desc.sector_size == 1024


def test_kaypro_cpm_5inch_mfm_layout_loads():
    desc = ensure_layout_loaded("kaypro_mfm_ssdd_40_200k")
    assert desc.encoding == "mfm"
    assert desc.sides == 1
    assert desc.tracks == 40
    assert desc.sectors_per_track == 10
    assert desc.sector_size == 512


def test_ibm_xdf_layout_loads_mixed_sector_geometry():
    desc = ensure_layout_loaded("ibm_xdf_1890k")
    assert desc.encoding == "mfm"
    assert desc.sides == 2
    assert desc.tracks == 80
    assert desc.expected_sectors_for_track(0, 0) == 19
    assert desc.expected_sectors_for_track(1, 0) == 4
    assert desc.expected_sector_sizes_for_track(1, 0) == [512, 1024, 2048, 8192]


def test_trs80_model2_layout_loads_mixed_density_geometry():
    desc = ensure_layout_loaded("tandy_trs80_model2_cpm_625k")
    assert desc.encoding == "mfm"
    assert desc.rpm_nominal == 360
    assert desc.tracks == 77
    assert desc.sides == 1
    assert desc.encoding_for_track(0, 0) == "fm"
    assert desc.expected_sectors_for_track(0, 0) == 26
    assert desc.encoding_for_track(1, 0) == "mfm"
    assert desc.expected_sectors_for_track(1, 0) == 8


def test_trs80_model2_flat_image_geometry_is_625920_bytes():
    desc = ensure_layout_loaded("tandy_trs80_model2_cpm_625k")
    data = bytes(625_920)
    tracks = sectors_from_blob(desc, data)
    assert tracks is not None
    assert len(tracks) == 77
    assert [len(tracks[0].sectors), len(tracks[1].sectors)] == [26, 8]
    assert len(tracks[0].sectors[0].data) == 128
    assert len(tracks[1].sectors[0].data) == 1024


def test_trs80_model2_16x512_layout_loads_distinct_geometry():
    desc = ensure_layout_loaded("tandy_trs80_model2_cpm_16x512_625k")
    assert desc.sectors_per_track == 16
    assert desc.sector_size == 512
    assert desc.encoding_for_track(0, 0) == "fm"
    assert desc.encoding_for_track(1, 0) == "mfm"
    tracks = sectors_from_blob(desc, bytes(625_920))
    assert tracks is not None
    assert [len(tracks[0].sectors), len(tracks[1].sectors)] == [26, 16]
