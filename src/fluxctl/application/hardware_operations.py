"""Hardware imaging operations shared by Fluxctl frontends."""
from __future__ import annotations

from pathlib import Path
from typing import Optional
from datetime import datetime, timezone
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile

from .compare_operations import compare_images
from .models import (
    GreaseweazleFormat,
    GreaseweazleStatus,
    HardwareReadResult,
    HardwareSynthesisResult,
    HardwareWriteResult,
)
from ..exceptions import FluxctlError
from ..output import validate_output_path


def _greaseweazle_executable() -> Optional[Path]:
    exe_name = "gw.exe" if sys.platform.startswith("win") else "gw"
    candidate = Path(sys.executable).parent / exe_name
    if candidate.exists():
        return candidate
    found = shutil.which("gw")
    return Path(found) if found else None


def greaseweazle_status():
    executable = _greaseweazle_executable()
    if executable is None:
        return GreaseweazleStatus(False, "", "Greaseweazle command `gw` was not found.", "Install Greaseweazle, then run fluxctl doctor.")
    return GreaseweazleStatus(True, str(executable), f"Greaseweazle command available at {executable}")


def greaseweazle_formats():
    executable = _greaseweazle_executable()
    if executable is None:
        return []
    completed = subprocess.run([str(executable), "read", "--help"], text=True, capture_output=True, check=False)
    formats: set[str] = set()
    active = False
    for line in f"{completed.stdout}\n{completed.stderr}".splitlines():
        stripped = line.strip()
        if stripped.startswith("FORMAT options:"):
            active = True
            continue
        if active and (not stripped or stripped.startswith("Supported file suffixes:")):
            break
        if active:
            formats.update(token for token in stripped.split() if "." in token and not token.startswith(".") and all(char.isalnum() or char in "._-" for char in token))
    return [GreaseweazleFormat(item, item) for item in sorted(formats)]


def _command_display(args: list[str]) -> str:
    return " ".join(shlex.quote(item) for item in args)


