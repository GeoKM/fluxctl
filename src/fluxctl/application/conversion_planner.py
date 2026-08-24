"""Semantic conversion compatibility planning.

Exporters answer a deliberately narrow question: can they serialize the sector
object they were given?  This module answers the higher-level question shared
by the CLI and Studio: is that destination meaningful for the detected disk
layout, and what preservation trade-off does it make?
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional


SECTOR_LOSSLESS = "sector-lossless"
LOGICALLY_EQUIVALENT = "logically-equivalent"
LOSSY_BUT_USEFUL = "lossy-but-useful"
UNSUPPORTED = "unsupported"

KNOWN_EXPORTERS = ("raw", "imd", "adf", "d64", "d71", "d81", "g64", "po", "do", "scp")
PHYSICAL_CONTAINERS = {"scp", "imd", "dsk", "dmk", "woz", "nib"}
LOGICAL_CONTAINERS = {"img", "raw", "d64", "d71", "d81", "adf", "po", "do"}


@dataclass(frozen=True)
class ConversionContext:
    """Facts about a resolved source image used for semantic planning."""

    source_kind: str = ""
    layout_id: str = ""
    encoding: str = ""
    filesystem: str = ""
    tracks: Optional[int] = None
    heads: Optional[int] = None
    sector_size: Optional[int] = None
    sector_sizes: tuple[int, ...] = ()

    @classmethod
    def from_image(
        cls,
        image: Any,
        *,
        source_kind: str = "",
        layout: Any = None,
        encoding: str = "",
        filesystem: str = "",
    ) -> "ConversionContext":
        tracks = getattr(image, "tracks", None)
        track_list = list(tracks) if tracks is not None else []
        observed_sizes = tuple(
            sorted(
                {
                    int(sector.size)
                    for track in track_list
                    for sector in getattr(track, "sectors", ())
                    if getattr(sector, "size", None)
                }
            )
        )
        layout_id = str(getattr(layout, "layout_id", "") or getattr(getattr(image, "layout", None), "layout_id", ""))
        layout_encoding = str(getattr(layout, "encoding", "") or "")
        return cls(
            source_kind=source_kind.lower().lstrip("."),
            layout_id=layout_id,
            encoding=(encoding or layout_encoding or "").lower(),
            filesystem=(filesystem or "").lower(),
            tracks=getattr(layout, "tracks", None) or (max((int(t.track) for t in track_list), default=-1) + 1 or None),
            heads=getattr(layout, "sides", None) or (max((int(t.head) for t in track_list), default=-1) + 1 or None),
            sector_size=getattr(image, "bytes_per_sector", None) or getattr(layout, "sector_size", None),
            sector_sizes=observed_sizes or tuple(
                int(size)
                for size in (getattr(layout, "sector_sizes", None) or ())
                if size
            ),
        )


@dataclass(frozen=True)
class ConversionPlan:
    """Decision and explanation for one source-to-target conversion route."""

    target: str
    classification: str
    allowed: bool
    reason: str
    warnings: tuple[str, ...] = ()

    @property
    def lossy(self) -> bool:
        return self.classification == LOSSY_BUT_USEFUL

    @property
    def label(self) -> str:
        return self.classification.replace("-", " ").title()


def _normalise_target(target: str) -> str:
    target = target.lower().strip().lstrip(".")
    return "raw" if target in {"img", "ima"} else target


def _layout_family(context: ConversionContext) -> str:
    layout = context.layout_id
    if layout.startswith("apple2_"):
        return "apple2"
    if layout.startswith("amiga_"):
        return "amiga"
    if layout.startswith("commodore_gcr_1541"):
        return "commodore1541"
    if layout == "commodore_gcr_1571_341k":
        return "commodore1571"
    if layout == "commodore_mfm_1581_800k":
        return "commodore1581"
    filesystem = context.filesystem
    if filesystem.startswith("amiga"):
        return "amiga"
    if filesystem == "cbm_dos_1581":
        return "commodore1581"
    if filesystem in {"cbm_dos", "cbm_dos_1571"}:
        if context.source_kind == "d71":
            return "commodore1571"
        if context.source_kind == "d64":
            return "commodore1541"
    return "unknown"


def _physical_loss(context: ConversionContext) -> bool:
    return context.source_kind in PHYSICAL_CONTAINERS


def _plan_known_target(context: ConversionContext, target: str) -> tuple[bool, str, tuple[str, ...]]:
    family = _layout_family(context)
    source = context.source_kind
    warnings: list[str] = []

    if target == "scp":
        if not context.layout_id:
            return False, "SCP synthesis requires an explicit or detected layout.", ()
        if context.encoding not in {"fm", "mfm", "gcr", "apple2_gcr"}:
            return False, f"SCP synthesis does not support {context.encoding or 'unknown'} encoding.", ()
        if context.encoding == "gcr" and family not in {"commodore1541", "commodore1571"}:
            return False, "SCP GCR synthesis currently supports Commodore GCR layouts only.", ()
        unsupported_markers = (
            "wang_", "hs32", "hard_sector", "displaywriter", "rx02", "xdf",
            "cpmplus", "mmfm", "apple_gcr", "victor_", "northstar_",
        )
        if any(marker in context.layout_id.lower() for marker in unsupported_markers):
            return False, "This layout needs a specialised physical track encoder before SCP export is safe.", ()
        return True, "The resolved logical sectors can be encoded as deterministic synthetic SCP flux.", (
            "Synthetic SCP preserves logical track structure, not original analogue timing, weak bits, write splices, or copy protection.",
        )

    if target == "raw":
        if family == "unknown" and not context.layout_id:
            return True, "The source geometry is not identified; raw sector serialization is the only generic route.", ()
        if _physical_loss(context):
            warnings.append("Physical track encoding, timing, and protection details are not preserved.")
        return True, "Raw sector output is compatible with the resolved source geometry.", tuple(warnings)

    if target == "imd":
        if family == "apple2":
            return False, "ImageDisk is not a compatible container for Apple II GCR track semantics.", ()
        if len(context.sector_sizes) > 1:
            return False, "ImageDisk cannot represent mixed sector sizes on one conversion route.", ()
        if family == "amiga":
            return True, "ImageDisk can carry decoded Amiga sectors, but not native Amiga MFM track encoding.", (
                "Use ADF for a logical Amiga disk image or SCP for physical preservation.",
            )
        return True, "ImageDisk can represent the resolved standard FM/MFM sector geometry.", ()

    if target == "adf":
        if family != "amiga" and source != "adf":
            return False, "ADF is only valid for an Amiga layout with 512-byte sectors.", ()
        if context.sector_size not in (None, 512) and 512 not in context.sector_sizes:
            return False, "ADF requires 512-byte Amiga sectors.", ()
        return True, "The resolved source is an Amiga 880K logical disk.", ()

    if target == "d64":
        if family != "commodore1541" and source != "d64":
            return False, "D64 is only valid for a Commodore 1541/compatible 256-byte sector layout.", ()
        if context.sector_size not in (None, 256):
            return False, "D64 requires 256-byte logical sectors.", ()
        return True, "The resolved source matches Commodore 1541 logical geometry.", ()

    if target == "d71":
        if family != "commodore1571" and source != "d71":
            return False, "D71 is only valid for a two-sided Commodore 1571 logical layout.", ()
        if context.heads not in (None, 2):
            return False, "D71 requires two recorded sides.", ()
        if context.sector_size not in (None, 256):
            return False, "D71 requires 256-byte logical sectors.", ()
        return True, "The resolved source matches Commodore 1571 logical geometry.", ()

    if target == "d81":
        if family != "commodore1581" and source != "d81":
            return False, "D81 is only valid for the Commodore 1581 800K logical layout.", ()
        if context.sector_size not in (None, 512):
            return False, "D81 requires 512-byte physical sectors.", ()
        return True, "The resolved source matches Commodore 1581 logical geometry.", ()

    if target == "g64":
        if not (context.encoding == "gcr" or family in {"commodore1541", "commodore1571"}):
            return False, "G64 requires a Commodore GCR source with decoded nibble data.", ()
        return True, "The source uses Commodore GCR encoding; G64 preserves decoded track nibble data.", ()

    if target in {"po", "do"}:
        if family != "apple2" and source not in {"po", "do"}:
            return False, f".{target} is only valid for Apple II sector-order images.", ()
        return True, "The resolved source matches Apple II logical sector geometry.", ()

    return False, f"Unknown conversion target '{target}'. Supported targets: {', '.join(KNOWN_EXPORTERS)}.", ()


def plan_conversion(context: ConversionContext, target: str) -> ConversionPlan:
    """Return the shared semantic compatibility decision for a conversion."""

    normalised = _normalise_target(target)
    allowed, reason, warnings = _plan_known_target(context, normalised)
    if not allowed:
        return ConversionPlan(normalised, UNSUPPORTED, False, reason, warnings)

    if normalised == "scp":
        classification = LOSSY_BUT_USEFUL if context.source_kind == "scp" else LOGICALLY_EQUIVALENT
    elif normalised == context.source_kind or (normalised == "raw" and context.source_kind in {"img", "raw"}):
        classification = SECTOR_LOSSLESS
    elif normalised == "imd" and _layout_family(context) == "amiga":
        classification = LOSSY_BUT_USEFUL
    elif _physical_loss(context):
        classification = LOGICALLY_EQUIVALENT
    else:
        classification = SECTOR_LOSSLESS

    return ConversionPlan(normalised, classification, True, reason, warnings)


def available_conversion_plans(context: ConversionContext) -> tuple[ConversionPlan, ...]:
    """Return valid destinations for a source, in stable UI/CLI order."""

    plans: list[ConversionPlan] = []
    for target in KNOWN_EXPORTERS:
        plan = plan_conversion(context, target)
        if plan.allowed:
            plans.append(plan)
    return tuple(plans)


__all__ = [
    "ConversionContext",
    "ConversionPlan",
    "SECTOR_LOSSLESS",
    "LOGICALLY_EQUIVALENT",
    "LOSSY_BUT_USEFUL",
    "UNSUPPORTED",
    "available_conversion_plans",
    "plan_conversion",
]
