from pathlib import Path

import pytest
from typer.testing import CliRunner

from fluxctl import cli
from fluxctl.exceptions import SCPFormatError
from fluxctl.scp import parse_scp


def _write_minimal_scp(path: Path) -> None:
    header = bytearray(16)
    header[:3] = b"SCP"
    header[3] = 0x10
    header[6:8] = (1).to_bytes(2, "little")
    header[8] = 0
    header[9] = 0
    header[14:16] = (25).to_bytes(2, "little")
    path.write_bytes(header)


def test_parse_scp_rejects_invalid_magic(tmp_path: Path) -> None:
    bad = tmp_path / "bad.scp"
    bad.write_bytes(b"BADFORMAT")
    with pytest.raises(SCPFormatError):
        parse_scp(bad)


def test_ensure_layout_error_surfaces_in_cli(tmp_path: Path) -> None:
    image = tmp_path / "empty.scp"
    _write_minimal_scp(image)
    runner = CliRunner()
    result = runner.invoke(cli.app, ["sectors", str(image), "--layout", "missing"])
    assert result.exit_code == 1
    assert "Unknown layout" in result.output