def _run_greaseweazle(args: list[str], action: str) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(args, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit status {completed.returncode}"
        raise FluxctlError(f"Greaseweazle {action} failed: {detail}")
    return completed


def _temporary_output(destination: Path) -> Path:
    """Reserve a sibling temporary name without exposing it to Greaseweazle."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=f".{destination.stem}.fluxctl-",
        suffix=destination.suffix,
        dir=destination.parent,
    )
    os.close(descriptor)
    temporary = Path(name)
    temporary.unlink()
    return temporary


def _publish_external_output(temporary: Path, destination: Path, *, overwrite: bool) -> None:
    """Atomically publish a Greaseweazle-generated sibling file."""

    try:
        if overwrite:
            os.replace(temporary, destination)
        else:
            try:
                os.link(temporary, destination)
            except FileExistsError as exc:
                raise FluxctlError(f"Output already exists: {destination}. Pass --force to replace it.") from exc
            temporary.unlink()
    finally:
        if temporary.exists():
            temporary.unlink()


def _normalise_scp_output(output: Path) -> Path:
    return output if output.suffix.lower() == ".scp" else output.with_suffix(".scp")


def synthesize_scp_with_greaseweazle(
    source: Path,
    output: Path,
    *,
    gw_format: str,
    tracks: str = "",
    overwrite: bool = False,
) -> HardwareSynthesisResult:
    """Generate a calibrated SCP from a sector image through Greaseweazle.

    SCP is a flux container. A result created here represents a fresh,
    format-defined encoding of the logical sectors; it does *not* preserve the
    source capture's analogue timing, weak bits, or copy protection.
    """

    if not gw_format.strip():
        raise FluxctlError("A Greaseweazle --format is required to synthesize an SCP image")
    if not source.exists():
        raise FluxctlError(f"Input image does not exist: {source}")
    output = _normalise_scp_output(output)
    validate_output_path(output, overwrite=overwrite, source_paths=[source])
    executable = _greaseweazle_executable()
    if executable is None:
        raise FluxctlError("Greaseweazle command `gw` is not available")
    temporary = _temporary_output(output)
    args = [str(executable), "convert", "--format", gw_format]
    if tracks:
        args.extend(["--tracks", tracks])
    args.extend([str(source), str(temporary)])
    try:
        completed = _run_greaseweazle(args, "conversion")
        _publish_external_output(temporary, output, overwrite=overwrite)
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise
    return HardwareSynthesisResult(
        path=str(output),
        command=args,
        command_display=_command_display(args),
        stdout=completed.stdout,
        stderr=completed.stderr,
        format_id=gw_format,
    )


def _write_manifest(path: Path, payload: dict[str, object], *, overwrite: bool, source: Path) -> None:
    from ..output import atomic_write_text

    atomic_write_text(
        path,
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        overwrite=overwrite,
        source_paths=[source],
    )


def write_and_verify_with_greaseweazle(
    source: Path,
    readback: Path,
    manifest: Path,
    *,
    drive: str,
    gw_format: str,
    layout: str,
    encoding: str = "mfm",
    tracks: str = "",
    readback_revs: int = 3,
    overwrite: bool = False,
    confirmed: bool = False,
) -> HardwareWriteResult:
    """Write a disk, retain a raw SCP read-back, and compare decoded sectors.

    Greaseweazle's write verification remains enabled. This then makes an
    independent raw SCP capture and compares it with the source using the
    caller-supplied Fluxctl layout. The source image is never changed.
    """

    if not confirmed:
        raise FluxctlError("Refusing destructive disk write without explicit confirmation")
    if not gw_format.strip():
        raise FluxctlError("A Greaseweazle --format is required for verified disk writing")
    if not layout.strip():
        raise FluxctlError("A Fluxctl --layout is required for read-back sector comparison")
    if readback_revs < 1:
        raise FluxctlError("Read-back revolutions must be 1 or greater")
    if not source.exists():
        raise FluxctlError(f"Input image does not exist: {source}")

    readback = _normalise_scp_output(readback)
    for path in (readback, manifest):
        validate_output_path(path, overwrite=overwrite, source_paths=[source])
    if readback.expanduser().resolve(strict=False) == manifest.expanduser().resolve(strict=False):
        raise FluxctlError("Read-back SCP and write manifest paths must be distinct")
    executable = _greaseweazle_executable()
    if executable is None:
        raise FluxctlError("Greaseweazle command `gw` is not available")

    source_hash = _sha256_file(source)
    write_args = [str(executable), "write", "--drive", drive, "--format", gw_format]
    if tracks:
        write_args.extend(["--tracks", tracks])
    # Deliberately omit --no-verify: gw's native post-write verification is required.
    write_args.append(str(source))
    readback_temporary = _temporary_output(readback)
    read_args = [
        str(executable),
        "read",
        "--drive",
        drive,
        "--raw",
        "--format",
        gw_format,
        "--revs",
        str(readback_revs),
    ]
    if tracks:
        read_args.extend(["--tracks", tracks])
    read_args.append(str(readback_temporary))
    report: dict[str, object] = {
        "schema_version": 1,
        "operation": "greaseweazle_write_verify",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": {"path": str(source), "sha256": source_hash},
        "drive": drive,
        "format": gw_format,
        "layout": layout,
        "encoding": encoding,
        "tracks": tracks,
        "readback_revs": readback_revs,
        "write": {"command": write_args, "command_display": _command_display(write_args)},
        "readback": {"path": str(readback), "command": read_args, "command_display": _command_display(read_args)},
    }
    try:
        write_completed = _run_greaseweazle(write_args, "write")
        report["write"] = {
            **dict(report["write"]),
            "verified_by_greaseweazle": True,
            "stdout": write_completed.stdout,
            "stderr": write_completed.stderr,
        }
        read_completed = _run_greaseweazle(read_args, "read-back")
        _publish_external_output(readback_temporary, readback, overwrite=overwrite)
        report["readback"] = {
            **dict(report["readback"]),
            "sha256": _sha256_file(readback),
            "stdout": read_completed.stdout,
            "stderr": read_completed.stderr,
        }
        comparison = compare_images(
            source,
            readback,
            layout_a=layout,
            layout_b=layout,
            encoding_a=encoding,
            encoding_b=encoding,
        ).report
        report["comparison"] = comparison
        report["success"] = bool(comparison.get("identical"))
        _write_manifest(manifest, report, overwrite=overwrite, source=source)
    except Exception as exc:
        if readback_temporary.exists():
            readback_temporary.unlink()
        report["success"] = False
        report["error"] = str(exc)
        try:
            _write_manifest(manifest, report, overwrite=overwrite, source=source)
        except Exception:
            pass
        raise
    return HardwareWriteResult(
        source_path=str(source),
        readback_path=str(readback),
        manifest_path=str(manifest),
        write_command=write_args,
        write_command_display=_command_display(write_args),
        write_stdout=write_completed.stdout,
        write_stderr=write_completed.stderr,
        read_command=read_args,
        read_command_display=_command_display(read_args),
        read_stdout=read_completed.stdout,
        read_stderr=read_completed.stderr,
        comparison=comparison,
    )


def _sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_disk_with_greaseweazle(
    output: Path,
    *,
    drive: str = "A",
    gw_format: str = "",
    tracks: str = "",
    revs: Optional[int] = None,
    overwrite: bool = False,
):
    if output.suffix.lower() != ".scp":
        output = output.with_suffix(".scp")
    executable = _greaseweazle_executable()
    if executable is None:
        raise FluxctlError("Greaseweazle command `gw` is not available")
    if output.exists() and not overwrite:
        raise FileExistsError(f"Output image already exists: {output}")
    validate_output_path(output, overwrite=overwrite)
    args = [str(executable), "read", "--drive", drive, "--raw"]
    if gw_format:
        args.extend(["--format", gw_format])
    if tracks:
        args.extend(["--tracks", tracks])
    if revs is not None:
        if revs < 1:
            raise ValueError("Greaseweazle read revolutions must be 1 or greater")
        args.extend(["--revs", str(revs)])
    args.append(str(output))
    completed = subprocess.run(args, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit status {completed.returncode}"
        raise FluxctlError(f"Greaseweazle read failed: {detail}")
    return HardwareReadResult(str(output), args, " ".join(shlex.quote(item) for item in args), completed.stdout, completed.stderr)
