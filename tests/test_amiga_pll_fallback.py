from pathlib import Path
import json

import pytest

from fluxctl.scp import parse_scp
from fluxctl.decoding.mfm import MFMDecoder
from fluxctl.reports.qc import build_qc_report
from fluxctl.models import LayoutDescriptor, RevolutionFlux


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
    assert good_total >= amiga_layout.sectors_per_track  # at least one track decodes
    for track in report.tracks:
        if track.good_sectors:
            assert track.good_sectors >= 8  # allow a few misses in fallback path


def test_amiga_greaseweazle_flux_uses_index_cued_multi_revolution_timing():
    pytest.importorskip("greaseweazle")
    from greaseweazle.flux import Flux
    from fluxctl.sector.reconstruct_amiga import _greaseweazle_flux_from_revolutions

    revolutions = [
        RevolutionFlux(index=0, interval_ns=[25, 50, 75], index_time_ns=200),
        RevolutionFlux(index=1, interval_ns=[100, 125], index_time_ns=250),
    ]

    flux = _greaseweazle_flux_from_revolutions(Flux, revolutions, timebase_ns=25.0)

    assert flux is not None
    assert flux.list == [1, 2, 3, 4, 5]
    assert flux.index_list == [8, 10]
    assert flux.sample_freq == 40_000_000
    assert flux.index_cued is True
