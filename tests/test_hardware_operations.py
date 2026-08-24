from pathlib import Path
from types import SimpleNamespace
import json

import pytest

from fluxctl.exceptions import FluxctlError
from fluxctl.application import hardware_operations as hardware


def test_synthesize_scp_uses_greaseweazle_convert_and_publishes_atomically(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "disk.img"
    source.write_bytes(b"sector-image")
    output = tmp_path / "disk.scp"
    executable = tmp_path / "gw"
    executable.write_text("")
    seen: list[list[str]] = []
    monkeypatch.setattr(hardware, "_greaseweazle_executable", lambda: executable)

    def fake_run(args, **_kwargs):
        seen.append(args)
        Path(args[-1]).write_bytes(b"synthesized-scp")
        return SimpleNamespace(returncode=0, stdout="converted", stderr="")

    monkeypatch.setattr(hardware.subprocess, "run", fake_run)

    result = hardware.synthesize_scp_with_greaseweazle(source, output, gw_format="ibm.720")

    assert result.path == str(output)
    assert output.read_bytes() == b"synthesized-scp"
    assert seen[0][1:4] == ["convert", "--format", "ibm.720"]
    assert seen[0][-2] == str(source)
    assert seen[0][-1] != str(output)


def test_verified_write_requires_explicit_confirmation(tmp_path: Path) -> None:
    source = tmp_path / "disk.img"
    source.write_bytes(b"sector-image")

    with pytest.raises(FluxctlError, match="explicit confirmation"):
        hardware.write_and_verify_with_greaseweazle(
            source,
            tmp_path / "readback.scp",
            tmp_path / "manifest.json",
            drive="A",
            gw_format="ibm.720",
            layout="ibm_mfm_720k",
        )


def test_verified_write_keeps_gw_verify_readback_and_manifest(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "disk.img"
    source.write_bytes(b"sector-image")
    readback = tmp_path / "readback.scp"
    manifest = tmp_path / "write.json"
    executable = tmp_path / "gw"
    executable.write_text("")
    commands: list[list[str]] = []
    monkeypatch.setattr(hardware, "_greaseweazle_executable", lambda: executable)

    def fake_run(args, **_kwargs):
        commands.append(args)
        if args[1] == "read":
            Path(args[-1]).write_bytes(b"readback-scp")
        return SimpleNamespace(returncode=0, stdout=f"{args[1]} ok", stderr="")

    monkeypatch.setattr(hardware.subprocess, "run", fake_run)
    monkeypatch.setattr(
        hardware,
        "compare_images",
        lambda *_args, **_kwargs: SimpleNamespace(report={"identical": True, "first_diff_offset": None}),
    )

    result = hardware.write_and_verify_with_greaseweazle(
        source,
        readback,
        manifest,
        drive="A",
        gw_format="ibm.720",
        layout="ibm_mfm_720k",
        tracks="c=0-79:h=0-1",
        readback_revs=4,
        confirmed=True,
    )

    assert commands[0][1] == "write"
    assert "--no-verify" not in commands[0]
    assert commands[1][1:6] == ["read", "--drive", "A", "--raw", "--format"]
    assert "--revs" in commands[1]
    assert result.comparison["identical"] is True
    assert readback.read_bytes() == b"readback-scp"
    saved = json.loads(manifest.read_text())
    assert saved["success"] is True
    assert saved["write"]["verified_by_greaseweazle"] is True
    assert saved["comparison"]["identical"] is True
