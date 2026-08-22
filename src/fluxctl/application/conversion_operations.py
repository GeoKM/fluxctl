"""Image conversion operations shared by Fluxctl frontends.

The conversion implementation still lives in the legacy CLI module during the
incremental refactor, but callers use this typed boundary instead of spawning
the CLI process or importing command handlers directly.
"""
from __future__ import annotations

import hashlib
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


def convert_image(
    path: Path,
    output: Path,
    exporter: str,
    layout: Optional[str],
    encoding: str,
    *,
    force: bool = False,
) -> ConversionResult:
    """Convert an image using the same core and output policy as the CLI."""

    # Imports stay lazy so importing the application layer does not initialize
    # Typer or the complete decoder/exporter registry for unrelated operations.
    from .. import cli

    result = cli._prepare_convert_payload(path, exporter, layout, encoding)
    provenance_path = output.with_suffix(output.suffix + ".provenance.json")
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
