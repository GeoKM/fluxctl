"""Content comparison operations shared by Fluxctl frontends."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class ComparisonResult:
    """Decoded content comparison result."""

    path_a: Path
    path_b: Path
    report: dict[str, object]

    @property
    def identical(self) -> bool:
        return bool(self.report["identical"])


def compare_images(
    path_a: Path,
    path_b: Path,
    *,
    layout_a: Optional[str] = None,
    layout_b: Optional[str] = None,
    encoding_a: str = "auto",
    encoding_b: str = "auto",
) -> ComparisonResult:
    """Compare decoded image contents using the CLI's comparison core."""

    from .. import cli

    bytes_a, meta_a = cli._image_bytes_for_compare(path_a, layout_a, encoding_a)
    bytes_b, meta_b = cli._image_bytes_for_compare(path_b, layout_b, encoding_b)
    sha_a = hashlib.sha256(bytes_a).hexdigest()
    sha_b = hashlib.sha256(bytes_b).hexdigest()
    diff = cli._first_diff_offset(bytes_a, bytes_b)
    report = {
        "path_a": str(path_a),
        "path_b": str(path_b),
        "len_a": len(bytes_a),
        "len_b": len(bytes_b),
        "sha256_a": sha_a,
        "sha256_b": sha_b,
        "identical": diff is None and len(bytes_a) == len(bytes_b),
        "first_diff_offset": diff,
        "meta_a": meta_a,
        "meta_b": meta_b,
    }
    return ComparisonResult(path_a, path_b, report)
