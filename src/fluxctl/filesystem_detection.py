"""Filesystem detection with evidence and regional notes."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .filesystems import Filesystem, TrackSectorImage, load_builtin_filesystems
from .filesystems.cbm_dos import CBMDOS
from .filesystems.cpm import cpm_disk_parameters_for_layout
from .plugins import registry


@dataclass(frozen=True)
class FilesystemRegion:
    """A filesystem interpretation for part of a disk image."""

    region: str
    filesystem: str
    evidence: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class FilesystemDetection:
    """Structured filesystem detection result."""

    primary: Optional[str]
    confidence: float
    evidence: list[str] = field(default_factory=list)
    regions: list[FilesystemRegion] = field(default_factory=list)
    plugin: Optional[Filesystem] = None

    def to_dict(self) -> dict[str, object]:
        return {
            "primary": self.primary,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "regions": [
                {"region": region.region, "filesystem": region.filesystem, "evidence": region.evidence}
                for region in self.regions
            ],
        }


def detect_filesystem(image, *, path_name: str = "") -> FilesystemDetection:
    """Detect filesystems while retaining evidence and hybrid-region notes."""

    if not registry.filesystem:
        load_builtin_filesystems()

    layout = getattr(image, "layout", None)
    layout_id = getattr(layout, "layout_id", "") or ""

    if layout_id.startswith("commodore_"):
        return _detect_commodore(image, layout_id)

    cpm = registry.filesystem.get("cpm")
    if cpm_disk_parameters_for_layout(layout_id) is not None and cpm and cpm.entry.probe(image):
        return FilesystemDetection(
            primary=_metadata_name("cpm", cpm.entry),
            confidence=0.85,
            evidence=[f"layout={layout_id}", "cpm_dpb_layout=1", "cpm_directory_probe=1"],
            regions=[FilesystemRegion("disk", "cpm", ["CP/M directory entries at modelled DPB directory start"])],
            plugin=cpm.entry,
        )

    for key, plugin in registry.filesystem.items():
        fs = plugin.entry
        if key == "cpm":
            continue
        if fs.probe(image):
            return FilesystemDetection(
                primary=_metadata_name(key, fs),
                confidence=0.95,
                evidence=[f"{key}_probe=1"],
                plugin=fs,
            )

    if cpm and cpm.entry.probe(image):
        return FilesystemDetection(
            primary="cpm",
            confidence=0.55,
            evidence=["cpm_probe=1", "weak_generic_probe=1"],
            plugin=cpm.entry,
        )

    return FilesystemDetection(primary=None, confidence=0.0, evidence=["no_filesystem_probe_matched"])


def _metadata_name(key: str, fs: Filesystem) -> str:
    try:
        metadata = fs.metadata()
    except Exception:
        metadata = {}
    return str(metadata.get("filesystem") or key)


def _detect_commodore(image, layout_id: str) -> FilesystemDetection:
    cbm = registry.filesystem.get("cbm_dos")
    cpm = registry.filesystem.get("cpm")
    evidence: list[str] = [f"layout={layout_id}", "commodore_layout=1"]
    regions: list[FilesystemRegion] = []

    cbm_ok = False
    cbm_fs = cbm.entry if cbm else None
    if cbm_fs is not None:
        try:
            cbm_ok = cbm_fs.probe(image)
        except Exception:
            cbm_ok = False
    if cbm_ok:
        evidence.append("cbm_dos_bam_probe=1")

    cpm_ok = False
    cpm_fs = cpm.entry if cpm else None
    if cpm_fs is not None:
        try:
            cpm_ok = cpm_fs.probe(image)
        except Exception:
            cpm_ok = False
    if cpm_ok:
        evidence.append("cpm_directory_probe=1")

    if layout_id == "commodore_gcr_1571_341k":
        if cbm_ok:
            regions.extend(
                [
                    FilesystemRegion(
                        "head0",
                        "cbm_dos_1541_compatible",
                        ["BAM/directory on track 18 head 0"],
                    ),
                    FilesystemRegion(
                        "head1",
                        "cbm_dos_1571_extended_side",
                        ["1571 GCR second side extends CBM DOS capacity"],
                    ),
                ]
            )
            return FilesystemDetection(
                primary="cbm_dos_1571",
                confidence=0.98,
                evidence=evidence,
                regions=regions,
                plugin=cbm_fs,
            )

        if cpm_ok:
            cpm_name = _metadata_name("cpm", cpm_fs) if cpm_fs else "cpm"
            regions.extend(
                [
                    FilesystemRegion(
                        "disk",
                        cpm_name,
                        ["CP/M directory-like entries detected"],
                    ),
                ]
            )
            return FilesystemDetection(
                primary=cpm_name,
                confidence=0.72,
                evidence=evidence,
                regions=regions,
                plugin=cpm_fs,
            )

        regions.extend(
            [
                FilesystemRegion(
                    "head0",
                    "cbm_dos_1541_compatible",
                    ["1571 GCR layout implies 1541-compatible side"],
                ),
                FilesystemRegion(
                    "head1",
                    "cbm_dos_1571_extended_side",
                    ["1571 GCR second side extends CBM DOS capacity"],
                ),
            ]
        )
        return FilesystemDetection(
            primary="cbm_dos_1571",
            confidence=0.65,
            evidence=evidence + ["layout_1571_cbm_dos_hint=1", "no_strong_filesystem_probe=1"],
            regions=regions,
        )

    if layout_id == "commodore_gcr_1541_170k":
        if cpm_ok:
            cpm_name = "c64_cpm_2_2"
            return FilesystemDetection(
                primary=cpm_name,
                confidence=0.75,
                evidence=evidence,
                regions=[FilesystemRegion("head0", cpm_name, ["CP/M directory-like entries detected"])],
                plugin=cpm_fs,
            )
        if cbm_ok:
            return FilesystemDetection(
                primary="cbm_dos",
                confidence=0.98,
                evidence=evidence + ["cbm_dos_1541=1"],
                regions=[FilesystemRegion("head0", "cbm_dos", ["1541 BAM/directory detected"])],
                plugin=cbm_fs,
            )

        cbm_diagnostics = CBMDOS().diagnostic_evidence(image)
        return FilesystemDetection(
            primary="cbm_dos",
            confidence=0.45,
            evidence=evidence + ["cbm_dos_likely_but_incomplete=1", *cbm_diagnostics],
            regions=[
                FilesystemRegion(
                    "head0",
                    "cbm_dos",
                    ["1541 layout and BAM evidence present, but directory reconstruction is incomplete"],
                )
            ],
        )

    if layout_id == "commodore_gcr_1541_cpm_170k":
        if cpm_ok:
            return FilesystemDetection(
                primary="c64_cpm_2_2",
                confidence=0.75,
                evidence=evidence,
                regions=[FilesystemRegion("head0", "c64_cpm_2_2", ["CP/M directory-like entries detected"])],
                plugin=cpm_fs,
            )
        if cbm_ok:
            return FilesystemDetection(
                primary="cbm_dos",
                confidence=0.68,
                evidence=evidence + ["cbm_dos_1541=1", "layout_cpm_mismatch=1"],
                regions=[FilesystemRegion("head0", "cbm_dos", ["1541 BAM/directory detected"])],
                plugin=cbm_fs,
            )
        return FilesystemDetection(
            primary="c64_cpm_2_2",
            confidence=0.5,
            evidence=evidence + ["layout_cpm_hint=1"],
        )

    if layout_id == "commodore_mfm_1581_800k":
        cbm1581 = registry.filesystem.get("cbm_dos_1581")
        cbm1581_fs = cbm1581.entry if cbm1581 else None
        cbm1581_ok = False
        if cbm1581_fs is not None:
            try:
                cbm1581_ok = cbm1581_fs.probe(image)
            except Exception:
                cbm1581_ok = False
        if cbm1581_ok:
            return FilesystemDetection(
                primary="cbm_dos_1581",
                confidence=0.95,
                evidence=evidence + ["cbm_dos_1581_header_probe=1"],
                regions=[FilesystemRegion("disk", "cbm_dos_1581", ["1581 header and directory detected"])],
                plugin=cbm1581_fs,
            )
        return FilesystemDetection(
            primary="cbm_dos",
            confidence=0.8 if cbm_ok else 0.55,
            evidence=evidence + (["cbm_dos_probe=1"] if cbm_ok else ["layout_cbm_dos_hint=1"]),
            plugin=cbm_fs if cbm_ok else None,
        )

    if "cpm" in layout_id:
        if cpm_ok:
            cpm_name = _metadata_name("cpm", cpm_fs) if cpm_fs else "cpm"
            return FilesystemDetection(
                primary=cpm_name,
                confidence=0.75,
                evidence=evidence,
                regions=[FilesystemRegion("disk", cpm_name, ["CP/M directory-like entries detected"])],
                plugin=cpm_fs,
            )
        return FilesystemDetection(
            primary="c128_cpm_3_0" if "1571" in layout_id else "cpm",
            confidence=0.5,
            evidence=evidence + ["layout_cpm_hint=1"],
        )

    if cbm_ok:
        return FilesystemDetection(
            primary="cbm_dos",
            confidence=0.9,
            evidence=evidence,
            plugin=cbm_fs,
        )

    return FilesystemDetection(primary=None, confidence=0.0, evidence=evidence + ["no_filesystem_probe_matched"])
