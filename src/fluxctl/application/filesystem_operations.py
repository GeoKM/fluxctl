"""Filesystem operations exposed to Fluxctl frontends.

The implementation is migrated incrementally from ``studio_services``. These
typed entry points give CLI and Qt a stable ownership boundary first.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Sequence
import shutil
import tempfile

from ..filesystem_detection import detect_filesystem
from ..filesystems import RawSectorImage, TrackSectorImage, load_builtin_filesystems
from ..filesystems.cbm_dos import CBMDOS
from ..filesystems.cbm_dos_1581 import CBMDOS1581
from ..filesystems.cpm import CPMFilesystem, cpm_disk_parameters_for_layout
from ..filesystems.fat12 import FAT12
from ..layouts.loader import ensure_layout_loaded
from ..filesystems.cbm_dos import cbm_file_type_label
from ..output import atomic_write_bytes
from . import models
from .image_operations import prepare_image
from .hex_operations import format_hex_dump


def _models():
    return models


def _join_filesystem_path(directory: str, name: str) -> str:
    parts = [part for part in directory.strip("/").split("/") if part]
    parts.append(name)
    return "/" + "/".join(parts)


def _format_hex_dump(data: bytes, *, max_bytes: Optional[int] = None) -> str:
    return format_hex_dump(data, max_bytes=max_bytes)


def safe_export_name(name: str) -> str:
    return _safe_export_name(name)


def sector_hex_dump(
    path: Path,
    layout_id: Optional[str],
    encoding: str,
    track: int,
    head: int,
    sector_id: int,
    *,
    max_bytes: Optional[int] = None,
):
    image = prepare_image(path, layout_id, encoding)
    if not isinstance(image, TrackSectorImage):
        raise ValueError("Image could not be reconstructed into sector tracks")
    try:
        data = image._sector_lookup[(track, head, sector_id)]
    except KeyError as exc:
        raise ValueError(f"Sector {track}:{head}:{sector_id} is not available") from exc
    return _models().HexDumpView(
        title=f"Sector T{track} H{head} S{sector_id}",
        size=len(data),
        text=_format_hex_dump(data, max_bytes=max_bytes),
        data=data,
        source_kind="sector",
        track=track,
        head=head,
        sector=sector_id,
    )


def sector_list(path: Path, layout_id: Optional[str], encoding: str, track: int, head: int):
    image = prepare_image(path, layout_id, encoding)
    if not isinstance(image, TrackSectorImage):
        raise ValueError("Image could not be reconstructed into sector tracks")
    selected = next((row for row in image.tracks if row.track == track and row.head == head), None)
    if selected is None:
        raise ValueError(f"Track {track} head {head} is not available")
    lines = [
        f"Track {selected.track} head {selected.head}: "
        f"{len(selected.sectors)} sectors (weak={selected.weak} missing={selected.missing})"
    ]
    for sector in sorted(selected.sectors, key=lambda item: item.sector_id):
        crc_status = "ok" if sector.crc_ok else "bad"
        lines.append(
            f"ID {sector.sector_id:02d} size={sector.size} crc={crc_status} "
            f"deleted={'yes' if sector.deleted else 'no'} conf={sector.confidence:.2f}"
        )
    return _models().TextView(title=f"Sectors T{track} H{head}", text="\n".join(lines))


def _mount_filesystem(path: Path, layout_id: Optional[str], encoding: str):
    load_builtin_filesystems()
    image = prepare_image(path, layout_id, encoding)
    filesystem = detect_filesystem(image).plugin
    if filesystem is None:
        raise ValueError("No supported filesystem is available")
    return filesystem


def _filesystem_volume_text(filesystem) -> str:
    try:
        metadata = filesystem.metadata()
    except Exception:
        return ""
    disk_name = str(metadata.get("disk_name") or "").strip()
    disk_id = str(metadata.get("disk_id") or "").strip()
    dos_type = str(metadata.get("dos_type") or "").strip()
    if disk_name or disk_id or dos_type:
        parts = []
        if disk_name:
            parts.append(f"Name: {disk_name}")
        if disk_id:
            parts.append(f"ID: {disk_id}")
        if dos_type:
            parts.append(f"DOS: {dos_type}")
        return "  ".join(parts)
    volume_label = str(metadata.get("volume_label") or metadata.get("label") or "").strip()
    if volume_label:
        return f"Label: {volume_label}"
    if metadata.get("filesystem") == "apple_dos_3_3":
        volume_number = metadata.get("volume_number")
        catalog_entries = int(metadata.get("catalog_entries") or 0)
        catalog = f"{catalog_entries} cataloged file(s)" if catalog_entries else "empty catalog"
        return f"Apple DOS 3.3  Volume: {volume_number}  {catalog}"
    return ""


def _filesystem_parent(path: str) -> str:
    parts = [part for part in path.strip("/").split("/") if part]
    if len(parts) <= 1:
        return "/"
    return "/" + "/".join(parts[:-1])


def _filesystem_basename(path: str) -> str:
    parts = [part for part in path.strip("/").split("/") if part]
    return parts[-1] if parts else ""


def _safe_export_name(name: str) -> str:
    safe = "".join(char if char not in "/\\:\0" else "_" for char in name).strip()
    return safe or "unnamed"


def _find_entry(filesystem, fs_path: str):
    models = _models()
    root_name = fs_path.strip("/")
    if root_name:
        for entry in filesystem.list_directory("/"):
            if entry.name.casefold() == root_name.casefold():
                return models.FileEntryView(
                    entry.name, "<DIR>" if entry.is_dir else "file", entry.size,
                    _join_filesystem_path("/", entry.name), entry.is_dir,
                )
    parent = _filesystem_parent(fs_path)
    name = _filesystem_basename(fs_path)
    if not name:
        raise ValueError("Choose a file or directory entry to export")
    for entry in filesystem.list_directory(parent):
        if entry.name.lower() == name.lower():
            return models.FileEntryView(
                entry.name, "<DIR>" if entry.is_dir else "file", entry.size,
                _join_filesystem_path(parent, entry.name), entry.is_dir,
            )
    raise ValueError(f"Filesystem entry '{fs_path}' was not found")


def _remove_export_target(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def _export_directory_contents(filesystem, fs_path: str, host_directory: Path) -> tuple[int, int]:
    host_directory.mkdir(parents=True, exist_ok=True)
    files = 0
    byte_count = 0
    for entry in filesystem.list_directory(fs_path):
        entry_path = _join_filesystem_path(fs_path, entry.name)
        host_path = host_directory / _safe_export_name(entry.name)
        if entry.is_dir:
            child_files, child_bytes = _export_directory_contents(filesystem, entry_path, host_path)
            files += child_files
            byte_count += child_bytes
            continue
        data = filesystem.extract_file(entry_path)
        host_path.write_bytes(data)
        files += 1
        byte_count += len(data)
    return files, byte_count


def _export_directory(filesystem, fs_path: str, destination_parent: Path, *, overwrite: bool = False):
    models = _models()
    directory_name = _safe_export_name(_filesystem_basename(fs_path))
    final_path = destination_parent / directory_name
    if (final_path.exists() or final_path.is_symlink()) and not overwrite:
        raise ValueError(f"Export destination already exists: {final_path}")
    destination_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{directory_name}.", dir=destination_parent) as temp_name:
        temp_path = Path(temp_name)
        files, byte_count = _export_directory_contents(filesystem, fs_path, temp_path)
        if final_path.exists() or final_path.is_symlink():
            _remove_export_target(final_path)
        shutil.move(str(temp_path), str(final_path))
    return models.ExportResult(path=str(final_path), files=files, bytes=byte_count)


def list_files(path: Path, layout_id: Optional[str], encoding: str, directory: str):
    return list_files_with_info(path, layout_id, encoding, directory).entries


def list_files_with_info(path: Path, layout_id: Optional[str], encoding: str, directory: str):
    models = _models()
    load_builtin_filesystems()
    image = prepare_image(path, layout_id, encoding)
    filesystem = detect_filesystem(image).plugin
    if filesystem is None:
        return models.FileListView([])
    volume_text = _filesystem_volume_text(filesystem)
    try:
        entries = filesystem.list_directory(directory)
    except Exception:
        return models.FileListView([], volume_text)
    return models.FileListView(
        [
            models.FileEntryView(
                entry.name,
                "<DIR>" if entry.is_dir else "file",
                entry.size,
                _join_filesystem_path(directory, entry.name),
                entry.is_dir,
                cbm_file_type_label(entry.attributes, entry.is_dir)
                if entry.attributes is not None and filesystem.__class__.__name__ in {"CBMDOS", "CBMDOS1581"}
                else "",
            )
            for entry in entries
        ],
        volume_text,
    )


def file_allocation_for_image(path: Path, layout_id: Optional[str], encoding: str, file_path: str):
    load_builtin_filesystems()
    filesystem = _mount_filesystem(path, layout_id, encoding)
    if not hasattr(filesystem, "file_sector_addresses"):
        raise ValueError("This filesystem does not expose file sector allocation yet")
    logical_sectors = (
        filesystem.logical_file_sector_addresses(file_path)
        if hasattr(filesystem, "logical_file_sector_addresses") else None
    )
    return _models().FileAllocationView(file_path, filesystem.file_sector_addresses(file_path), logical_sectors)


def file_hex_dump(
    path: Path,
    layout_id: Optional[str],
    encoding: str,
    file_path: str,
    max_bytes: Optional[int] = 65536,
):
    filesystem = _mount_filesystem(path, layout_id, encoding)
    data = filesystem.extract_file(file_path)
    return _models().HexDumpView(
        title=f"File {file_path}", size=len(data), text=_format_hex_dump(data, max_bytes=max_bytes),
        data=data, source_kind="file", file_path=file_path,
    )


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

    data = file_hex_dump(path, layout_id, encoding, file_path, max_bytes=None).data
    atomic_write_bytes(output, data, overwrite=overwrite, source_paths=[path])
    return output


def export_filesystem_entry(
    path: Path,
    layout_id: Optional[str],
    encoding: str,
    file_path: str,
    destination: Path,
    overwrite: bool = False,
):
    filesystem = _mount_filesystem(path, layout_id, encoding)
    entry = _find_entry(filesystem, file_path)
    if entry.is_dir:
        return _export_directory(filesystem, entry.path, destination, overwrite=overwrite)
    data = filesystem.extract_file(entry.path)
    atomic_write_bytes(destination, data, overwrite=overwrite)
    return _models().ExportResult(path=str(destination), files=1, bytes=len(data))


def export_filesystem_entries(path: Path, layout_id: Optional[str], encoding: str, file_paths: Sequence[str], destination: Path, overwrite: bool = False):
    if not file_paths:
        raise ValueError("Choose one or more filesystem entries to export")
    filesystem = _mount_filesystem(path, layout_id, encoding)
    destination.mkdir(parents=True, exist_ok=True)
    files = 0
    byte_count = 0
    exported_paths: list[str] = []
    with tempfile.TemporaryDirectory(prefix=".fluxctl-export.", dir=destination) as temp_name:
        temp_path = Path(temp_name)
        for file_path in file_paths:
            entry = _find_entry(filesystem, file_path)
            if entry.is_dir:
                result = _export_directory(filesystem, entry.path, temp_path, overwrite=overwrite)
                files += result.files
                byte_count += result.bytes
                exported_paths.append(Path(result.path).name)
                continue
            data = filesystem.extract_file(entry.path)
            host_path = temp_path / _safe_export_name(entry.name)
            if host_path.exists():
                raise ValueError(f"Duplicate export name: {entry.name}")
            host_path.write_bytes(data)
            files += 1
            byte_count += len(data)
            exported_paths.append(host_path.name)
        for name in exported_paths:
            final_path = destination / name
            if (final_path.exists() or final_path.is_symlink()) and not overwrite:
                raise ValueError(f"Export destination already exists: {final_path}")
        for child in temp_path.iterdir():
            final_path = destination / child.name
            if final_path.exists() or final_path.is_symlink():
                _remove_export_target(final_path)
            shutil.move(str(child), str(final_path))
    return _models().ExportResult(path=str(destination), files=files, bytes=byte_count)


def _read_mutation_source(path: Path, output: Path) -> bytes:
    if path.resolve() == output.resolve():
        raise ValueError("Output image must be a new copy, not the original image")
    if output.exists():
        raise ValueError(f"Output image already exists: {output}")
    return path.read_bytes()


def _probe_mutation_filesystem(data: bytes, suffix: str, layout_id: Optional[str]):
    if suffix == ".img" and layout_id and cpm_disk_parameters_for_layout(layout_id):
        params = cpm_disk_parameters_for_layout(layout_id)
        image = RawSectorImage(data, bytes_per_sector=params.sector_size)
        image.layout = ensure_layout_loaded(layout_id)
        fs = CPMFilesystem()
        if not fs.probe(image):
            raise ValueError("CP/M mutation is currently supported only for formatted modelled CP/M images")
        return fs, "cpm"
    if suffix == ".img":
        fs = FAT12()
        if not fs.probe(RawSectorImage(data)):
            raise ValueError("FAT12 mutation is currently supported only for FAT12 images")
        return fs, "fat12"
    if suffix in {".d64", ".d71"}:
        fs = CBMDOS()
        if not fs.probe(RawSectorImage(data, bytes_per_sector=256)):
            raise ValueError("CBM DOS mutation is currently supported only for formatted .d64/.d71 images")
        return fs, "cbm_dos_1571" if suffix == ".d71" else "cbm_dos"
    if suffix == ".d81":
        fs = CBMDOS1581()
        if not fs.probe(RawSectorImage(data, bytes_per_sector=256)):
            raise ValueError("1581 mutation is currently supported only for formatted .d81 images")
        return fs, "cbm_dos_1581"
    raise ValueError("Mutation is currently supported only for flat .img, .d64/.d71, and .d81 images")


def _write_mutation(output: Path, data: bytes, source: Path) -> None:
    atomic_write_bytes(output, data, source_paths=[source])


def replace_file_with_copy(path: Path, layout_id: Optional[str], encoding: str, file_path: str, replacement: Path, output: Path):
    data = replacement.read_bytes()
    original = _read_mutation_source(path, output)
    fs, name = _probe_mutation_filesystem(original, path.suffix.lower(), layout_id)
    patched = (fs.replace_file_allocating_clusters(original, file_path, data)
               if name in {"fat12", "cpm"} and hasattr(fs, "replace_file_allocating_clusters")
               else fs.replace_file(original, file_path, data))
    _write_mutation(output, patched, path)
    return _models().ReplaceResult(str(output), file_path, len(data), name)


def delete_filesystem_entry_with_copy(path: Path, layout_id: Optional[str], encoding: str, file_path: str, output: Path):
    original = _read_mutation_source(path, output)
    fs, name = _probe_mutation_filesystem(original, path.suffix.lower(), layout_id)
    entry = _find_entry(fs, file_path)
    _write_mutation(output, fs.delete_entry(original, file_path), path)
    return _models().MutationResult(str(output), "delete", 1, entry.size, name)


def import_file_with_copy(path: Path, layout_id: Optional[str], encoding: str, directory: str, source: Path, output: Path):
    original = _read_mutation_source(path, output)
    fs, name = _probe_mutation_filesystem(original, path.suffix.lower(), layout_id)
    data = source.read_bytes()
    patched = fs.import_file(original, directory, source.name, data)
    _write_mutation(output, patched, path)
    return _models().MutationResult(str(output), "import-file", 1, len(data), name)


def import_directory_with_copy(path: Path, layout_id: Optional[str], encoding: str, directory: str, source: Path, output: Path):
    if not source.is_dir():
        raise ValueError("Choose a host directory to import")
    original = _read_mutation_source(path, output)
    fs, fs_name = _probe_mutation_filesystem(original, path.suffix.lower(), layout_id)
    payload = original
    file_count = 0
    byte_count = 0
    def import_tree(parent: str, host: Path) -> None:
        nonlocal payload, file_count, byte_count, fs
        for child in sorted(host.iterdir(), key=lambda item: item.name.casefold()):
            if child.is_dir():
                if not hasattr(fs, "create_directory"):
                    raise ValueError("Directory import is not supported for this filesystem")
                payload = fs.create_directory(payload, parent, child.name)
                fs, _ = _probe_mutation_filesystem(payload, path.suffix.lower(), layout_id)
                file_count += 1
                import_tree(_join_filesystem_path(parent, child.name), child)
            else:
                data = child.read_bytes()
                payload = fs.import_file(payload, parent, child.name, data)
                fs, _ = _probe_mutation_filesystem(payload, path.suffix.lower(), layout_id)
                file_count += 1; byte_count += len(data)
    if not hasattr(fs, "create_directory"):
        raise ValueError("Directory import is not supported for this filesystem")
    payload = fs.create_directory(payload, directory, source.name)
    fs, _ = _probe_mutation_filesystem(payload, path.suffix.lower(), layout_id)
    file_count += 1
    import_tree(_join_filesystem_path(directory, source.name), source)
    _write_mutation(output, payload, path)
    return _models().MutationResult(str(output), "import-directory", file_count, byte_count, fs_name)


def create_directory_with_copy(path: Path, layout_id: Optional[str], encoding: str, parent: str, name: str, output: Path):
    original = _read_mutation_source(path, output)
    fs, fs_name = _probe_mutation_filesystem(original, path.suffix.lower(), layout_id)
    if not hasattr(fs, "create_directory"):
        raise ValueError("Directory creation is not supported for this filesystem")
    _write_mutation(output, fs.create_directory(original, parent, name), path)
    return _models().MutationResult(str(output), "create-directory", 1, 0, fs_name)


def replace_file_bytes_with_copy(path: Path, layout_id: Optional[str], encoding: str, file_path: str, data: bytes, output: Path):
    original = _read_mutation_source(path, output)
    fs, _ = _probe_mutation_filesystem(original, path.suffix.lower(), layout_id)
    method = getattr(fs, "replace_file_allocating_clusters", None) or fs.replace_file
    _write_mutation(output, method(original, file_path, data), path)
    return _models().HexEditResult(str(output), file_path, len(data), "file")


def replace_flat_sector_bytes_with_copy(path: Path, layout_id: str, track: int, head: int, sector: int, data: bytes, output: Path):
    original = _read_mutation_source(path, output)
    layout = ensure_layout_loaded(layout_id)
    if path.suffix.lower() not in {".img", ".ima", ".raw", ".adf", ".d64", ".d71", ".d81"}:
        raise ValueError("Advanced sector hex editing requires a flat sector image container")
    offset = 0
    if track < 0 or head < 0 or track >= layout.tracks or head >= layout.sides:
        raise ValueError("Target sector is outside the image layout")
    counts = list(layout.track_sectors) if layout.track_sectors else [layout.sectors_per_track] * layout.tracks
    base = int(layout.id_rules.get("sector_number_base", 1))
    for cylinder in range(track):
        offset += counts[cylinder] * layout.sector_size * layout.sides
    offset += head * counts[track] * layout.sector_size + (sector - base) * layout.sector_size
    if len(data) != layout.sector_size or offset < 0 or offset + len(data) > len(original):
        raise ValueError("Edited sector size or location is invalid")
    patched = bytearray(original); patched[offset:offset + len(data)] = data
    _write_mutation(output, bytes(patched), path)
    return _models().HexEditResult(str(output), f"T{track} H{head} S{sector}", len(data), "sector")
