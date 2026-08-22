"""Filesystem operations exposed to Fluxctl frontends.

The implementation is migrated incrementally from ``studio_services``. These
typed entry points give CLI and Qt a stable ownership boundary first.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Sequence

from ..output import atomic_write_bytes


def _services():
    from .. import studio_services

    return studio_services


def list_files(path: Path, layout_id: Optional[str], encoding: str, directory: str):
    return _services().list_files(path, layout_id, encoding, directory)


def list_files_with_info(path: Path, layout_id: Optional[str], encoding: str, directory: str):
    return _services().list_files_with_info(path, layout_id, encoding, directory)


def file_allocation_for_image(path: Path, layout_id: Optional[str], encoding: str, file_path: str):
    return _services().file_allocation_for_image(path, layout_id, encoding, file_path)


def file_hex_dump(path: Path, layout_id: Optional[str], encoding: str, file_path: str, max_bytes: int = 65536):
    return _services().file_hex_dump(path, layout_id, encoding, file_path, max_bytes=max_bytes)


def extract_file_to_path(
    path: Path,
    layout_id: Optional[str],
    encoding: str,
    file_path: str,
    output: Path,
    *,
    overwrite: bool = False,
) -> Path:
    """Extract one filesystem file and atomically write it to the host."""

    data = _services().file_hex_dump(path, layout_id, encoding, file_path, max_bytes=None).data
    atomic_write_bytes(output, data, overwrite=overwrite, source_paths=[path])
    return output


def export_filesystem_entry(path: Path, layout_id: Optional[str], encoding: str, file_path: str, destination: Path):
    return _services().export_filesystem_entry(path, layout_id, encoding, file_path, destination)


def export_filesystem_entries(path: Path, layout_id: Optional[str], encoding: str, file_paths: Sequence[str], destination: Path, overwrite: bool = False):
    return _services().export_filesystem_entries(path, layout_id, encoding, file_paths, destination, overwrite=overwrite)


def replace_file_with_copy(path: Path, layout_id: Optional[str], encoding: str, file_path: str, replacement: Path, output: Path):
    return _services().replace_file_with_copy(path, layout_id, encoding, file_path, replacement, output)


def delete_filesystem_entry_with_copy(path: Path, layout_id: Optional[str], encoding: str, file_path: str, output: Path):
    return _services().delete_filesystem_entry_with_copy(path, layout_id, encoding, file_path, output)


def import_file_with_copy(path: Path, layout_id: Optional[str], encoding: str, source: Path, directory: str, output: Path):
    return _services().import_file_with_copy(path, layout_id, encoding, source, directory, output)


def import_directory_with_copy(path: Path, layout_id: Optional[str], encoding: str, source: Path, directory: str, output: Path):
    return _services().import_directory_with_copy(path, layout_id, encoding, source, directory, output)


def create_directory_with_copy(path: Path, layout_id: Optional[str], encoding: str, directory: str, output: Path):
    return _services().create_directory_with_copy(path, layout_id, encoding, directory, output)


def replace_file_bytes_with_copy(path: Path, layout_id: Optional[str], encoding: str, file_path: str, data: bytes, output: Path):
    return _services().replace_file_bytes_with_copy(path, layout_id, encoding, file_path, data, output)


def replace_flat_sector_bytes_with_copy(path: Path, layout_id: str, track: int, head: int, sector: int, data: bytes, output: Path):
    return _services().replace_flat_sector_bytes_with_copy(path, layout_id, track, head, sector, data, output)
