import json
from pathlib import Path

from typer.testing import CliRunner

from fluxctl.cli import app


runner = CliRunner()


def test_compare_identical_images(tmp_path: Path) -> None:
    img = tmp_path / "disk.img"
    img.write_bytes(b"\x00\x01\x02\x03")

    result = runner.invoke(app, ["compare", str(img), str(img)])

    assert result.exit_code == 0
    assert "MATCH" in result.output


def test_compare_reports_first_difference(tmp_path: Path) -> None:
    a = tmp_path / "a.img"
    b = tmp_path / "b.img"
    json_out = tmp_path / "report.json"
    a.write_bytes(b"\xAA\xBB\xCC")
    b.write_bytes(b"\xAA\x00\xCC")

    result = runner.invoke(app, ["compare", str(a), str(b), "--json-out", str(json_out)])

    assert result.exit_code == 1
    assert json_out.exists()
    report = json.loads(json_out.read_text())
    assert report["identical"] is False
    assert report["first_diff_offset"] == 1
