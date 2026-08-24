"""Batch Studio operations over a selected set of image files."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .diagnostic_operations import summarize_image
from .report_operations import build_qc_for_image
from .conversion_operations import convert_image


def batch_probe(paths: Iterable[Path], operation=None) -> list[dict[str, object]]:
    items = list(paths)
    results: list[dict[str, object]] = []
    for index, path in enumerate(items, start=1):
        if operation is not None:
            operation.checkpoint("batch probe", index, len(items))
        try:
            summary = summarize_image(path)
            results.append({"path": str(path), "status": "ok", "layout": summary.layout_id, "filesystem": summary.filesystem, "encoding": summary.encoding})
        except Exception as exc:
            results.append({"path": str(path), "status": "error", "error": str(exc)})
    return results


def batch_qc(paths: Iterable[Path], operation=None) -> list[dict[str, object]]:
    items = list(paths)
    results: list[dict[str, object]] = []
    for index, path in enumerate(items, start=1):
        if operation is not None:
            operation.checkpoint("batch QC", index, len(items))
        try:
            summary = summarize_image(path)
            report = build_qc_for_image(path, summary.layout_id, summary.encoding, operation=operation)
            results.append({"path": str(path), "status": report.status, "good": report.total_good_sectors, "total": report.total_sectors, "bad": report.total_bad_sectors, "missing": report.total_missing_sectors})
        except Exception as exc:
            results.append({"path": str(path), "status": "error", "error": str(exc)})
    return results


def batch_convert(paths: Iterable[Path], exporter: str, operation=None) -> list[dict[str, object]]:
    items = list(paths)
    suffix = ".img" if exporter == "raw" else f".{exporter}"
    results: list[dict[str, object]] = []
    for index, path in enumerate(items, start=1):
        if operation is not None:
            operation.checkpoint("batch convert", index, len(items))
        output = path.with_name(f"{path.stem}-converted{suffix}")
        counter = 2
        while output.exists():
            output = path.with_name(f"{path.stem}-converted-{counter}{suffix}")
            counter += 1
        try:
            summary = summarize_image(path)
            result = convert_image(path, output, exporter, summary.layout_id, summary.encoding)
            results.append({"path": str(path), "status": "ok", "output": str(result.output_path), "bytes": result.output_size})
        except Exception as exc:
            results.append({"path": str(path), "status": "error", "output": str(output), "error": str(exc)})
    return results


__all__ = ["batch_convert", "batch_probe", "batch_qc"]
