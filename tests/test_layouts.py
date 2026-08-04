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
