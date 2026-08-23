"""Image conversion operations shared by Fluxctl frontends."""
from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .. import __version__
from ..models import ProvenanceRecord
from ..output import atomic_write_bytes, atomic_write_text, validate_output_path
from ..provenance import write_provenance
from .image_operations import first_diff_offset, image_bytes_for_compare, is_lossy, prepare_image
from .conversion_pipeline import prepare_convert_payload


@dataclass(frozen=True)
class ConversionResult:
    """Observable result of a completed conversion."""

    input_path: Path
    output_path: Path
    exporter: str
    output_size: int
    output_sha256: str
    lossy_warning: bool
    conversion_classification: str = ""
    conversion_reason: str = ""
    conversion_warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class RoundtripResult:
    """Decoded comparison result for a two-leg conversion check."""

    report: dict[str, object]
    roundtrip_match: bool


_MAX_REPORTED_DIFFERENCES = 100


def _track_snapshot(
    tracks: Optional[list[Any]],
    *,
    synthesized: bool = False,
) -> dict[str, Any]:
    """Reduce decoded tracks to stable, JSON-friendly comparison data."""

    if tracks is None:
        return {"available": False, "synthesized": synthesized}

    ordered: list[tuple[int, int, int]] = []
    sectors: dict[tuple[int, int, int], dict[str, Any]] = {}
    track_status: dict[tuple[int, int], dict[str, int]] = {}
    for track in tracks:
        track_key = (int(track.track), int(track.head))
        track_status[track_key] = {
            "missing": int(getattr(track, "missing", 0)),
            "weak": int(getattr(track, "weak", 0)),
        }
        for sector in track.sectors:
            key = (int(track.track), int(track.head), int(sector.sector_id))
            ordered.append(key)
            sectors[key] = {
                "track": key[0],
                "head": key[1],
                "sector": key[2],
                "size": int(sector.size),
                "data_sha256": hashlib.sha256(sector.data).hexdigest() if sector.data else None,
                "has_data": bool(sector.data),
                "crc_ok": bool(sector.crc_ok),
                "deleted": bool(getattr(sector, "deleted", False)),
                "source_revolutions": list(getattr(sector, "source_revolutions", [])),
            }
    return {
        "available": True,
        "synthesized": synthesized,
        "ordered_sectors": [list(key) for key in ordered],
        "track_heads": [list(key) for key in track_status],
        "sectors": {"%d:%d:%d" % key: value for key, value in sectors.items()},
        "track_status": {"%d:%d" % key: value for key, value in track_status.items()},
    }


def _filesystem_snapshot(path: Path, layout: Optional[str], encoding: str) -> dict[str, Any]:
    """Hash readable filesystem files without making extraction mandatory."""

    try:
        image = prepare_image(path, layout, encoding)
        from ..filesystem_detection import detect_filesystem
        detection = detect_filesystem(image)
        plugin = detection.plugin
        result: dict[str, Any] = {
            "available": plugin is not None,
            "filesystem": detection.primary,
            "files": {},
            "extraction_errors": [],
        }
        if plugin is None:
            return result

        pending = ["/"]
        visited: set[str] = set()
        files: dict[str, dict[str, Any]] = {}
        errors: list[dict[str, str]] = []
        while pending:
            directory = pending.pop()
            if directory in visited:
                continue
            visited.add(directory)
            try:
                entries = plugin.list_directory(directory)
            except Exception as exc:  # pragma: no cover - plugin-specific failures
                errors.append({"path": directory, "error": str(exc)})
                continue
            for entry in entries:
                entry_path = "/" + entry.name if directory == "/" else directory.rstrip("/") + "/" + entry.name
                if entry.is_dir:
                    pending.append(entry_path)
                    continue
                try:
                    data = plugin.extract_file(entry_path)
                    files[entry_path] = {
                        "size": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                    }
                except Exception as exc:  # pragma: no cover - plugin-specific failures
                    errors.append({"path": entry_path, "error": str(exc)})
        result["files"] = files
        result["extraction_errors"] = errors
        result["readable"] = bool(files) or not errors
        return result
    except Exception as exc:  # pragma: no cover - unsupported image/container
        return {
            "available": False,
            "filesystem": None,
            "files": {},
            "extraction_errors": [{"path": "/", "error": str(exc)}],
        }


