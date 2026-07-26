import pytest

from fluxctl.cli import _apply_sector_patch, _parse_write_sector_spec
from fluxctl.exceptions import FluxctlError
from fluxctl.sector.models import Sector, TrackSectors


def test_parse_write_sector_spec_accepts_track_sector_hex() -> None:
    track, sector, payload = _parse_write_sector_spec("12:3:DEADBEEF")

    assert track == 12
    assert sector == 3
    assert payload == b"\xDE\xAD\xBE\xEF"


@pytest.mark.parametrize("spec", ["12:3", "12::DEAD", ":3:DEAD", "12:3:"])
def test_parse_write_sector_spec_rejects_malformed_values(spec: str) -> None:
    with pytest.raises(ValueError, match="Expected T:S:HEX"):
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

    _apply_sector_patch([TrackSectors(track=0, head=0, sectors=[sector])], 0, 1, b"\xAA" * 256)

    assert sector.data == b"\xAA" * 256
    assert sector.crc_ok is True
    assert sector.confidence == 1.0


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
        _apply_sector_patch([TrackSectors(track=0, head=0, sectors=[sector])], 0, 1, b"\xAA")


def test_apply_sector_patch_rejects_missing_sector() -> None:
    with pytest.raises(FluxctlError, match="not found"):
        _apply_sector_patch([TrackSectors(track=0, head=0, sectors=[])], 0, 1, b"\xAA")
