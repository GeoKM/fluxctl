"""Collision-safe atomic writes for user-visible output files."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Iterable

from .exceptions import FluxctlError, OutputExistsError


def validate_output_path(
    path: Path,
    *,
    overwrite: bool = False,
    source_paths: Iterable[Path] = (),
) -> None:
    """Reject source replacement and unapproved output collisions."""

    destination = path.expanduser().resolve(strict=False)
    for source in source_paths:
        if destination == source.expanduser().resolve(strict=False):
            raise FluxctlError(f"Output path must differ from input path: {path}")
    if (path.exists() or path.is_symlink()) and not overwrite:
        raise OutputExistsError(f"Output already exists: {path}. Pass --force to replace it.")


def atomic_write_bytes(
    path: Path,
    data: bytes,
    *,
    overwrite: bool = False,
    source_paths: Iterable[Path] = (),
) -> Path:
    """Publish bytes atomically after validating collision policy.

    The temporary file is created in the destination directory so publication
    cannot cross filesystems. Non-overwriting publication uses a hard link,
    which atomically fails if another process creates the destination first.
    """

    validate_output_path(path, overwrite=overwrite, source_paths=source_paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if overwrite:
            os.replace(temp_path, path)
        else:
            try:
                os.link(temp_path, path)
            except FileExistsError as exc:
                raise OutputExistsError(f"Output already exists: {path}. Pass --force to replace it.") from exc
            except OSError as exc:
                raise FluxctlError(f"Could not publish output atomically at {path}: {exc}") from exc
            temp_path.unlink()
        _sync_directory(path.parent)
    finally:
        if temp_path.exists():
            temp_path.unlink()
    return path


def atomic_write_text(
    path: Path,
    text: str,
    *,
    encoding: str = "utf-8",
    overwrite: bool = False,
    source_paths: Iterable[Path] = (),
) -> Path:
    """Encode and atomically publish text."""

    return atomic_write_bytes(
        path,
        text.encode(encoding),
        overwrite=overwrite,
        source_paths=source_paths,
    )


def _sync_directory(path: Path) -> None:
    """Best-effort durability barrier for the directory entry."""

    flags = getattr(os, "O_DIRECTORY", 0) | os.O_RDONLY
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


__all__ = ["atomic_write_bytes", "atomic_write_text", "validate_output_path"]
