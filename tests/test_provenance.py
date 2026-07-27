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


def test_compare_json_writes_provenance(tmp_path):
    report = tmp_path / "compare.json"
    result = runner.invoke(app, ["compare", str(FIXTURE_IMG), str(FIXTURE_IMG), "--json-out", str(report)])

    assert result.exit_code == 0, result.output
    prov = _load_prov(report.with_suffix(report.suffix + ".provenance.json"))
    assert prov["operation"] == "compare"
    assert prov["output_sha256"] == ProvenanceRecord.sha256_file(report)
    assert prov["parameters"]["json_out"] == str(report)
    assert any(entry.startswith("decoded_sha256_a=") for entry in prov["evidence"])


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


def test_patch_respects_provenance_override(tmp_path):
    out = tmp_path / "patched.img"
    prov_out = tmp_path / "custom.patch.provenance.json"
    payload = "AA" * 512

    result = runner.invoke(
        app,
        [
            "patch",
            str(FIXTURE_SCP),
            "--layout",
            "ibm_mfm_180k",
            "--write-sector",
            f"0:1:{payload}",
            "--out",
            str(out),
            "--prov-out",
            str(prov_out),
        ],
    )

    assert result.exit_code == 0, result.output
    prov = _load_prov(prov_out)
    assert prov["operation"] == "patch"
    assert prov["output_path"].endswith("patched.img")


def test_help_mentions_real_world_examples() -> None:
    convert_help = runner.invoke(app, ["convert", "--help"])
    patch_help = runner.invoke(app, ["patch", "--help"])
    compare_help = runner.invoke(app, ["compare", "--help"])

    assert convert_help.exit_code == 0
    assert "disk.img --layout ibm_mfm_720k --to imd" in convert_help.output
    assert patch_help.exit_code == 0
    assert "T:H:S:HEX" in patch_help.output
    assert compare_help.exit_code == 0
    assert "--prov-out" in compare_help.output
