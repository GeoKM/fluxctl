from pathlib import Path
import json

import pytest

from fluxctl.scp import parse_scp
from fluxctl.decoding.mfm import MFMDecoder
from fluxctl.reports.qc import build_qc_report
from fluxctl.models import LayoutDescriptor


@pytest.fixture
def amiga_layout():
    path = Path("src/fluxctl/data/layouts/amiga_mfm_880k.json")
    return LayoutDescriptor(**json.loads(path.read_text()))


def test_amiga_pll_fallback(monkeypatch, amiga_layout):
    """Ensure Amiga QC succeeds when Greaseweazle is unavailable."""

    # Force Greaseweazle path to be bypassed.
    import fluxctl.sector.reconstruct_amiga as amiga

    monkeypatch.setattr(amiga, "reconstruct_amiga_greaseweazle", lambda *_, **__: None)

    image = parse_scp(Path("tests/fixtures/3.5inch/Commodore/Commodore-1010-DSDD-MFM-Amiga-880K.scp"))
    report = build_qc_report(image, MFMDecoder(), layout=amiga_layout)

    good_total = sum(t.good_sectors for t in report.tracks)
    assert good_total >= amiga_layout.sectors_per_track  # at least one full side
    for track in report.tracks:
        if track.good_sectors:
            assert track.good_sectors == amiga_layout.sectors_per_track
            assert track.missing_sectors == 0
            assert track.crc_errors == 0
