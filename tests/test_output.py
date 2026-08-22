from pathlib import Path

import pytest
from typer.testing import CliRunner

from fluxctl.cli import app
from fluxctl.exceptions import FluxctlError, OutputExistsError
from fluxctl.output import atomic_write_bytes, atomic_write_text


def test_atomic_write_refuses_existing_output_and_leaves_it_unchanged(tmp_path: Path) -> None:
    output = tmp_path / "result.bin"
    output.write_bytes(b"original")

    with pytest.raises(OutputExistsError, match="already exists"):
        atomic_write_bytes(output, b"replacement")

    assert output.read_bytes() == b"original"
    assert list(tmp_path.iterdir()) == [output]


def test_atomic_write_replaces_existing_output_only_when_requested(tmp_path: Path) -> None:
    output = tmp_path / "result.txt"
    output.write_text("original", encoding="utf-8")

    atomic_write_text(output, "replacement", overwrite=True)

    assert output.read_text(encoding="utf-8") == "replacement"
    assert list(tmp_path.iterdir()) == [output]


def test_atomic_write_never_replaces_an_input_path(tmp_path: Path) -> None:
    source = tmp_path / "disk.img"
    source.write_bytes(b"source")

    with pytest.raises(FluxctlError, match="must differ from input"):
        atomic_write_bytes(source, b"replacement", overwrite=True, source_paths=[source])

    assert source.read_bytes() == b"source"


def test_convert_requires_force_and_preflights_provenance(tmp_path: Path) -> None:
    source = tmp_path / "source.img"
    source.write_bytes(b"\x00" * 737_280)
    output = tmp_path / "converted.img"
    provenance = output.with_suffix(output.suffix + ".provenance.json")
    output.write_bytes(b"existing image")
    provenance.write_text("existing provenance", encoding="utf-8")
    runner = CliRunner()
    args = [
        "convert",
        str(source),
        "--layout",
        "ibm_mfm_720k",
        "--to",
        "raw",
        "--out",
        str(output),
    ]

    refused = runner.invoke(app, args)

    assert refused.exit_code == 1
    assert "Pass --force" in refused.output
    assert output.read_bytes() == b"existing image"
    assert provenance.read_text(encoding="utf-8") == "existing provenance"

    output.unlink()
    refused_sidecar = runner.invoke(app, args)

    assert refused_sidecar.exit_code == 1
    assert "Pass --force" in refused_sidecar.output
    assert not output.exists()
    assert provenance.read_text(encoding="utf-8") == "existing provenance"

    replaced = runner.invoke(app, [*args, "--force"])

    assert replaced.exit_code == 0, replaced.output
    assert output.stat().st_size == 737_280
    assert "existing provenance" not in provenance.read_text(encoding="utf-8")


def test_convert_rejects_input_as_output_even_with_force(tmp_path: Path) -> None:
    source = tmp_path / "source.img"
    original = b"\x00" * 737_280
    source.write_bytes(original)

    result = CliRunner().invoke(
        app,
        [
            "convert",
            str(source),
            "--layout",
            "ibm_mfm_720k",
            "--to",
            "raw",
            "--out",
            str(source),
            "--force",
        ],
    )

    assert result.exit_code == 1
    assert "must differ from input" in result.output
    assert source.read_bytes() == original
