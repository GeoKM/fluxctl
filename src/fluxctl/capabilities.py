"""Machine-readable filesystem/container capability declarations.

This registry is deliberately independent of Qt and Typer.  Frontends can
query the same format-specific contract for action state, documentation, and
future capability-aware workflows.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional


MutationAction = str
WRITE_ACTIONS_SUPPORT_SUMMARY = (
    "Write actions currently support FAT12 .img, modelled CP/M .img, "
    "and CBM DOS .d64/.d71/.d81 images only."
)


@dataclass(frozen=True, slots=True)
class FilesystemCapability:
    filesystem: str
    label: str
    containers: tuple[str, ...]
    detect: bool
    list_entries: bool
    extract: bool
    directory_traversal: bool
    mutation_actions: frozenset[MutationAction]
    map_overlay: str
    hex_editing: bool
    conversion_destinations: tuple[str, ...]
    limitations: str
    action_reasons: tuple[tuple[MutationAction, str], ...] = ()

    def supports_container(self, container: str) -> bool:
        return container.lower().lstrip(".") in self.containers

    def reason_for(self, action: MutationAction) -> str:
        return dict(self.action_reasons).get(action, self.limitations)


_CAPABILITIES: tuple[FilesystemCapability, ...] = (
    FilesystemCapability(
        "fat12", "FAT12", ("img",), True, True, True, True,
        frozenset({"replace_file", "delete_entry", "import_file", "import_directory", "create_directory"}),
        "cluster-chain overlay", True, ("raw", "imd"),
        "Write operations create a new flat .img copy and use 8.3 ASCII names.",
    ),
    FilesystemCapability(
        "cpm", "CP/M variants", ("img",), True, True, True, False,
        frozenset({"delete_entry", "import_file"}), "allocation-block overlay", False, ("raw", "imd"),
        "Mutation is limited to modelled flat .img DPBs; replace and directory operations are not implemented.",
        (("import_file", "Available for modelled CP/M flat .img images. File import and delete write a new image copy."),),
    ),
    FilesystemCapability(
        "cbm_dos", "CBM DOS 1541", ("d64",), True, True, True, False,
        frozenset({"replace_file", "delete_entry", "import_file"}), "CBM DOS BAM block map", False, ("d64", "raw"),
        "Root-level file operations only; directory import and creation are not implemented.",
        (("replace_file", "This CBM DOS image supports root-level file import, replace, and delete in a new image copy."),
         ("delete_entry", "This CBM DOS image supports root-level file import, replace, and delete in a new image copy."),
         ("import_file", "Available for CBM DOS .d64 root-level PRG import by default. .SEQ/.USR suffixes set the corresponding type; REL import requires side-sector support. The operation writes a new image copy.")),
    ),
    FilesystemCapability(
        "cbm_dos_1571", "CBM DOS 1571", ("d71",), True, True, True, False,
        frozenset({"replace_file", "delete_entry", "import_file"}), "CBM DOS BAM block map", False, ("d71", "raw"),
        "Root-level file operations only; directory import and creation are not implemented.",
        (("replace_file", "This CBM DOS image supports root-level file import, replace, and delete in a new image copy."),
         ("delete_entry", "This CBM DOS image supports root-level file import, replace, and delete in a new image copy."),
         ("import_file", "Available for CBM DOS .d71 root-level PRG import by default. .SEQ/.USR suffixes set the corresponding type; REL import requires side-sector support. The operation writes a new image copy.")),
    ),
    FilesystemCapability(
        "cbm_dos_1581", "CBM DOS 1581", ("d81",), True, True, True, True,
        frozenset({"replace_file", "delete_entry", "import_file", "import_directory", "create_directory"}),
        "1581 BAM block map", False, ("d81", "raw"),
        "Replace and delete remain root-file-only; REL side-sector mutation is not implemented.",
        (("import_file", "Available for CBM DOS 1581 .d81 PRG import by default. .SEQ/.USR suffixes set the corresponding type; REL import requires side-sector support. The operation writes a new image copy."),),
    ),
    FilesystemCapability(
        "amiga_ffs", "Amiga OFS/FFS", ("adf",), True, True, True, True,
        frozenset(), "filesystem logical map", False, ("adf", "raw"),
        "ADF mutation is pending allocation bitmap, checksums, file headers, and directory hash-chain updates.",
    ),
    FilesystemCapability(
        "prodos", "Apple ProDOS", ("woz", "po", "do", "nib", "img", "scp"), True, True, True, True,
        frozenset(), "ProDOS block overlay", False, ("po", "do"), "Read-only Apple II support.",
    ),
    FilesystemCapability(
        "apple_dos_33", "Apple DOS 3.3", ("woz", "po", "do", "nib", "img", "scp"), True, True, True, False,
        frozenset(), "DOS 3.3 T/S-list overlay", False, ("do",), "Read-only; extracted sizes are sector-granular.",
    ),
    FilesystemCapability(
        "displaywriter", "DisplayWriter", ("img", "scp", "imd"), True, True, False, False,
        frozenset(), "", False, ("raw",), "Only standard-label records are listed; document extraction is not implemented.",
    ),
    FilesystemCapability(
        "rt11", "RT-11", ("img", "scp", "imd"), True, True, True, False,
        frozenset(), "", False, ("raw",), "Read-only flat RAD50 directory and extent reader.",
    ),
    FilesystemCapability(
        "wang_ois", "Wang OIS", ("img", "scp"), True, True, True, True,
        frozenset(), "allocation extent overlay", False, ("raw",), "Read-only support for the modelled 315K package catalog.",
    ),
    FilesystemCapability(
        "seiko_8300", "Seiko 8300", ("img", "scp"), True, True, False, False,
        frozenset(), "", False, ("raw",), "Catalog/header records are readable; physical allocation is not proven.",
    ),
)


def filesystem_capabilities() -> tuple[FilesystemCapability, ...]:
    """Return all registered filesystem/container capability declarations."""
    return _CAPABILITIES


def filesystem_capability(filesystem: str, container: str) -> Optional[FilesystemCapability]:
    """Resolve the most specific capability for a detected filesystem/container."""
    fs = (filesystem or "").lower()
    suffix = (container or "").lower().lstrip(".")
    for capability in _CAPABILITIES:
        if capability.filesystem == fs and capability.supports_container(suffix):
            return capability
    return None


def capability_markdown(entries: Iterable[FilesystemCapability] = _CAPABILITIES) -> str:
    """Render the registry as a stable Markdown table for generated docs."""
    lines = [
        "| Filesystem | Containers | Detect | List | Extract | Mutations | Map overlay | HEX edit | Destinations | Limitations |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for entry in entries:
        mutations = ", ".join(sorted(entry.mutation_actions)) or "None"
        lines.append(
            f"| {entry.label} (`{entry.filesystem}`) | {', '.join('.' + item for item in entry.containers)} | "
            f"{'Yes' if entry.detect else 'No'} | {'Yes' if entry.list_entries else 'No'} | "
            f"{'Yes' if entry.extract else 'No'} | {mutations} | {entry.map_overlay or 'None'} | "
            f"{'Yes' if entry.hex_editing else 'No'} | {', '.join('.' + item for item in entry.conversion_destinations)} | {entry.limitations} |"
        )
    return "\n".join(lines)