def _compare_snapshots(
    original: dict[str, Any],
    candidate: dict[str, Any],
    original_files: dict[str, Any],
    candidate_files: dict[str, Any],
) -> dict[str, Any]:
    """Compare decoded physical structure, data, preservation, and files."""

    unavailable = not original.get("available") or not candidate.get("available")
    if unavailable:
        geometry = {"available": False, "match": None, "reason": "decoded sector model unavailable"}
        data = {"available": False, "match": None, "reason": "decoded sector model unavailable"}
        preservation = {"available": False, "match": None, "reason": "decoded sector model unavailable"}
    else:
        original_sectors = original["sectors"]
        candidate_sectors = candidate["sectors"]
        original_keys = set(original_sectors)
        candidate_keys = set(candidate_sectors)
        common = sorted(original_keys & candidate_keys)
        identity_match = original_keys == candidate_keys
        order_match = original["ordered_sectors"] == candidate["ordered_sectors"]
        sizes_match = all(original_sectors[key]["size"] == candidate_sectors[key]["size"] for key in common)
        data_differences = [
            key for key in common
            if original_sectors[key]["data_sha256"] != candidate_sectors[key]["data_sha256"]
            or original_sectors[key]["has_data"] != candidate_sectors[key]["has_data"]
        ]
        deleted_differences = [key for key in common if original_sectors[key]["deleted"] != candidate_sectors[key]["deleted"]]
        crc_differences = [key for key in common if original_sectors[key]["crc_ok"] != candidate_sectors[key]["crc_ok"]]
        missing_differences = original.get("track_status") != candidate.get("track_status") or original_keys != candidate_keys
        synthesized_match = original.get("synthesized", False) == candidate.get("synthesized", False)
        geometry = {
            "available": True,
            "match": identity_match and order_match and sizes_match,
            "track_head_identity_match": original.get("track_heads") == candidate.get("track_heads"),
            "sector_identity_match": identity_match,
            "sector_order_match": order_match,
            "sector_sizes_match": sizes_match,
            "original_sector_count": len(original_keys),
            "candidate_sector_count": len(candidate_keys),
        }
        data = {
            "available": True,
            "match": identity_match and not data_differences,
            "compared_sectors": len(common),
            "different_sector_count": len(data_differences),
            "different_sectors": [list(map(int, key.split(":"))) for key in data_differences[:_MAX_REPORTED_DIFFERENCES]],
        }
        preservation = {
            "available": True,
            "match": bool(data["match"] and geometry["match"] and not deleted_differences and not crc_differences and missing_differences is False and synthesized_match),
            "deleted_marks_match": not deleted_differences,
            "crc_status_match": not crc_differences,
            "missing_status_match": not missing_differences,
            "synthesized_status_match": synthesized_match,
            "deleted_mark_difference_count": len(deleted_differences),
            "crc_difference_count": len(crc_differences),
        }

    file_available = bool(original_files.get("readable")) and bool(candidate_files.get("readable"))
    file_match = None
    if file_available:
        file_match = (
            original_files.get("filesystem") == candidate_files.get("filesystem")
            and original_files.get("files") == candidate_files.get("files")
        )
    filesystem = {
        "available": file_available,
        "match": file_match,
        "original_filesystem": original_files.get("filesystem"),
        "candidate_filesystem": candidate_files.get("filesystem"),
        "original_file_count": len(original_files.get("files", {})),
        "candidate_file_count": len(candidate_files.get("files", {})),
        "hashes_match": file_match,
    }
    return {"data": data, "logical_geometry": geometry, "preservation": preservation, "filesystem_files": filesystem}


