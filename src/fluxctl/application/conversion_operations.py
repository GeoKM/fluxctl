"""Image conversion operations shared by Fluxctl frontends.

The conversion implementation still lives in the legacy CLI module during the
incremental refactor, but callers use this typed boundary instead of spawning
the CLI process or importing command handlers directly.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .. import __version__


@dataclass(frozen=True)
class ConversionResult:
    """Observable result of a completed conversion."""

    input_path: Path
    output_path: Path
    exporter: str
    output_size: int
    output_sha256: str
    lossy_warning: bool


@dataclass(frozen=True)
class RoundtripResult:
    """Decoded comparison result for a two-leg conversion check."""

    report: dict[str, object]
    roundtrip_match: bool


def convert_image(
    path: Path,
    output: Path,
    exporter: str,
    layout: Optional[str],
    encoding: str,
    *,
    prov_out: Optional[Path] = None,
    force: bool = False,
) -> ConversionResult:
    """Convert an image using the same core and output policy as the CLI."""

    # Imports stay lazy so importing the application layer does not initialize
    # Typer or the complete decoder/exporter registry for unrelated operations.
    from .. import cli

    result = cli._prepare_convert_payload(path, exporter, layout, encoding)
    provenance_path = prov_out or output.with_suffix(output.suffix + ".provenance.json")
    cli._validate_outputs([output, provenance_path], force=force, source_paths=[path])
    cli.atomic_write_bytes(output, result.payload, overwrite=force, source_paths=[path])
    provenance = cli.ProvenanceRecord(
        tool_name="fluxctl",
        tool_version=__version__,
        operation="convert",
        input_path=path,
        input_sha256=cli.sha256_file(path),
        output_path=output,
        output_sha256=hashlib.sha256(result.payload).hexdigest(),
        parameters={
            "layout": layout or "",
            "resolved_layout": result.layout_id,
            "encoding": result.encoding,
            "exporter": exporter,
            "output": str(output),
        },
        plugins={
            "exporter": result.exporter_name,
            "exporter_version": result.exporter_version,
            "decoder": result.encoding,
        },
        decoder=result.encoding,
        encoder=exporter,
    )
    cli.write_provenance(provenance, provenance_path, overwrite=force)
    return ConversionResult(
        input_path=path,
        output_path=output,
        exporter=exporter,
        output_size=len(result.payload),
        output_sha256=hashlib.sha256(result.payload).hexdigest(),
        lossy_warning=cli._is_lossy(result.track_data, result.exporter_metadata),
    )


def roundtrip_image(
    path: Path,
    to: str,
    back_to: Optional[str],
    layout: Optional[str],
    encoding: str,
    *,
    work_dir: Optional[Path] = None,
    json_out: Optional[Path] = None,
    prov_out: Optional[Path] = None,
    force: bool = False,
) -> RoundtripResult:
    """Run a decoded sector round trip without spawning the CLI process."""

    from .. import cli

    back_exporter = back_to or cli._infer_roundtrip_back_exporter(path)
    temp_context = tempfile.TemporaryDirectory(prefix="fluxctl-roundtrip-") if work_dir is None else None
    base_dir = Path(temp_context.name) if temp_context is not None else work_dir
    assert base_dir is not None
    base_dir.mkdir(parents=True, exist_ok=True)
    try:
        first_path = base_dir / f"{path.stem}-to-{to}{cli._exporter_suffix(to)}"
        final_path = base_dir / f"{path.stem}-roundtrip-{back_exporter}{cli._exporter_suffix(back_exporter)}"
        prov_target = (prov_out or json_out.with_suffix(json_out.suffix + ".provenance.json")) if json_out else None
        retained_outputs = [first_path, final_path] if work_dir is not None else []
        report_outputs = [output for output in (json_out, prov_target) if output is not None]
        cli._validate_outputs([*retained_outputs, *report_outputs], force=force, source_paths=[path])
        intermediate_overwrite = force if work_dir is not None else False

        first = cli._prepare_convert_payload(path, to, layout, encoding)
        cli.atomic_write_bytes(first_path, first.payload, overwrite=intermediate_overwrite, source_paths=[path])
        resolved_layout = first.layout_id or layout
        original_bytes, original_meta = cli._image_bytes_for_compare(path, resolved_layout, first.encoding)
        first_bytes, first_meta = cli._image_bytes_for_compare(first_path, resolved_layout, first.encoding)
        second = cli._prepare_convert_payload(first_path, back_exporter, resolved_layout, first.encoding)
        cli.atomic_write_bytes(final_path, second.payload, overwrite=intermediate_overwrite, source_paths=[path])
        final_bytes, final_meta = cli._image_bytes_for_compare(final_path, resolved_layout, first.encoding)

        original_sha = hashlib.sha256(original_bytes).hexdigest()
        first_sha = hashlib.sha256(first_bytes).hexdigest()
        final_sha = hashlib.sha256(final_bytes).hexdigest()
        forward_diff = cli._first_diff_offset(original_bytes, first_bytes)
        final_diff = cli._first_diff_offset(original_bytes, final_bytes)
        forward_match = forward_diff is None and len(original_bytes) == len(first_bytes)
        roundtrip_match = final_diff is None and len(original_bytes) == len(final_bytes)
        lossy = cli._is_lossy(first.track_data, first.exporter_metadata) or cli._is_lossy(second.track_data, second.exporter_metadata)
        report = {
            "input": str(path), "to": to, "back_to": back_exporter,
            "layout": resolved_layout or "", "encoding": first.encoding,
            "work_dir": str(base_dir) if work_dir is not None else "",
            "first_path": str(first_path) if work_dir is not None else "",
            "final_path": str(final_path) if work_dir is not None else "",
            "original_sha256": original_sha, "first_sha256": first_sha, "final_sha256": final_sha,
            "original_length": len(original_bytes), "first_length": len(first_bytes), "final_length": len(final_bytes),
            "forward_match": forward_match, "roundtrip_match": roundtrip_match,
            "forward_first_diff_offset": forward_diff, "roundtrip_first_diff_offset": final_diff,
            "lossy_warning": lossy,
            "meta": {"original": original_meta, "first": first_meta, "final": final_meta},
        }
        if json_out:
            cli.atomic_write_text(json_out, json.dumps(report, indent=2), overwrite=force, source_paths=[path])
            assert prov_target is not None
            record = cli.ProvenanceRecord(
                tool_name="fluxctl", tool_version=__version__, operation="roundtrip",
                input_path=path, input_sha256=cli.sha256_file(path), output_path=json_out,
                output_sha256=cli.ProvenanceRecord.sha256_file(json_out),
                parameters={"to": to, "back_to": back_exporter, "layout": layout or "", "resolved_layout": resolved_layout or "", "encoding": first.encoding, "work_dir": str(work_dir or ""), "json_out": str(json_out)},
                plugins={"forward_exporter": first.exporter_name, "back_exporter": second.exporter_name},
                decoder=first.encoding, encoder=back_exporter,
                evidence=[f"original_decoded_sha256={original_sha}", f"first_decoded_sha256={first_sha}", f"final_decoded_sha256={final_sha}", f"forward_match={int(forward_match)}", f"roundtrip_match={int(roundtrip_match)}"],
            )
            cli.write_provenance(record, prov_target, overwrite=force)
        return RoundtripResult(report=report, roundtrip_match=roundtrip_match)
    finally:
        if temp_context is not None:
            temp_context.cleanup()
