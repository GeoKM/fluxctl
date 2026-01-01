import json
from datetime import datetime
from pathlib import Path

from typer.testing import CliRunner

from fluxctl.cli import app
from fluxctl.models import ProvenanceRecord

runner = CliRunner()


FIXTURE_SCP = Path("tests/fixtures/5.25inch/IBM/IBM-Generic-SSDD-MFM-IBMPC-180K.scp")
FIXTURE_IMG = Path("tests/fixtures/3.5inch/IBM/IBM-Generic-DSDD-MFM-IBMPC-720K.img")


def _load_prov(path: Path) -> dict:
    assert path.exists(), f"missing provenance at {path}"
    data = json.loads(path.read_text())
    datetime.fromisoformat(data["timestamp"])
    return data


def test_convert_writes_provenance(tmp_path):
    out = tmp_path / "disk.img"
    result = runner.invoke(app, ["convert", str(FIXTURE_IMG), "--to", "raw", "--out", str(out)])
    assert result.exit_code == 0, result.output
    prov = _load_prov(out.with_suffix(out.suffix + ".provenance.json"))
    assert prov["operation"] == "convert"
    assert prov["input_sha256"] == ProvenanceRecord.sha256_file(FIXTURE_IMG)
    assert prov["output_sha256"] == ProvenanceRecord.sha256_file(out)


def test_qc_provenance(tmp_path):
    json_out = tmp_path / "report.json"
    result = runner.invoke(
        app,
        ["qc", str(FIXTURE_SCP), "--encoding", "mfm", "--json-out", str(json_out)],
    )
    assert result.exit_code == 0, result.output
    prov = _load_prov(json_out.with_suffix(json_out.suffix + ".provenance.json"))
    assert prov["operation"] == "qc"
    assert prov["output_path"].endswith("report.json")


def test_visualize_and_extract_provenance(tmp_path):
    vis_out = tmp_path / "map.txt"
    vis_result = runner.invoke(
        app, ["visualize", str(FIXTURE_SCP), "--format", "ascii", "--out", str(vis_out)]
    )
    assert vis_result.exit_code == 0, vis_result.output
    vis_prov = _load_prov(vis_out.with_suffix(".txt.provenance.json"))
    assert vis_prov["operation"] == "visualize"

    raw_image = tmp_path / "raw.img"
    raw_image.write_bytes(b"\x00" * 1024)
    ext_result = runner.invoke(app, ["extract", str(raw_image), "--out", str(tmp_path / "dump.bin")])
    assert ext_result.exit_code == 0, ext_result.output
    ext_prov = _load_prov(tmp_path / "dump.bin.provenance.json")
    assert ext_prov["operation"] == "extract"
    assert ext_prov["output_sha256"] == ProvenanceRecord.sha256_file(tmp_path / "dump.bin")