def _decoded_tracks(path: Path, layout: Optional[str], encoding: str) -> Optional[list[Any]]:
    """Return decoded tracks when the selected image path exposes them."""

    try:
        image = prepare_image(path, layout, encoding)
        tracks = getattr(image, "tracks", None)
        return list(tracks) if tracks is not None else None
    except Exception:
        return None


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

    result = prepare_convert_payload(path, exporter, layout, encoding)
    provenance_path = prov_out or output.with_suffix(output.suffix + ".provenance.json")
    validate_output_path(output, overwrite=force, source_paths=[path])
    validate_output_path(provenance_path, overwrite=force, source_paths=[path])
    atomic_write_bytes(output, result.payload, overwrite=force, source_paths=[path])
    provenance = ProvenanceRecord(
        tool_name="fluxctl",
        tool_version=__version__,
        operation="convert",
        input_path=path,
        input_sha256=ProvenanceRecord.sha256_file(path),
        output_path=output,
        output_sha256=hashlib.sha256(result.payload).hexdigest(),
        parameters={
            "layout": layout or "",
            "resolved_layout": result.layout_id,
            "encoding": result.encoding,
            "exporter": exporter,
            "conversion_classification": result.conversion_plan.classification,
            "conversion_reason": result.conversion_plan.reason,
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
    write_provenance(provenance, provenance_path, overwrite=force)
    return ConversionResult(
        input_path=path,
        output_path=output,
        exporter=exporter,
        output_size=len(result.payload),
        output_sha256=hashlib.sha256(result.payload).hexdigest(),
        lossy_warning=(
            result.conversion_plan.lossy
            or is_lossy(result.track_data, result.exporter_metadata)
        ),
        conversion_classification=result.conversion_plan.classification,
        conversion_reason=result.conversion_plan.reason,
        conversion_warnings=result.conversion_plan.warnings,
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

    def suffix(exporter: str) -> str:
        return ".img" if exporter == "raw" else f".{exporter}"

    def infer_back_exporter(source: Path) -> str:
        source_suffix = source.suffix.lower()
        return {
            ".img": "raw",
            ".ima": "raw",
            ".raw": "raw",
            ".adf": "adf",
            ".d64": "d64",
            ".d71": "d71",
            ".d81": "d81",
            ".imd": "imd",
            ".dsk": "raw",
            ".dmk": "raw",
        }.get(source_suffix, "raw")

    back_exporter = back_to or infer_back_exporter(path)
    temp_context = tempfile.TemporaryDirectory(prefix="fluxctl-roundtrip-") if work_dir is None else None
    base_dir = Path(temp_context.name) if temp_context is not None else work_dir
    assert base_dir is not None
    base_dir.mkdir(parents=True, exist_ok=True)
    try:
        first_path = base_dir / f"{path.stem}-to-{to}{suffix(to)}"
        final_path = base_dir / f"{path.stem}-roundtrip-{back_exporter}{suffix(back_exporter)}"
        prov_target = (prov_out or json_out.with_suffix(json_out.suffix + ".provenance.json")) if json_out else None
        retained_outputs = [first_path, final_path] if work_dir is not None else []
        report_outputs = [output for output in (json_out, prov_target) if output is not None]
        for output in [*retained_outputs, *report_outputs]:
            validate_output_path(output, overwrite=force, source_paths=[path])
        intermediate_overwrite = force if work_dir is not None else False

        first = prepare_convert_payload(path, to, layout, encoding)
        atomic_write_bytes(first_path, first.payload, overwrite=intermediate_overwrite, source_paths=[path])
        resolved_layout = first.layout_id or layout
        original_bytes, original_meta = image_bytes_for_compare(path, resolved_layout, first.encoding)
        first_bytes, first_meta = image_bytes_for_compare(first_path, resolved_layout, first.encoding)
        second = prepare_convert_payload(first_path, back_exporter, resolved_layout, first.encoding)
        atomic_write_bytes(final_path, second.payload, overwrite=intermediate_overwrite, source_paths=[path])
        final_bytes, final_meta = image_bytes_for_compare(final_path, resolved_layout, first.encoding)

        original_tracks = _decoded_tracks(path, resolved_layout, first.encoding)
        first_tracks = first.track_data or _decoded_tracks(first_path, resolved_layout, first.encoding)
        final_tracks = second.track_data or _decoded_tracks(final_path, resolved_layout, first.encoding)
        original_sector_snapshot = _track_snapshot(original_tracks)
        first_sector_snapshot = _track_snapshot(
            first_tracks,
            synthesized=bool(first.exporter_metadata.get("padded_missing")),
        )
        final_sector_snapshot = _track_snapshot(
            final_tracks,
            synthesized=bool(second.exporter_metadata.get("padded_missing")),
        )
        original_files = _filesystem_snapshot(path, resolved_layout, first.encoding)
        first_files = _filesystem_snapshot(first_path, resolved_layout, first.encoding)
        final_files = _filesystem_snapshot(final_path, resolved_layout, first.encoding)
        forward_equivalence = _compare_snapshots(
            original_sector_snapshot,
            first_sector_snapshot,
            original_files,
            first_files,
        )
        roundtrip_equivalence = _compare_snapshots(
            original_sector_snapshot,
            final_sector_snapshot,
            original_files,
            final_files,
        )

        original_sha = hashlib.sha256(original_bytes).hexdigest()
        first_sha = hashlib.sha256(first_bytes).hexdigest()
        final_sha = hashlib.sha256(final_bytes).hexdigest()
        forward_diff = first_diff_offset(original_bytes, first_bytes)
        final_diff = first_diff_offset(original_bytes, final_bytes)
        forward_match = forward_diff is None and len(original_bytes) == len(first_bytes)
        roundtrip_match = final_diff is None and len(original_bytes) == len(final_bytes)
        lossy = is_lossy(first.track_data, first.exporter_metadata) or is_lossy(second.track_data, second.exporter_metadata)
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
            "forward_conversion_classification": first.conversion_plan.classification,
            "forward_conversion_reason": first.conversion_plan.reason,
            "back_conversion_classification": second.conversion_plan.classification,
            "back_conversion_reason": second.conversion_plan.reason,
            "forward_equivalence": forward_equivalence,
            "roundtrip_equivalence": roundtrip_equivalence,
            # These aliases make the final round-trip verdict convenient for
            # consumers while preserving the explicit per-leg reports above.
            "data_equivalence": roundtrip_equivalence["data"],
            "logical_geometry_equivalence": roundtrip_equivalence["logical_geometry"],
            "preservation_equivalence": roundtrip_equivalence["preservation"],
            "filesystem_file_equivalence": roundtrip_equivalence["filesystem_files"],
            "meta": {"original": original_meta, "first": first_meta, "final": final_meta},
        }
        if json_out:
            atomic_write_text(json_out, json.dumps(report, indent=2), overwrite=force, source_paths=[path])
            assert prov_target is not None
            record = ProvenanceRecord(
                tool_name="fluxctl", tool_version=__version__, operation="roundtrip",
                input_path=path, input_sha256=ProvenanceRecord.sha256_file(path), output_path=json_out,
                output_sha256=ProvenanceRecord.sha256_file(json_out),
                parameters={"to": to, "back_to": back_exporter, "layout": layout or "", "resolved_layout": resolved_layout or "", "encoding": first.encoding, "work_dir": str(work_dir or ""), "json_out": str(json_out)},
                plugins={"forward_exporter": first.exporter_name, "back_exporter": second.exporter_name},
                decoder=first.encoding, encoder=back_exporter,
                evidence=[
                    f"original_decoded_sha256={original_sha}",
                    f"first_decoded_sha256={first_sha}",
                    f"final_decoded_sha256={final_sha}",
                    f"forward_match={int(forward_match)}",
                    f"roundtrip_match={int(roundtrip_match)}",
                    f"data_equivalence={int(roundtrip_equivalence['data'].get('match') is True)}",
                    f"logical_geometry_equivalence={int(roundtrip_equivalence['logical_geometry'].get('match') is True)}",
                    f"preservation_equivalence={int(roundtrip_equivalence['preservation'].get('match') is True)}",
                ],
            )
            write_provenance(record, prov_target, overwrite=force)
        return RoundtripResult(report=report, roundtrip_match=roundtrip_match)
    finally:
        if temp_context is not None:
            temp_context.cleanup()
