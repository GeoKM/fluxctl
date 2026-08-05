from pathlib import Path

from fluxctl.trs80 import load_trs80_image


FIXTURE_DIR = Path("tests/fixtures/5.25inch/TANDY")
FIXTURE_JV3 = FIXTURE_DIR / "Tandy-Model4-SSDD-MFM-CPM22-180K.dsk"
FIXTURE_DMK = FIXTURE_DIR / "Tandy-Model4-SSDD-MFM-CPMPlus-156K.dsk"


def test_loads_jv3_dsk_sector_table() -> None:
    tracks, geometry, metadata = load_trs80_image(FIXTURE_JV3)

    assert metadata["format"] == "jv3"
    assert geometry.tracks == 40
    assert geometry.heads == 1
    assert geometry.spt == 18
    assert geometry.sector_size == 256
    assert sum(len(track.sectors) for track in tracks) == 720


def test_loads_dmk_mixed_cpm_plus_tracks() -> None:
    tracks, geometry, metadata = load_trs80_image(FIXTURE_DMK)

    assert metadata["format"] == "dmk"
    assert geometry.tracks == 40
    assert geometry.heads == 1
    assert geometry.spt == 18
    assert geometry.sector_size == 512
    track0 = next(track for track in tracks if track.track == 0)
    track1 = next(track for track in tracks if track.track == 1)
    assert len(track0.sectors) == 18
    assert {sector.size for sector in track0.sectors} == {256}
    assert len(track1.sectors) == 8
    assert {sector.size for sector in track1.sectors} == {512}


def test_explicit_dmk_suffix_is_accepted(tmp_path: Path) -> None:
    copied = tmp_path / "tandy.dmk"
    copied.write_bytes(FIXTURE_DMK.read_bytes())

    tracks, _geometry, metadata = load_trs80_image(copied)

    assert metadata["format"] == "dmk"
    assert tracks
