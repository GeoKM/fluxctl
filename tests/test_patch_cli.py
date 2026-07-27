from pathlib import Path

import pytest
from typer.testing import CliRunner

from fluxctl.cli import _apply_sector_patch, _parse_write_sector_spec, app
from fluxctl.exceptions import FluxctlError
from fluxctl.sector.models import Sector, TrackSectors


runner = CliRunner()
FIXTURE = Path("tests/fixtures/3.5inch/IBM/IBM-Generic-DSDD-MFM-IBMPC-720K.scp")
LAYOUT = "ibm_mfm_720k"


def test_parse_write_sector_spec_accepts_track_sector_hex() -> None:
    track, head, sector, payload = _parse_write_sector_spec("12:3:DEADBEEF")

    assert track == 12
    assert head is None
    assert sector == 3
    assert payload == b"\xDE\xAD\xBE\xEF"


def test_parse_write_sector_spec_accepts_track_head_sector_hex() -> None:
    track, head, sector, payload = _parse_write_sector_spec("12:1:3:DEADBEEF")

    assert track == 12
    assert head == 1
    assert sector == 3
    assert payload == b"\xDE\xAD\xBE\xEF"


@pytest.mark.parametrize("spec", ["12:3", "12::DEAD", ":3:DEAD", "12:3:", "12::3:DEAD"])
def test_parse_write_sector_spec_rejects_malformed_values(spec: str) -> None:
    with pytest.raises(ValueError, match="Expected T:S:HEX or T:H:S:HEX"):
        _parse_write_sector_spec(spec)


def test_parse_write_sector_spec_rejects_invalid_hex() -> None:
    with pytest.raises(ValueError):
        _parse_write_sector_spec("12:3:NOTHEX")


def test_apply_sector_patch_updates_full_sector_payload() -> None:
    sector = Sector(
        cylinder=0,
        head=0,
        sector_id=1,
        size_code=1,
        data=b"\x00" * 256,
        crc_ok=False,
        confidence=0.0,
    )

    _apply_sector_patch([TrackSectors(track=0, head=0, sectors=[sector])], 0, None, 1, b"\xAA" * 256)

    assert sector.data == b"\xAA" * 256
    assert sector.crc_ok is True
    assert sector.confidence == 1.0


def test_apply_sector_patch_updates_requested_head_only() -> None:
    sector_head_0 = Sector(
        cylinder=0,
        head=0,
        sector_id=1,
        size_code=1,
        data=b"\x00" * 256,
        crc_ok=True,
        confidence=1.0,
    )
    sector_head_1 = Sector(
        cylinder=0,
        head=1,
        sector_id=1,
        size_code=1,
        data=b"\x11" * 256,
        crc_ok=True,
        confidence=1.0,
    )
    tracks = [
        TrackSectors(track=0, head=0, sectors=[sector_head_0]),
        TrackSectors(track=0, head=1, sectors=[sector_head_1]),
    ]

    _apply_sector_patch(tracks, 0, 1, 1, b"\xAA" * 256)

    assert sector_head_0.data == b"\x00" * 256
    assert sector_head_1.data == b"\xAA" * 256


def test_apply_sector_patch_rejects_ambiguous_headless_target() -> None:
    tracks = [
        TrackSectors(
            track=0,
            head=0,
            sectors=[Sector(0, 0, 1, 1, b"\x00" * 256, True, 1.0)],
        ),
        TrackSectors(
            track=0,
            head=1,
            sectors=[Sector(0, 1, 1, 1, b"\x11" * 256, True, 1.0)],
        ),
    ]

    with pytest.raises(FluxctlError, match="matches multiple heads"):
        _apply_sector_patch(tracks, 0, None, 1, b"\xAA" * 256)


def test_apply_sector_patch_rejects_partial_payload() -> None:
    sector = Sector(
        cylinder=0,
        head=0,
        sector_id=1,
        size_code=1,
        data=b"\x00" * 256,
        crc_ok=True,
        confidence=1.0,
    )

    with pytest.raises(FluxctlError, match="requires 256 bytes"):
        _apply_sector_patch([TrackSectors(track=0, head=0, sectors=[sector])], 0, None, 1, b"\xAA")


def test_apply_sector_patch_rejects_missing_sector() -> None:
    with pytest.raises(FluxctlError, match="not found"):
        _apply_sector_patch([TrackSectors(track=0, head=0, sectors=[])], 0, None, 1, b"\xAA")


def test_patch_cli_rejects_ambiguous_headless_sector(tmp_path: Path) -> None:
    out = tmp_path / "patched.img"

    result = runner.invoke(
        app,
        [
            "patch",
            str(FIXTURE),
            "--layout",
            LAYOUT,
            "--write-sector",
            f"0:1:{'AA' * 512}",
            "--out",
            str(out),
        ],
    )

    assert result.exit_code == 1
    assert "matches multiple heads" in result.output
    assert not out.exists()


def test_patch_cli_targets_requested_head(tmp_path: Path) -> None:
    out = tmp_path / "patched.img"
    patched_sector = b"\xAA" * 512

    result = runner.invoke(
        app,
        [
            "patch",
            str(FIXTURE),
            "--layout",
            LAYOUT,
            "--write-sector",
            f"0:1:1:{patched_sector.hex()}",
            "--out",
            str(out),
        ],
    )

    assert result.exit_code == 0, result.output
    data = out.read_bytes()
    assert data[:512] != patched_sector
    assert data[9 * 512 : 10 * 512] == patched_sector
