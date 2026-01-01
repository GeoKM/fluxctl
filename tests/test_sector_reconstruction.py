from pathlib import Path

import pytest

from fluxctl.decoding.mfm import mfm_decoder
from fluxctl.scp import parse_scp
from fluxctl.sector.reconstruct import build_track_sectors


FIXTURES = [
    (
        Path("tests/fixtures/5.25inch/IBM/IBM-Generic-SSDD-MFM-IBMPC-180K.scp"),
        9,
    ),
    (
        Path("tests/fixtures/3.5inch/IBM/IBM-Generic-DSDD-MFM-IBMPC-720K.scp"),
        9,
    ),
    (
        Path("tests/fixtures/3.5inch/IBM/IBM-Generic-DSHD-MFM-IBMPC-1440K.scp"),
        18,
    ),
]


@pytest.mark.parametrize("path,expected_count", FIXTURES)
def test_reconstructs_mfm_track_zero(path: Path, expected_count: int) -> None:
    scp = parse_scp(path)
    track0 = next((t for t in scp.tracks if t.track == 0 and t.side == 0), None)
    assert track0 is not None, "Fixture missing track 0/side 0"
    assert track0.revolutions, "Expected at least one revolution in fixture"

    track_sectors = build_track_sectors(
        track0.revolutions[0], mfm_decoder, cylinder=track0.track, head=track0.side, expected_sectors=expected_count
    )

    assert len(track_sectors.sectors) == expected_count
    sector_ids = sorted(sec.sector_id for sec in track_sectors.sectors)
    assert sector_ids == list(range(1, expected_count + 1))
    assert all(len(sec.data) == (128 << sec.size_code) for sec in track_sectors.sectors)
