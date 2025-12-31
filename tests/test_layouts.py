from pathlib import Path

from fluxctl.layouts.loader import ensure_layout_loaded


def test_layout_loads():
    desc = ensure_layout_loaded("ibm_mfm_1440k")
    assert desc.layout_id == "ibm_mfm_1440k"
    assert desc.sectors_per_track == 18
