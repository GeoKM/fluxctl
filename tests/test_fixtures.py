from pathlib import Path

import pytest

from fluxctl.fixtures import FixtureDiscoveryError, FixtureDescriptor, discover_fixtures


FIXTURE_ROOT = Path(__file__).parent / "fixtures"


def test_discover_fixtures_loads_metadata() -> None:
    fixtures = discover_fixtures(FIXTURE_ROOT)
    assert fixtures, "Expected at least one fixture to be discovered"
    fixture = next(
        fd for fd in fixtures if fd.path.name == "IBM-Generic-DSHD-MFM-IBMPC-1440K.scp"
    )
    assert fixture.manufacturer == "IBM"
    assert fixture.drive_style == "Generic"
    assert fixture.sides_density == "DSHD"
    assert fixture.encoding == "MFM"
    assert fixture.os_name == "IBMPC"
    assert fixture.approx_capacity == "1440K"
    assert fixture.metadata == {}


def test_discover_fixtures_rejects_unknown_root(tmp_path: Path) -> None:
    with pytest.raises(FixtureDiscoveryError):
        discover_fixtures(tmp_path / "missing")


def test_discover_fixtures_ignores_malformed_entries(tmp_path: Path) -> None:
    malformed = tmp_path / "DEC-RX02-DSDD-MFM.scp"
    malformed.write_bytes(b"dummy")
    good = tmp_path / "IBM-Generic-DSDD-MFM-IBMPC-360K.scp"
    good.write_bytes(b"dummy")

    fixtures = discover_fixtures(tmp_path)

    assert len(fixtures) == 1
    assert fixtures[0].path == good


def test_fixture_parsing_rejects_short_names(tmp_path: Path) -> None:
    bad = tmp_path / "bad.scp"
    bad.write_bytes(b"dummy")
    with pytest.raises(FixtureDiscoveryError):
        FixtureDescriptor.from_path(bad)
