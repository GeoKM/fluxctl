from pathlib import Path

from fluxctl.layouts.loader import ensure_layout_loaded


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
