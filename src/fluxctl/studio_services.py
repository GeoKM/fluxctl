"""Application services shared by Fluxctl Studio and future frontends."""
from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import sys
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Optional

from . import __version__
from .application.image_operations import (
    doctor_report as _application_doctor_report,
    get_decoder as _get_decoder,
    maybe_hxc_hint as _maybe_hxc_hint,
    prepare_image as _prepare_image,
    prefix_track_count_for_size as _prefix_track_count_for_size,
    probe_flat_image as _probe_flat_image,
    track_in_range as _track_in_range,
)
from .application.models import (
    BlankImagePreset,
    BlankImageResult,
    ExportResult,
    FileAllocationView,
    FileEntryView,
    FileListView,
    GreaseweazleFormat,
    GreaseweazleStatus,
    HardwareReadResult,
    HexDumpView,
    HexEditResult,
    ImageSummary,
    MutationResult,
    ReplaceResult,
    TextView,
)
from .application.command_operations import (
    CommandResult,
    run_fluxctl_command,
)
from .decoding import load_builtin_decoders
from .detection import detect_encoding, detect_layout
from .filesystem_detection import detect_filesystem
from .filesystems import RawSectorImage, TrackSectorImage, load_builtin_filesystems
from .filesystems.cbm_dos import CBMDOS, cbm_file_type_label
from .filesystems.cbm_dos_1581 import CBMDOS1581
from .filesystems.cpm import CPMFilesystem, cpm_disk_parameters_for_layout
from .filesystems.fat12 import FAT12
from .layouts.loader import ensure_layout_loaded, load_builtin_layouts
from .output import atomic_write_bytes
from .plugins import registry
from .reports.map import (
    DiskMap,
    apply_c64_cpm_2_2_logical_overlay,
    build_cbm_bam_block_map,
    build_disk_map,
    build_disk_map_from_tracksectors,
)
from .reports.qc import DiskQCReport, build_qc_report, build_qc_report_from_tracks
from .scp import parse_scp


@lru_cache(maxsize=8)
def _prepare_image_cached(
    path: str,
    modified_ns: int,
    changed_ns: int,
    size: int,
    layout_id: str,
    encoding: str,
):
    """Cache one reconstructed image snapshot for repeated Studio views."""

    del modified_ns, changed_ns, size
    return _prepare_image(Path(path), layout_id or None, encoding)


def _prepare_image_for_studio(path: Path, layout_id: Optional[str], encoding: str):
    """Reconstruct from a metadata-keyed cache, invalidating changed files."""

    resolved = path.expanduser().resolve()
    stat = resolved.stat()
    return _prepare_image_cached(
        str(resolved),
        stat.st_mtime_ns,
        stat.st_ctime_ns,
        stat.st_size,
        layout_id or "",
        encoding,
    )


FAT12_PRESETS = {
    "fat12_180k": {
        "label": "IBM DOS FAT12 180K (.img)",
        "layout_id": "ibm_mfm_180k",
        "total_sectors": 360,
        "media": 0xFC,
        "sectors_per_cluster": 1,
        "root_entries": 64,
        "sectors_per_fat": 2,
        "sectors_per_track": 9,
        "heads": 1,
    },
    "fat12_360k": {
        "label": "IBM DOS FAT12 360K (.img)",
        "layout_id": "ibm_mfm_360k",
        "total_sectors": 720,
        "media": 0xFD,
        "sectors_per_cluster": 2,
        "root_entries": 112,
        "sectors_per_fat": 2,
        "sectors_per_track": 9,
        "heads": 2,
    },
    "fat12_720k": {
        "label": "IBM DOS FAT12 720K (.img)",
        "layout_id": "ibm_mfm_720k",
        "total_sectors": 1440,
        "media": 0xF9,
        "sectors_per_cluster": 2,
        "root_entries": 112,
        "sectors_per_fat": 3,
        "sectors_per_track": 9,
        "heads": 2,
    },
    "fat12_1200k": {
        "label": "IBM DOS FAT12 1.2M (.img)",
        "layout_id": "ibm_mfm_1200k",
        "total_sectors": 2400,
        "media": 0xF9,
        "sectors_per_cluster": 1,
        "root_entries": 224,
        "sectors_per_fat": 7,
        "sectors_per_track": 15,
        "heads": 2,
    },
    "fat12_1440k": {
        "label": "IBM DOS FAT12 1.44M (.img)",
        "layout_id": "ibm_mfm_1440k",
        "total_sectors": 2880,
        "media": 0xF0,
        "sectors_per_cluster": 1,
        "root_entries": 224,
        "sectors_per_fat": 9,
        "sectors_per_track": 18,
        "heads": 2,
    },
}


BLANK_IMAGE_PRESETS: tuple[BlankImagePreset, ...] = tuple(
    BlankImagePreset(
        preset_id=preset_id,
        label=str(spec["label"]),
        suffix=".img",
        layout_id=str(spec["layout_id"]),
        filesystem="fat12",
        size=int(spec["total_sectors"]) * 512,
        description="Formatted MS-DOS FAT12 image compatible with Studio file import and directory creation.",
    )
    for preset_id, spec in FAT12_PRESETS.items()
) + (
    BlankImagePreset(
        "cbm_dos_1541_d64",
        "Commodore 1541 CBM DOS 170K (.d64)",
        ".d64",
        "commodore_gcr_1541_170k",
        "cbm_dos",
        174848,
        "Formatted empty CBM DOS 2A image with BAM and root directory.",
    ),
    BlankImagePreset(
        "cbm_dos_1571_d71",
        "Commodore 1571 CBM DOS 341K (.d71)",
        ".d71",
        "commodore_gcr_1571_341k",
        "cbm_dos_1571",
        349696,
        "Formatted empty two-sided CBM DOS 2A image with BAM and root directory.",
    ),
    BlankImagePreset(
        "cbm_dos_1581_d81",
        "Commodore 1581 CBM DOS 800K (.d81)",
        ".d81",
        "commodore_mfm_1581_800k",
        "cbm_dos_1581",
        819200,
        "Minimal empty CBM DOS 3D directory image.",
    ),
    BlankImagePreset(
        "amiga_ofs_adf",
        "AmigaDOS OFS 880K (.adf)",
        ".adf",
        "amiga_mfm_880k",
        "amiga_ofs",
        901120,
        "Minimal empty AmigaDOS image with DOS boot marker.",
    ),
    BlankImagePreset(
        "cpm_osborne_200k_img",
        "Osborne 1 CP/M SSDD 200K (.img)",
        ".img",
        "osborne_mfm_ssdd_200k",
        "cpm",
        204800,
        "Formatted empty CP/M image for Osborne 1 SSDD 40-track MFM disks.",
    ),
    BlankImagePreset(
        "cpm_kaypro_200k_img",
        "Kaypro II CP/M SSDD 200K (.img)",
        ".img",
        "kaypro_mfm_ssdd_40_200k",
        "cpm",
        204800,
        "Formatted empty CP/M image for Kaypro II SSDD 40-track MFM disks.",
    ),
    BlankImagePreset(
        "cpm_tandy_model4_180k_img",
        "Tandy Model 4 CP/M 2.2 SSDD 180K (.img)",
        ".img",
        "tandy_mfm_ssdd_180k",
        "cpm",
        184320,
        "Formatted empty CP/M image for Tandy Model 4 40-track MFM disks.",
    ),
)


def _legacy_blank_image_presets() -> list[BlankImagePreset]:
    """Return blank image presets supported by Studio."""

    return list(BLANK_IMAGE_PRESETS)


def _legacy_create_blank_image(preset_id: str, output_path: Path, *, overwrite: bool = False) -> BlankImageResult:
    """Create a new blank disk image for a supported Studio preset."""

    preset = _blank_preset_by_id(preset_id)
    output_path = output_path.with_suffix(preset.suffix) if output_path.suffix == "" else output_path
    if preset_id in FAT12_PRESETS:
        payload = _build_blank_fat12_image(FAT12_PRESETS[preset_id])
    elif preset_id == "cbm_dos_1541_d64":
        payload = _build_blank_cbm_dos_image(sides=1)
    elif preset_id == "cbm_dos_1571_d71":
        payload = _build_blank_cbm_dos_image(sides=2)
    elif preset_id == "cbm_dos_1581_d81":
        payload = _build_blank_1581_image()
    elif preset_id == "amiga_ofs_adf":
        payload = _build_blank_amiga_image()
    elif preset_id == "cpm_osborne_200k_img":
        payload = _build_blank_cpm_image("osborne_mfm_ssdd_200k")
    elif preset_id == "cpm_kaypro_200k_img":
        payload = _build_blank_cpm_image("kaypro_mfm_ssdd_40_200k")
    elif preset_id == "cpm_tandy_model4_180k_img":
        payload = _build_blank_cpm_image("tandy_mfm_ssdd_180k")
    else:  # pragma: no cover - guarded by _blank_preset_by_id
        raise ValueError(f"Unsupported blank image preset: {preset_id}")
    atomic_write_bytes(output_path, payload, overwrite=overwrite)
    return BlankImageResult(
        path=str(output_path),
        preset_id=preset.preset_id,
        label=preset.label,
        layout_id=preset.layout_id,
        filesystem=preset.filesystem,
        size=len(payload),
    )


def _blank_preset_by_id(preset_id: str) -> BlankImagePreset:
    for preset in BLANK_IMAGE_PRESETS:
        if preset.preset_id == preset_id:
            return preset
    raise ValueError(f"Unsupported blank image preset: {preset_id}")


def _build_blank_fat12_image(spec: dict) -> bytes:
    total_sectors = int(spec["total_sectors"])
    media = int(spec["media"])
    image = bytearray(total_sectors * 512)
    boot = bytearray(512)
    boot[0:3] = b"\xEB\x3C\x90"
    boot[3:11] = b"FLUXCTL "
    boot[11:13] = (512).to_bytes(2, "little")
    boot[13] = int(spec["sectors_per_cluster"])
    boot[14:16] = (1).to_bytes(2, "little")
    boot[16] = 2
    boot[17:19] = int(spec["root_entries"]).to_bytes(2, "little")
    boot[19:21] = total_sectors.to_bytes(2, "little") if total_sectors <= 0xFFFF else b"\x00\x00"
    boot[21] = media
    boot[22:24] = int(spec["sectors_per_fat"]).to_bytes(2, "little")
    boot[24:26] = int(spec["sectors_per_track"]).to_bytes(2, "little")
    boot[26:28] = int(spec["heads"]).to_bytes(2, "little")
    if total_sectors > 0xFFFF:
        boot[32:36] = total_sectors.to_bytes(4, "little")
    boot[36] = 0
    boot[38] = 0x29
    boot[39:43] = b"FCTL"
    boot[43:54] = b"NO NAME    "
    boot[54:62] = b"FAT12   "
    boot[510:512] = b"\x55\xAA"
    image[:512] = boot
    sectors_per_fat = int(spec["sectors_per_fat"])
    for fat_index in range(2):
        fat_offset = (1 + fat_index * sectors_per_fat) * 512
        image[fat_offset : fat_offset + 3] = bytes([media, 0xFF, 0xFF])
    return bytes(image)


def _build_blank_cbm_dos_image(*, sides: int) -> bytes:
    from .exporters.d64 import DEFAULT_SECTORS_PER_TRACK, SECTOR_SIZE

    sectors_per_track = list(DEFAULT_SECTORS_PER_TRACK) * sides
    image = bytearray(sum(sectors_per_track) * SECTOR_SIZE)

    def offset(track: int, sector: int) -> int:
        return (sum(sectors_per_track[: track - 1]) + sector) * SECTOR_SIZE

    def mark_used(bam: bytearray, track: int, sector: int) -> None:
        bam_offset = 4 + (track - 1) * 4
        byte_offset = bam_offset + 1 + sector // 8
        if bam[byte_offset] & (1 << (sector % 8)):
            bam[byte_offset] &= ~(1 << (sector % 8))
            bam[bam_offset] = max(0, bam[bam_offset] - 1)

    bam = bytearray(SECTOR_SIZE)
    bam[0:2] = bytes([18, 1])
    bam[2] = 0x41
    if sides == 2:
        bam[3] = 0x80
    bam[0x90:0xA0] = b"FLUXCTL BLANK".ljust(16, b"\xA0")
    bam[0xA2:0xA4] = b"2A"
    bam[0xA5:0xA7] = b"\xA0\xA0"
    for track in range(1, 36):
        count = DEFAULT_SECTORS_PER_TRACK[track - 1]
        entry_offset = 4 + (track - 1) * 4
        bam[entry_offset] = count
        for sector in range(count):
            bam[entry_offset + 1 + sector // 8] |= 1 << (sector % 8)
    mark_used(bam, 18, 0)
    mark_used(bam, 18, 1)

    directory = bytearray(SECTOR_SIZE)
    directory[0:2] = b"\x00\xFF"
    image[offset(18, 1) : offset(18, 1) + SECTOR_SIZE] = directory

    if sides == 2:
        side_bam = bytearray(SECTOR_SIZE)
        for track in range(36, 71):
            logical_track_index = track - 36
            count = DEFAULT_SECTORS_PER_TRACK[logical_track_index]
            if track == 53:
                count = 0
            else:
                side_bam[logical_track_index * 3 : logical_track_index * 3 + 3] = _cbm_free_bitmap(count)
            bam[221 + logical_track_index] = count
        image[offset(53, 0) : offset(53, 0) + SECTOR_SIZE] = side_bam
    image[offset(18, 0) : offset(18, 0) + SECTOR_SIZE] = bam
    return bytes(image)


def _cbm_free_bitmap(sector_count: int) -> bytes:
    bitmap = bytearray(3)
    for sector in range(sector_count):
        bitmap[sector // 8] |= 1 << (sector % 8)
    return bytes(bitmap)


def _build_blank_1581_image() -> bytes:
    image = bytearray(819200)

    def offset(track: int, sector: int) -> int:
        return ((track - 1) * 40 + sector) * 256

    def mark_used(bam: bytearray, track: int, sector: int) -> None:
        track_index = (track - 1) if track <= 40 else (track - 41)
        entry_offset = 16 + track_index * 6
        byte_offset = entry_offset + 1 + sector // 8
        if bam[byte_offset] & (1 << (sector % 8)):
            bam[byte_offset] &= ~(1 << (sector % 8))
            bam[entry_offset] = max(0, bam[entry_offset] - 1)

    header = bytearray(256)
    header[0:3] = bytes([40, 3, ord("D")])
    header[4:20] = b"FLUXCTL BLANK".ljust(16, b"\xA0")
    header[22:24] = b"FC"
    header[24] = 0xA0
    header[25:27] = b"3D"
    header[27:29] = b"\xA0\xA0"
    image[offset(40, 0) : offset(40, 0) + 256] = header

    bam1 = bytearray(256)
    bam1[0:8] = bytes([40, 2, ord("D"), 0xBB, ord("F"), ord("C"), 0xC0, 0x00])
    bam2 = bytearray(256)
    bam2[0:8] = bytes([0, 0xFF, ord("D"), 0xBB, ord("F"), ord("C"), 0xC0, 0x00])
    for track in range(1, 81):
        bam = bam1 if track <= 40 else bam2
        track_index = (track - 1) if track <= 40 else (track - 41)
        entry_offset = 16 + track_index * 6
        bam[entry_offset] = 40
        for sector in range(40):
            bam[entry_offset + 1 + sector // 8] |= 1 << (sector % 8)
    for sector in (0, 1, 2, 3):
        mark_used(bam1, 40, sector)
    image[offset(40, 1) : offset(40, 1) + 256] = bam1
    image[offset(40, 2) : offset(40, 2) + 256] = bam2

    directory = bytearray(256)
    directory[0:2] = b"\x00\xFF"
    image[offset(40, 3) : offset(40, 3) + 256] = directory
    return bytes(image)


def _build_blank_amiga_image() -> bytes:
    image = bytearray(901120)
    image[0:4] = b"DOS\0"
    root = bytearray(512)
    root[0:4] = (2).to_bytes(4, "big")
    root[508:512] = (-2).to_bytes(4, "big", signed=True)
    image[880 * 512 : 881 * 512] = root
    return bytes(image)


def _build_blank_cpm_image(layout_id: str) -> bytes:
    layout = ensure_layout_loaded(layout_id)
    params = cpm_disk_parameters_for_layout(layout_id)
    if params is None:  # pragma: no cover - guarded by caller presets
        raise ValueError(f"No modelled CP/M DPB for {layout_id}")
    image = bytearray(b"\xE5" * (layout.tracks * layout.sides * layout.sectors_per_track * layout.sector_size))
    markers = {
        "kaypro_mfm_ssdd_40_200k": b"FLUXCTL CPM KAYPROII",
        "osborne_mfm_ssdd_200k": b"FLUXCTL CPM OSBORNE",
        "tandy_mfm_ssdd_180k": b"FLUXCTL CPM TANDY4",
    }
    marker = markers.get(layout_id, b"FLUXCTL CPM")
    image[: len(marker)] = marker
    directory_start = params.reserved_tracks * params.sectors_per_track * params.sector_size
    directory_size = params.directory_blocks * params.block_size
    image[directory_start : directory_start + directory_size] = b"\xE5" * directory_size
    return bytes(image)


def _greaseweazle_executable() -> Optional[Path]:
    exe_name = "gw.exe" if sys.platform.startswith("win") else "gw"
    venv_candidate = Path(sys.executable).parent / exe_name
    if venv_candidate.exists():
        return venv_candidate
    found = shutil.which("gw")
    return Path(found) if found else None


def _legacy_greaseweazle_status() -> GreaseweazleStatus:
    """Return whether the Greaseweazle CLI is callable from Studio."""

    executable = _greaseweazle_executable()
    if executable is None:
        return GreaseweazleStatus(
            available=False,
            executable="",
            detail="Greaseweazle command `gw` was not found.",
            suggestion="Install with the Fluxctl installer Greaseweazle options, then run fluxctl doctor.",
        )
    return GreaseweazleStatus(
        available=True,
        executable=str(executable),
        detail=f"Greaseweazle command available at {executable}",
    )


def _parse_greaseweazle_formats(help_text: str) -> list[GreaseweazleFormat]:
    """Parse Greaseweazle format ids from `gw read --help` output."""

    formats: set[str] = set()
    in_formats = False
    for line in help_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("FORMAT options:"):
            in_formats = True
            continue
        if not in_formats:
            continue
        if not stripped or stripped.startswith("Supported file suffixes:"):
            break
        for token in stripped.split():
            if "." not in token:
                continue
            if token.startswith("."):
                continue
            if all(char.isalnum() or char in "._-" for char in token):
                formats.add(token)
    return [GreaseweazleFormat(format_id=format_id, label=format_id) for format_id in sorted(formats)]


def _legacy_greaseweazle_formats() -> list[GreaseweazleFormat]:
    """Return Greaseweazle disk formats supported by the installed CLI."""

    executable = _greaseweazle_executable()
    if executable is None:
        return []
    completed = subprocess.run(
        [str(executable), "read", "--help"],
        text=True,
        capture_output=True,
        check=False,
    )
    return _parse_greaseweazle_formats(f"{completed.stdout}\n{completed.stderr}")


def _command_display(args: list[str]) -> str:
    return " ".join(shlex.quote(str(part)) for part in args)


def build_greaseweazle_read_command(
    output: Path,
    *,
    drive: str = "A",
    gw_format: str = "",
    tracks: str = "",
    revs: Optional[int] = None,
) -> list[str]:
    """Build a raw-flux Greaseweazle read command for SCP capture."""

    executable = _greaseweazle_executable()
    if executable is None:
        raise RuntimeError("Greaseweazle command `gw` is not available")
    args = [str(executable), "read", "--drive", drive, "--raw"]
    if gw_format:
        args.extend(["--format", gw_format])
    if tracks:
        args.extend(["--tracks", tracks])
    if revs is not None:
        if revs < 1:
            raise ValueError("Greaseweazle read revolutions must be 1 or greater")
        args.extend(["--revs", str(revs)])
    args.append(str(output))
    return args


def _legacy_read_disk_with_greaseweazle(
    output: Path,
    *,
    drive: str = "A",
    gw_format: str = "",
    tracks: str = "",
    revs: Optional[int] = None,
    overwrite: bool = False,
) -> HardwareReadResult:
    """Read a physical disk via Greaseweazle into a raw SCP image."""

    if output.suffix.lower() != ".scp":
        output = output.with_suffix(".scp")
    if output.exists() and not overwrite:
        raise FileExistsError(f"Output image already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    args = build_greaseweazle_read_command(
        output,
        drive=drive,
        gw_format=gw_format,
        tracks=tracks,
        revs=revs,
    )
    completed = subprocess.run(args, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit status {completed.returncode}"
        raise RuntimeError(f"Greaseweazle read failed: {detail}")
    return HardwareReadResult(
        path=str(output),
        command=args,
        command_display=_command_display(args),
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def doctor_report(hxcfe: Optional[Path] = None) -> dict:
    """Return the same doctor report used by the CLI."""

    return _application_doctor_report(hxcfe)


def _legacy_load_layout_options() -> list[dict[str, object]]:
    """Return layout descriptors in a compact GUI-friendly shape."""

    layouts = load_builtin_layouts()
    return [
        {
            "layout_id": layout.layout_id,
            "name": layout.name,
            "encoding": layout.encoding,
            "tracks": layout.tracks,
            "sides": layout.sides,
            "sectors_per_track": layout.sectors_per_track,
            "sector_size": layout.sector_size,
        }
        for layout in sorted(layouts, key=lambda item: item.layout_id)
    ]


def summarize_image(path: Path, hxcfe: Optional[Path] = None) -> ImageSummary:
    """Probe an image and return the best current interpretation."""

    load_builtin_decoders()
    load_builtin_layouts()
    load_builtin_filesystems()

    if path.suffix.lower() != ".scp":
        candidates = _probe_flat_image(path)
    else:
        image = parse_scp(path)
        hint = None
        if hxcfe:
            hint = _maybe_hxc_hint(path, hxcfe)
        encoding = detect_encoding(image, hint=hint)
        layout = detect_layout(image, encoding.encoding, hint=hint) if encoding else None
        candidates = []
        if layout:
            fs_name = ""
            try:
                image_obj = _prepare_image_for_studio(path, layout.layout.layout_id, layout.layout.encoding)
                fs_detection = detect_filesystem(image_obj)
                fs_name = fs_detection.primary or ""
                fs_evidence = [
                    f"filesystem_confidence={fs_detection.confidence:.2f}",
                    *fs_detection.evidence,
                    *[
                        f"filesystem_region={region.region}:{region.filesystem}"
                        for region in fs_detection.regions
                    ],
                ]
            except Exception:
                fs_name = ""
                fs_evidence = ["filesystem_probe_failed=1"]
            candidates.append(
                {
                    "layout_id": layout.layout.layout_id,
                    "encoding": layout.layout.encoding,
                    "filesystem": fs_name,
                    "score": layout.score,
                    "evidence": (encoding.evidence if encoding else []) + layout.evidence + fs_evidence,
                }
            )
        elif encoding:
            candidates.append(
                {
                    "layout_id": "",
                    "encoding": encoding.encoding,
                    "filesystem": "",
                    "score": encoding.confidence,
                    "evidence": encoding.evidence,
                }
            )

    if not candidates:
        return ImageSummary(str(path), path.stat().st_size, path.suffix.lower().lstrip(".") or "image", "", "", "", 0.0, [])

    best = candidates[0]
    if not isinstance(best, dict):
        best = best.__dict__
    return ImageSummary(
        path=str(path),
        size=path.stat().st_size,
        kind=path.suffix.lower().lstrip(".") or "image",
        layout_id=str(best.get("layout_id") or ""),
        encoding=str(best.get("encoding") or ""),
        filesystem=str(best.get("filesystem") or ""),
        confidence=float(best.get("score") or 0.0),
        evidence=list(best.get("evidence") or []),
    )


def _legacy_build_qc_for_image(path: Path, layout_id: Optional[str], encoding: str = "mfm") -> DiskQCReport:
    """Build a QC report for SCP or flat images."""

    load_builtin_decoders()
    load_builtin_layouts()
    if path.suffix.lower() == ".scp":
        image = parse_scp(path)
        layout = ensure_layout_loaded(layout_id) if layout_id else None
        selected_encoding = layout.encoding if layout else encoding
        decoder = _get_decoder(selected_encoding)
        return build_qc_report(image, decoder, layout=layout)

    image_obj = _prepare_image_for_studio(path, layout_id, encoding)
    if not isinstance(image_obj, TrackSectorImage):
        raise ValueError("Image could not be reconstructed into sector tracks")
    layout = ensure_layout_loaded(layout_id) if layout_id else None
    return build_qc_report_from_tracks(image_obj.tracks, layout=layout, track_step=1)


def _legacy_build_disk_map_for_image(
    path: Path,
    layout_id: Optional[str],
    encoding: str = "mfm",
    map_view: str = "logical",
) -> DiskMap:
    """Build an in-memory disk map for the Studio visualizer."""

    load_builtin_decoders()
    load_builtin_layouts()
    load_builtin_filesystems()
    if map_view == "bam":
        image_obj = _prepare_image_for_studio(path, layout_id, encoding)
        layout = ensure_layout_loaded(layout_id) if layout_id else None
        max_tracks = (
            _prefix_track_count_for_size(layout, path.stat().st_size)
            if layout is not None and path.suffix.lower() in {".d64", ".d71"}
            else None
        )
        detection = detect_filesystem(image_obj)
        if detection.plugin is None or not hasattr(detection.plugin, "bam_blocks"):
            raise ValueError("No CBM DOS BAM is available for this image")
        return build_cbm_bam_block_map(detection.plugin.bam_blocks(max_tracks=max_tracks))

    if path.suffix.lower() == ".scp":
        image = parse_scp(path)
        layout = ensure_layout_loaded(layout_id) if layout_id else None
        selected_encoding = layout.encoding if layout else encoding
        decoder = _get_decoder(selected_encoding)
        disk_map = build_disk_map(image, decoder, layout=layout)
        if map_view == "logical" and layout and layout.layout_id == "commodore_gcr_1541_170k":
            try:
                image_obj = _prepare_image_for_studio(path, layout.layout_id, layout.encoding)
                detection = detect_filesystem(image_obj)
                if detection.primary == "c64_cpm_2_2":
                    allocated = (
                        detection.plugin.allocation_blocks()
                        if detection.plugin is not None and hasattr(detection.plugin, "allocation_blocks")
                        else None
                    )
                    return apply_c64_cpm_2_2_logical_overlay(disk_map, allocated)
            except Exception:
                pass
        return disk_map

    image_obj = _prepare_image_for_studio(path, layout_id, encoding)
    if not isinstance(image_obj, TrackSectorImage):
        raise ValueError("Image could not be reconstructed into sector tracks")
    disk_map = build_disk_map_from_tracksectors(image_obj.tracks, layout=layout)
    if map_view == "logical" and layout_id == "commodore_gcr_1541_170k":
        try:
            detection = detect_filesystem(image_obj)
            if detection.primary == "c64_cpm_2_2":
                allocated = (
                    detection.plugin.allocation_blocks()
                    if detection.plugin is not None and hasattr(detection.plugin, "allocation_blocks")
                    else None
                )
                return apply_c64_cpm_2_2_logical_overlay(disk_map, allocated)
        except Exception:
            pass
    return disk_map


def build_qc_for_image(path: Path, layout_id: Optional[str], encoding: str = "mfm") -> DiskQCReport:
    from .application.report_operations import build_qc_for_image as operation

    return operation(path, layout_id, encoding)


def build_disk_map_for_image(
    path: Path,
    layout_id: Optional[str],
    encoding: str = "mfm",
    map_view: str = "logical",
) -> DiskMap:
    from .application.report_operations import build_disk_map_for_image as operation

    return operation(path, layout_id, encoding, map_view)


def _join_filesystem_path(directory: str, name: str) -> str:
    parts = [part for part in directory.strip("/").split("/") if part]
    parts.append(name)
    return "/" + "/".join(parts)


def _legacy_format_hex_dump(data: bytes, *, width: int = 16, max_bytes: Optional[int] = None) -> str:
    """Render bytes as offset, hex, and ASCII columns."""

    if width <= 0:
        raise ValueError("Hex dump width must be positive")
    shown = data[:max_bytes] if max_bytes is not None else data
    lines: list[str] = []
    for offset in range(0, len(shown), width):
        chunk = shown[offset : offset + width]
        hex_bytes = " ".join(f"{byte:02X}" for byte in chunk)
        padded_hex = hex_bytes.ljust(width * 3 - 1)
        ascii_bytes = "".join(chr(byte) if 32 <= byte < 127 else "." for byte in chunk)
        lines.append(f"{offset:08X}  {padded_hex}  |{ascii_bytes}|")
    if max_bytes is not None and len(data) > max_bytes:
        lines.append(f"... truncated, showing {max_bytes:,} of {len(data):,} bytes")
    return "\n".join(lines)


def _legacy_parse_hex_dump_text(text: str, *, expected_size: Optional[int] = None) -> bytes:
    """Parse an edited Studio hex dump back into bytes.

    The parser accepts Fluxctl's offset/hex/ASCII dump format and intentionally
    ignores the ASCII column. Offsets must be contiguous so accidental line
    deletion, wrapping damage, or pasted prose is caught before anything writes.
    """

    payload = bytearray()
    expected_offset = 0
    parsed_any = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("... truncated"):
            continue
        if "|" in line:
            line = line.split("|", 1)[0].rstrip()
        parts = line.split()
        if not parts:
            continue
        try:
            offset = int(parts[0], 16)
        except ValueError as exc:
            raise ValueError(f"Invalid hex dump offset: {parts[0]!r}") from exc
        if offset != expected_offset:
            raise ValueError(f"Hex dump offset jumps from {expected_offset:08X} to {offset:08X}")
        line_bytes = bytearray()
        for token in parts[1:]:
            if len(token) != 2:
                raise ValueError(f"Invalid hex byte token: {token!r}")
            try:
                line_bytes.append(int(token, 16))
            except ValueError as exc:
                raise ValueError(f"Invalid hex byte token: {token!r}") from exc
        payload.extend(line_bytes)
        expected_offset += len(line_bytes)
        parsed_any = True
    if not parsed_any:
        raise ValueError("No hex bytes found in edited dump")
    if expected_size is not None and len(payload) != expected_size:
        raise ValueError(f"Edited data is {len(payload)} bytes; expected {expected_size} bytes")
    return bytes(payload)


def _legacy_apply_ascii_hex_dump_edits(text: str, original_data: bytes, *, width: int = 16) -> bytes:
    """Apply edited ASCII-column characters to a Fluxctl hex dump.

    Non-printable source bytes are rendered as ``.``. Keeping that character
    preserves the original byte, so merely synchronising an unchanged ASCII
    column cannot turn every non-printable byte into a literal full stop.
    """

    if width <= 0:
        raise ValueError("Hex dump width must be positive")
    payload = bytearray(original_data)
    expected_offset = 0
    parsed_any = False
    for raw_line in text.splitlines():
        if not raw_line or raw_line.startswith("... truncated"):
            continue
        marker = raw_line.find("|")
        if marker < 0:
            raise ValueError("ASCII edit requires a complete Fluxctl hex dump line")
        closing_marker = raw_line.find("|", marker + 1)
        if closing_marker < 0:
            raise ValueError("ASCII edit requires a closing ASCII column marker")
        if raw_line[closing_marker + 1 :].strip():
            raise ValueError("Unexpected text after ASCII column")
        prefix_parts = raw_line[:marker].split()
        if not prefix_parts:
            raise ValueError("ASCII edit is missing a hex dump offset")
        try:
            offset = int(prefix_parts[0], 16)
        except ValueError as exc:
            raise ValueError(f"Invalid hex dump offset: {prefix_parts[0]!r}") from exc
        if offset != expected_offset:
            raise ValueError(f"Hex dump offset jumps from {expected_offset:08X} to {offset:08X}")
        row_size = min(width, len(original_data) - offset)
        if row_size <= 0:
            raise ValueError(f"Hex dump offset {offset:08X} is beyond the edited data")
        ascii_text = raw_line[marker + 1 : closing_marker]
        if len(ascii_text) != row_size:
            raise ValueError(
                f"ASCII column at {offset:08X} contains {len(ascii_text)} characters; expected {row_size}"
            )
        for index, character in enumerate(ascii_text):
            source_byte = original_data[offset + index]
            rendered = chr(source_byte) if 32 <= source_byte < 127 else "."
            if character == rendered:
                continue
            codepoint = ord(character)
            if not 32 <= codepoint < 127:
                raise ValueError("ASCII edits must use printable 7-bit characters; edit other bytes as hex")
            payload[offset + index] = codepoint
        expected_offset += row_size
        parsed_any = True
    if not parsed_any:
        raise ValueError("No ASCII bytes found in edited dump")
    if expected_offset != len(original_data):
        raise ValueError(f"Edited ASCII data is {expected_offset} bytes; expected {len(original_data)} bytes")
    return bytes(payload)


def _legacy_sector_hex_dump(
    path: Path,
    layout_id: Optional[str],
    encoding: str,
    track: int,
    head: int,
    sector_id: int,
    *,
    max_bytes: Optional[int] = None,
) -> HexDumpView:
    """Return a hex dump for one decoded physical sector."""

    image = _prepare_image_for_studio(path, layout_id, encoding)
    if not isinstance(image, TrackSectorImage):
        raise ValueError("Image could not be reconstructed into sector tracks")
    try:
        data = image._sector_lookup[(track, head, sector_id)]
    except KeyError as exc:
        raise ValueError(f"Sector {track}:{head}:{sector_id} is not available") from exc
    title = f"Sector T{track} H{head} S{sector_id}"
    return HexDumpView(
        title=title,
        size=len(data),
        text=_legacy_format_hex_dump(data, max_bytes=max_bytes),
        data=data,
        source_kind="sector",
        track=track,
        head=head,
        sector=sector_id,
    )


def _legacy_sector_list(
    path: Path,
    layout_id: Optional[str],
    encoding: str,
    track: int,
    head: int,
) -> TextView:
    """Return a decoded sector listing for one physical track/head row."""

    image = _prepare_image_for_studio(path, layout_id, encoding)
    if not isinstance(image, TrackSectorImage):
        raise ValueError("Image could not be reconstructed into sector tracks")
    selected = None
    for track_sectors in image.tracks:
        if track_sectors.track == track and track_sectors.head == head:
            selected = track_sectors
            break
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
    return TextView(title=f"Sectors T{track} H{head}", text="\n".join(lines))


def _legacy_file_hex_dump(
    path: Path,
    layout_id: Optional[str],
    encoding: str,
    file_path: str,
    *,
    max_bytes: Optional[int] = None,
) -> HexDumpView:
    """Return a hex dump for one filesystem file."""

    load_builtin_filesystems()
    image = _prepare_image_for_studio(path, layout_id, encoding)
    filesystem = detect_filesystem(image).plugin
    if filesystem is None:
        raise ValueError("No supported filesystem is available")
    data = filesystem.extract_file(file_path)
    return HexDumpView(
        title=f"File {file_path}",
        size=len(data),
        text=_legacy_format_hex_dump(data, max_bytes=max_bytes),
        data=data,
        source_kind="file",
        file_path=file_path,
    )


def _legacy_file_allocation_for_image(
    path: Path,
    layout_id: Optional[str],
    encoding: str,
    file_path: str,
) -> FileAllocationView:
    """Return sector addresses occupied by a filesystem file when supported."""

    load_builtin_filesystems()
    image = _prepare_image_for_studio(path, layout_id, encoding)
    filesystem = detect_filesystem(image).plugin
    if filesystem is None:
        raise ValueError("No supported filesystem is available")
    if not hasattr(filesystem, "file_sector_addresses"):
        raise ValueError("This filesystem does not expose file sector allocation yet")
    logical_sectors = (
        filesystem.logical_file_sector_addresses(file_path)
        if hasattr(filesystem, "logical_file_sector_addresses")
        else None
    )
    return FileAllocationView(file_path, filesystem.file_sector_addresses(file_path), logical_sectors)


def _mount_filesystem(path: Path, layout_id: Optional[str], encoding: str):
    load_builtin_filesystems()
    image = _prepare_image_for_studio(path, layout_id, encoding)
    filesystem = detect_filesystem(image).plugin
    if filesystem is None:
        raise ValueError("No supported filesystem is available")
    return filesystem


def _filesystem_parent(path: str) -> str:
    parts = [part for part in path.strip("/").split("/") if part]
    if len(parts) <= 1:
        return "/"
    return "/" + "/".join(parts[:-1])


def _filesystem_basename(path: str) -> str:
    parts = [part for part in path.strip("/").split("/") if part]
    return parts[-1] if parts else ""


def _safe_export_name(name: str) -> str:
    safe = "".join(char if char not in '/\\:\0' else "_" for char in name).strip()
    return safe or "unnamed"


def _find_entry(filesystem, fs_path: str) -> FileEntryView:
    # Classic CBM DOS permits '/' in root filenames, even though Studio uses
    # '/' as its filesystem path separator. Match the complete root name
    # before interpreting the path as a directory traversal.
    root_name = fs_path.strip("/")
    if root_name:
        for entry in filesystem.list_directory("/"):
            if entry.name.casefold() == root_name.casefold():
                return FileEntryView(
                    entry.name,
                    "<DIR>" if entry.is_dir else "file",
                    entry.size,
                    _join_filesystem_path("/", entry.name),
                    entry.is_dir,
                )
    parent = _filesystem_parent(fs_path)
    name = _filesystem_basename(fs_path)
    if not name:
        raise ValueError("Choose a file or directory entry to export")
    for entry in filesystem.list_directory(parent):
        if entry.name.lower() == name.lower():
            return FileEntryView(
                entry.name,
                "<DIR>" if entry.is_dir else "file",
                entry.size,
                _join_filesystem_path(parent, entry.name),
                entry.is_dir,
            )
    raise ValueError(f"Filesystem entry '{fs_path}' was not found")


def _legacy_export_filesystem_entry(
    path: Path,
    layout_id: Optional[str],
    encoding: str,
    fs_path: str,
    destination: Path,
    overwrite: bool = False,
) -> ExportResult:
    """Export a selected filesystem file or directory to the host filesystem."""

    filesystem = _mount_filesystem(path, layout_id, encoding)
    entry = _find_entry(filesystem, fs_path)
    if entry.is_dir:
        return _export_directory(filesystem, entry.path, destination, overwrite=overwrite)
    data = filesystem.extract_file(entry.path)
    atomic_write_bytes(destination, data, overwrite=overwrite)
    return ExportResult(path=str(destination), files=1, bytes=len(data))


def _legacy_export_filesystem_entries(
    path: Path,
    layout_id: Optional[str],
    encoding: str,
    fs_paths: list[str],
    destination_parent: Path,
    overwrite: bool = False,
) -> ExportResult:
    """Export multiple selected filesystem entries into one host folder."""

    if not fs_paths:
        raise ValueError("Choose one or more filesystem entries to export")
    filesystem = _mount_filesystem(path, layout_id, encoding)
    destination_parent.mkdir(parents=True, exist_ok=True)
    files = 0
    byte_count = 0
    exported_paths: list[str] = []
    with tempfile.TemporaryDirectory(prefix=".fluxctl-export.", dir=destination_parent) as temp_name:
        temp_path = Path(temp_name)
        for fs_path in fs_paths:
            entry = _find_entry(filesystem, fs_path)
            if entry.is_dir:
                directory_result = _export_directory(filesystem, entry.path, temp_path, overwrite=overwrite)
                files += directory_result.files
                byte_count += directory_result.bytes
                exported_paths.append(Path(directory_result.path).name)
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
            final_path = destination_parent / name
            if (final_path.exists() or final_path.is_symlink()) and not overwrite:
                raise ValueError(f"Export destination already exists: {final_path}")
        for child in temp_path.iterdir():
            final_path = destination_parent / child.name
            if final_path.exists() or final_path.is_symlink():
                _remove_export_target(final_path)
            shutil.move(str(child), str(final_path))
    return ExportResult(path=str(destination_parent), files=files, bytes=byte_count)


def _legacy_replace_file_with_copy(
    path: Path,
    layout_id: Optional[str],
    encoding: str,
    fs_path: str,
    replacement_path: Path,
    output_path: Path,
) -> ReplaceResult:
    """Replace one file in a new image copy without modifying the original.

    Replacement writes only to a new image copy. FAT12 replacement may grow by
    allocating free clusters; CBM DOS replacement allocates a new block chain
    and keeps the existing directory entry name.
    """

    source = path.resolve()
    output = output_path.resolve()
    if source == output:
        raise ValueError("Output image must be a new copy, not the original image")
    if output_path.exists():
        raise ValueError(f"Output image already exists: {output_path}")

    replacement = replacement_path.read_bytes()
    suffix = path.suffix.lower()
    image_bytes = path.read_bytes()
    if suffix == ".img":
        filesystem = _probe_fat12_bytes(image_bytes)
        patched = filesystem.replace_file_allocating_clusters(image_bytes, fs_path, replacement)
        filesystem_name = "fat12"
    elif suffix in {".d64", ".d71"}:
        filesystem = _probe_cbm_dos_bytes(image_bytes)
        patched = filesystem.replace_file(image_bytes, fs_path, replacement)
        filesystem_name = "cbm_dos_1571" if suffix == ".d71" else "cbm_dos"
    elif suffix == ".d81":
        filesystem = _probe_cbm_dos_1581_bytes(image_bytes)
        patched = filesystem.replace_file(image_bytes, fs_path, replacement)
        filesystem_name = "cbm_dos_1581"
    else:
        raise ValueError("File replacement is currently supported only for FAT12 .img and CBM DOS .d64/.d71/.d81 images")
    atomic_write_bytes(output_path, patched, source_paths=[path])
    return ReplaceResult(
        path=str(output_path),
        file_path=fs_path,
        bytes=len(replacement),
        filesystem=filesystem_name,
    )


def _legacy_replace_file_bytes_with_copy(
    path: Path,
    layout_id: Optional[str],
    encoding: str,
    fs_path: str,
    replacement: bytes,
    output_path: Path,
) -> HexEditResult:
    """Replace one filesystem file with edited bytes in a new image copy."""

    source = path.resolve()
    output = output_path.resolve()
    if source == output:
        raise ValueError("Output image must be a new copy, not the original image")
    if output_path.exists():
        raise ValueError(f"Output image already exists: {output_path}")

    suffix = path.suffix.lower()
    image_bytes = path.read_bytes()
    if suffix == ".img":
        filesystem = _probe_fat12_bytes(image_bytes)
        patched = filesystem.replace_file_allocating_clusters(image_bytes, fs_path, replacement)
    elif suffix in {".d64", ".d71"}:
        filesystem = _probe_cbm_dos_bytes(image_bytes)
        patched = filesystem.replace_file(image_bytes, fs_path, replacement)
    elif suffix == ".d81":
        filesystem = _probe_cbm_dos_1581_bytes(image_bytes)
        patched = filesystem.replace_file(image_bytes, fs_path, replacement)
    else:
        raise ValueError(
            "Advanced file hex editing is currently supported only for FAT12 .img and CBM DOS .d64/.d71/.d81 images"
        )
    _write_new_image_copy(output_path, patched)
    return HexEditResult(path=str(output_path), target=fs_path, bytes=len(replacement), mode="file")


def _legacy_replace_flat_sector_bytes_with_copy(
    path: Path,
    layout_id: str,
    track: int,
    head: int,
    sector_id: int,
    replacement: bytes,
    output_path: Path,
) -> HexEditResult:
    """Replace one decoded sector in a new flat image copy.

    This intentionally rejects flux and structured exchange containers. Editing
    an SCP/IMD sector requires a real encoder for that container, which Fluxctl
    does not yet have.
    """

    source = path.resolve()
    output = output_path.resolve()
    if source == output:
        raise ValueError("Output image must be a new copy, not the original image")
    if output_path.exists():
        raise ValueError(f"Output image already exists: {output_path}")
    if path.suffix.lower() not in {".img", ".ima", ".raw", ".adf", ".d64", ".d71", ".d81"}:
        raise ValueError("Advanced sector hex editing currently requires a flat sector image container")

    layout = ensure_layout_loaded(layout_id)
    offset, sector_size = _flat_sector_offset(layout, track, head, sector_id)
    if len(replacement) != sector_size:
        raise ValueError(f"Edited sector is {len(replacement)} bytes; sector requires {sector_size} bytes")
    image_bytes = bytearray(path.read_bytes())
    if offset + sector_size > len(image_bytes):
        raise ValueError("Target sector exceeds image size")
    image_bytes[offset : offset + sector_size] = replacement
    _write_new_image_copy(output_path, bytes(image_bytes))
    return HexEditResult(
        path=str(output_path),
        target=f"T{track} H{head} S{sector_id}",
        bytes=len(replacement),
        mode="sector",
    )


def _legacy_delete_filesystem_entry_with_copy(
    path: Path,
    layout_id: Optional[str],
    encoding: str,
    fs_path: str,
    output_path: Path,
) -> MutationResult:
    """Delete one supported filesystem entry in a new image copy."""

    suffix = path.suffix.lower()
    image_bytes = _read_source_for_mutation(path, output_path)
    if suffix == ".img":
        if _is_modelled_cpm_layout(layout_id):
            filesystem = _probe_cpm_bytes(image_bytes, layout_id)
            entry = _find_entry(filesystem, fs_path)
            patched = filesystem.delete_entry(image_bytes, fs_path)
            filesystem_name = "cpm"
        else:
            filesystem = _probe_fat12_bytes(image_bytes)
            entry = _find_entry(filesystem, fs_path)
            patched = filesystem.delete_entry(image_bytes, fs_path)
            filesystem_name = "fat12"
    elif suffix in {".d64", ".d71"}:
        filesystem = _probe_cbm_dos_bytes(image_bytes)
        entries = filesystem.list_directory("/")
        entry = next((item for item in entries if item.name.upper() == fs_path.lstrip("/").upper()), None)
        if entry is None:
            raise ValueError(f"File not found: {fs_path}")
        patched = filesystem.delete_entry(image_bytes, fs_path)
        filesystem_name = "cbm_dos_1571" if suffix == ".d71" else "cbm_dos"
    elif suffix == ".d81":
        filesystem = _probe_cbm_dos_1581_bytes(image_bytes)
        entries = filesystem.list_directory("/")
        entry = next((item for item in entries if item.name.upper() == fs_path.lstrip("/").upper()), None)
        if entry is None:
            raise ValueError(f"File not found: {fs_path}")
        patched = filesystem.delete_entry(image_bytes, fs_path)
        filesystem_name = "cbm_dos_1581"
    else:
        raise ValueError(
            "Delete is currently supported only for FAT12 .img, modelled CP/M .img, "
            "and CBM DOS .d64/.d71/.d81 images"
        )
    _write_new_image_copy(output_path, patched)
    return MutationResult(str(output_path), "delete", 1, entry.size, filesystem_name)


def _legacy_create_directory_with_copy(
    path: Path,
    layout_id: Optional[str],
    encoding: str,
    parent: str,
    name: str,
    output_path: Path,
) -> MutationResult:
    """Create one empty supported filesystem directory in a new image copy."""

    suffix = path.suffix.lower()
    if suffix == ".img":
        image_bytes = _read_fat12_source_for_mutation(path, output_path)
        filesystem = _probe_fat12_bytes(image_bytes)
        patched = filesystem.create_directory(image_bytes, parent, name)
        filesystem_name = "fat12"
    elif suffix == ".d81":
        image_bytes = _read_source_for_mutation(path, output_path)
        filesystem = _probe_cbm_dos_1581_bytes(image_bytes)
        patched = filesystem.create_directory(image_bytes, parent, name)
        filesystem_name = "cbm_dos_1581"
    else:
        raise ValueError("Directory creation is currently supported only for FAT12 .img and CBM DOS 1581 .d81 images")
    _write_new_image_copy(output_path, patched)
    return MutationResult(str(output_path), "create-directory", 1, 0, filesystem_name)


def _legacy_import_file_with_copy(
    path: Path,
    layout_id: Optional[str],
    encoding: str,
    directory: str,
    host_file: Path,
    output_path: Path,
) -> MutationResult:
    """Import one host file into a supported filesystem in a new image copy."""

    data = host_file.read_bytes()
    suffix = path.suffix.lower()
    if suffix == ".img":
        image_bytes = _read_fat12_source_for_mutation(path, output_path)
        if _is_modelled_cpm_layout(layout_id):
            filesystem = _probe_cpm_bytes(image_bytes, layout_id)
            patched = filesystem.import_file(image_bytes, directory, host_file.name, data)
            filesystem_name = "cpm"
        else:
            filesystem = _probe_fat12_bytes(image_bytes)
            patched = filesystem.import_file(image_bytes, directory, host_file.name, data)
            filesystem_name = "fat12"
    elif suffix in {".d64", ".d71"}:
        image_bytes = _read_source_for_mutation(path, output_path)
        filesystem = _probe_cbm_dos_bytes(image_bytes)
        patched = filesystem.import_file(image_bytes, directory, host_file.name, data)
        filesystem_name = "cbm_dos_1571" if suffix == ".d71" else "cbm_dos"
    elif suffix == ".d81":
        image_bytes = _read_source_for_mutation(path, output_path)
        filesystem = _probe_cbm_dos_1581_bytes(image_bytes)
        patched = filesystem.import_file(image_bytes, directory, host_file.name, data)
        filesystem_name = "cbm_dos_1581"
    else:
        raise ValueError("File import is currently supported only for FAT12 .img and CBM DOS .d64/.d71/.d81 images")
    _write_new_image_copy(output_path, patched)
    return MutationResult(str(output_path), "import-file", 1, len(data), filesystem_name)


def _legacy_import_directory_with_copy(
    path: Path,
    layout_id: Optional[str],
    encoding: str,
    directory: str,
    host_directory: Path,
    output_path: Path,
) -> MutationResult:
    """Recursively import a host directory tree into a supported image copy."""

    if not host_directory.is_dir():
        raise ValueError("Choose a host directory to import")
    suffix = path.suffix.lower()
    if suffix == ".img":
        image_bytes = _read_fat12_source_for_mutation(path, output_path)
        entries, byte_count, patched = _import_directory_tree(image_bytes, directory, host_directory)
        filesystem_name = "fat12"
    elif suffix == ".d81":
        image_bytes = _read_source_for_mutation(path, output_path)
        entries, byte_count, patched = _import_1581_directory_tree(image_bytes, directory, host_directory)
        filesystem_name = "cbm_dos_1581"
    else:
        raise ValueError("Directory import is currently supported only for FAT12 .img and CBM DOS 1581 .d81 images")
    _write_new_image_copy(output_path, patched)
    return MutationResult(str(output_path), "import-directory", entries, byte_count, filesystem_name)


def _read_fat12_source_for_mutation(path: Path, output_path: Path) -> bytes:
    if path.suffix.lower() != ".img":
        raise ValueError("FAT12 mutation is currently supported only for flat .img images")
    return _read_source_for_mutation(path, output_path)


def _read_source_for_mutation(path: Path, output_path: Path) -> bytes:
    source = path.resolve()
    output = output_path.resolve()
    if source == output:
        raise ValueError("Output image must be a new copy, not the original image")
    if output_path.exists():
        raise ValueError(f"Output image already exists: {output_path}")
    return path.read_bytes()


def _probe_fat12_bytes(image_bytes: bytes) -> FAT12:
    filesystem = FAT12()
    if not filesystem.probe(RawSectorImage(image_bytes)):
        raise ValueError("FAT12 mutation is currently supported only for FAT12 images")
    return filesystem


def _is_modelled_cpm_layout(layout_id: Optional[str]) -> bool:
    return layout_id is not None and cpm_disk_parameters_for_layout(layout_id) is not None


def _probe_cpm_bytes(image_bytes: bytes, layout_id: Optional[str]) -> CPMFilesystem:
    if layout_id is None:
        raise ValueError("CP/M import needs a modelled layout")
    layout = ensure_layout_loaded(layout_id)
    params = cpm_disk_parameters_for_layout(layout_id)
    if params is None:
        raise ValueError("CP/M import is currently supported only for modelled CP/M .img layouts")
    filesystem = CPMFilesystem()
    image = RawSectorImage(image_bytes, bytes_per_sector=params.sector_size)
    image.layout = layout
    if not filesystem.probe(image):
        raise ValueError("CP/M import is currently supported only for formatted modelled CP/M images")
    return filesystem


def _probe_cbm_dos_bytes(image_bytes: bytes) -> CBMDOS:
    filesystem = CBMDOS()
    if not filesystem.probe(RawSectorImage(image_bytes, bytes_per_sector=256)):
        raise ValueError("CBM DOS import is currently supported only for formatted .d64/.d71 images")
    return filesystem


def _probe_cbm_dos_1581_bytes(image_bytes: bytes) -> CBMDOS1581:
    filesystem = CBMDOS1581()
    if not filesystem.probe(RawSectorImage(image_bytes, bytes_per_sector=256)):
        raise ValueError("1581 import is currently supported only for formatted .d81 images")
    return filesystem


def _write_new_image_copy(output_path: Path, image_bytes: bytes) -> None:
    atomic_write_bytes(output_path, image_bytes)


def _flat_sector_offset(layout, track: int, head: int, sector_id: int) -> tuple[int, int]:
    if track < 0 or head < 0:
        raise ValueError("Track and head must be non-negative")
    if track >= layout.tracks:
        raise ValueError(f"Track {track} is outside layout {layout.layout_id}")
    if head >= layout.sides:
        raise ValueError(f"Head {head} is outside layout {layout.layout_id}")

    sectors_per_cylinder = list(layout.track_sectors) if layout.track_sectors else [layout.sectors_per_track] * layout.tracks
    if len(sectors_per_cylinder) < layout.tracks:
        sectors_per_cylinder.extend([layout.sectors_per_track] * (layout.tracks - len(sectors_per_cylinder)))

    def row_sizes(row_track: int, row_head: int) -> list[int]:
        sectors_on_track = int(sectors_per_cylinder[row_track])
        sector_size = int(layout.sector_size)
        sector_sizes = list(layout.sector_sizes) if layout.sector_sizes else None
        if layout.track_overrides:
            for override in layout.track_overrides:
                if _track_in_range(str(override.get("track_range", "")), row_track) and (
                    override.get("head") is None or override.get("head") == row_head
                ):
                    sectors_on_track = int(override.get("sectors_per_track", sectors_on_track))
                    sector_size = int(override.get("sector_size", sector_size))
                    sector_sizes = list(override.get("sector_sizes", [])) or None
                    break
        return [int(size) for size in sector_sizes] if sector_sizes else [sector_size] * sectors_on_track

    side_blocked_flat = layout.layout_id == "commodore_gcr_1571_341k"
    rows = (
        ((cylinder, row_head) for row_head in range(layout.sides) for cylinder in range(layout.tracks))
        if side_blocked_flat
        else ((cylinder, row_head) for cylinder in range(layout.tracks) for row_head in range(layout.sides))
    )
    sector_base = int(layout.id_rules.get("sector_number_base", 1))
    offset = 0
    for row_track, row_head in rows:
        sizes = row_sizes(row_track, row_head)
        if row_track == track and row_head == head:
            sector_index = sector_id - sector_base
            if sector_index < 0 or sector_index >= len(sizes):
                raise ValueError(f"Sector {sector_id} is outside track {track} head {head}")
            return offset + sum(sizes[:sector_index]), sizes[sector_index]
        offset += sum(sizes)
    raise ValueError(f"Sector T{track} H{head} S{sector_id} is outside layout {layout.layout_id}")


def _import_directory_tree(image_bytes: bytes, directory: str, host_directory: Path) -> tuple[int, int, bytes]:
    filesystem = _probe_fat12_bytes(image_bytes)
    patched = filesystem.create_directory(image_bytes, directory, host_directory.name)
    target_directory = _join_filesystem_path(directory, host_directory.name)
    entries = 1
    byte_count = 0
    for child in sorted(host_directory.iterdir(), key=lambda item: item.name.upper()):
        if child.name.startswith("."):
            continue
        if child.is_dir():
            child_entries, child_bytes, patched = _import_directory_tree(patched, target_directory, child)
            entries += child_entries
            byte_count += child_bytes
            continue
        if child.is_file():
            data = child.read_bytes()
            filesystem = _probe_fat12_bytes(patched)
            patched = filesystem.import_file(patched, target_directory, child.name, data)
            entries += 1
            byte_count += len(data)
    return entries, byte_count, patched


def _import_1581_directory_tree(image_bytes: bytes, directory: str, host_directory: Path) -> tuple[int, int, bytes]:
    filesystem = _probe_cbm_dos_1581_bytes(image_bytes)
    patched = filesystem.create_directory(image_bytes, directory, host_directory.name)
    target_directory = _join_filesystem_path(directory, host_directory.name)
    entries = 1
    byte_count = 0
    for child in sorted(host_directory.iterdir(), key=lambda item: item.name.upper()):
        if child.name.startswith("."):
            continue
        if child.is_dir():
            child_entries, child_bytes, patched = _import_1581_directory_tree(patched, target_directory, child)
            entries += child_entries
            byte_count += child_bytes
            continue
        if child.is_file():
            filesystem = _probe_cbm_dos_1581_bytes(patched)
            data = child.read_bytes()
            patched = filesystem.import_file(patched, target_directory, child.name, data)
            entries += 1
            byte_count += len(data)
    return entries, byte_count, patched


def _export_directory(
    filesystem,
    fs_path: str,
    destination_parent: Path,
    *,
    overwrite: bool = False,
) -> ExportResult:
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
    return ExportResult(path=str(final_path), files=files, bytes=byte_count)


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


def _legacy_list_files(
    path: Path,
    layout_id: Optional[str],
    encoding: str = "mfm",
    directory: str = "/",
) -> list[FileEntryView]:
    """Return directory entries when a supported filesystem is detected."""

    return list_files_with_info(path, layout_id, encoding, directory).entries


def _legacy_list_files_with_info(
    path: Path,
    layout_id: Optional[str],
    encoding: str = "mfm",
    directory: str = "/",
) -> FileListView:
    """Return directory entries and filesystem label/header text for display."""

    load_builtin_filesystems()
    image = _prepare_image_for_studio(path, layout_id, encoding)
    filesystem = detect_filesystem(image).plugin
    if filesystem is None:
        return FileListView([])
    volume_text = _filesystem_volume_text(filesystem)
    try:
        entries = filesystem.list_directory(directory)
    except Exception:
        return FileListView([], volume_text)
    return FileListView(
        [
            FileEntryView(
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


def list_files(path: Path, layout_id: Optional[str], encoding: str = "mfm", directory: str = "/"):
    from .application.filesystem_operations import list_files as operation

    return operation(path, layout_id, encoding, directory)


def list_files_with_info(
    path: Path,
    layout_id: Optional[str],
    encoding: str = "mfm",
    directory: str = "/",
):
    from .application.filesystem_operations import list_files_with_info as operation

    return operation(path, layout_id, encoding, directory)


def file_allocation_for_image(path: Path, layout_id: Optional[str], encoding: str, file_path: str):
    from .application.filesystem_operations import file_allocation_for_image as operation

    return operation(path, layout_id, encoding, file_path)


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
    from .application.filesystem_operations import sector_hex_dump as operation

    return operation(path, layout_id, encoding, track, head, sector_id, max_bytes=max_bytes)


def sector_list(path: Path, layout_id: Optional[str], encoding: str, track: int, head: int):
    from .application.filesystem_operations import sector_list as operation

    return operation(path, layout_id, encoding, track, head)


def file_hex_dump(
    path: Path,
    layout_id: Optional[str],
    encoding: str,
    file_path: str,
    *,
    max_bytes: Optional[int] = None,
):
    from .application.filesystem_operations import file_hex_dump as operation

    return operation(path, layout_id, encoding, file_path, max_bytes=max_bytes)


def export_filesystem_entry(
    path: Path,
    layout_id: Optional[str],
    encoding: str,
    fs_path: str,
    destination: Path,
    overwrite: bool = False,
):
    from .application.filesystem_operations import export_filesystem_entry as operation

    return operation(path, layout_id, encoding, fs_path, destination, overwrite=overwrite)


def export_filesystem_entries(
    path: Path,
    layout_id: Optional[str],
    encoding: str,
    fs_paths: list[str],
    destination_parent: Path,
    overwrite: bool = False,
):
    from .application.filesystem_operations import export_filesystem_entries as operation

    return operation(path, layout_id, encoding, fs_paths, destination_parent, overwrite=overwrite)


def replace_file_with_copy(path: Path, layout_id: Optional[str], encoding: str, fs_path: str, replacement_path: Path, output_path: Path):
    from .application.filesystem_operations import replace_file_with_copy as operation

    return operation(path, layout_id, encoding, fs_path, replacement_path, output_path)


def delete_filesystem_entry_with_copy(path: Path, layout_id: Optional[str], encoding: str, fs_path: str, output_path: Path):
    from .application.filesystem_operations import delete_filesystem_entry_with_copy as operation

    return operation(path, layout_id, encoding, fs_path, output_path)


def import_file_with_copy(path: Path, layout_id: Optional[str], encoding: str, directory: str, host_file: Path, output_path: Path):
    from .application.filesystem_operations import import_file_with_copy as operation

    return operation(path, layout_id, encoding, directory, host_file, output_path)


def import_directory_with_copy(path: Path, layout_id: Optional[str], encoding: str, directory: str, host_directory: Path, output_path: Path):
    from .application.filesystem_operations import import_directory_with_copy as operation

    return operation(path, layout_id, encoding, directory, host_directory, output_path)


def create_directory_with_copy(path: Path, layout_id: Optional[str], encoding: str, parent: str, name: str, output_path: Path):
    from .application.filesystem_operations import create_directory_with_copy as operation

    return operation(path, layout_id, encoding, parent, name, output_path)


def replace_file_bytes_with_copy(path: Path, layout_id: Optional[str], encoding: str, fs_path: str, replacement: bytes, output_path: Path):
    from .application.filesystem_operations import replace_file_bytes_with_copy as operation

    return operation(path, layout_id, encoding, fs_path, replacement, output_path)


def replace_flat_sector_bytes_with_copy(path: Path, layout_id: str, track: int, head: int, sector_id: int, replacement: bytes, output_path: Path):
    from .application.filesystem_operations import replace_flat_sector_bytes_with_copy as operation

    return operation(path, layout_id, track, head, sector_id, replacement, output_path)


def blank_image_presets() -> list[BlankImagePreset]:
    from .application.image_creation_operations import blank_image_presets as operation

    return operation()


def create_blank_image(preset_id: str, output_path: Path, *, overwrite: bool = False) -> BlankImageResult:
    from .application.image_creation_operations import create_blank_image as operation

    return operation(preset_id, output_path, overwrite=overwrite)


def greaseweazle_status() -> GreaseweazleStatus:
    from .application.hardware_operations import greaseweazle_status as operation

    return operation()


def greaseweazle_formats() -> list[GreaseweazleFormat]:
    from .application.hardware_operations import greaseweazle_formats as operation

    return operation()


def read_disk_with_greaseweazle(
    output: Path,
    *,
    drive: str = "A",
    gw_format: str = "",
    tracks: str = "",
    revs: Optional[int] = None,
    overwrite: bool = False,
) -> HardwareReadResult:
    from .application.hardware_operations import read_disk_with_greaseweazle as operation

    return operation(output, drive=drive, gw_format=gw_format, tracks=tracks, revs=revs, overwrite=overwrite)


def synthesize_scp_with_greaseweazle(
    source: Path,
    output: Path,
    *,
    gw_format: str,
    tracks: str = "",
    overwrite: bool = False,
):
    """Generate a calibrated SCP through the shared hardware operation."""

    from .application.hardware_operations import synthesize_scp_with_greaseweazle as operation

    return operation(source, output, gw_format=gw_format, tracks=tracks, overwrite=overwrite)


def write_and_verify_with_greaseweazle(
    source: Path,
    readback: Path,
    manifest: Path,
    **kwargs,
):
    """Write and independently verify media through the shared operation."""

    from .application.hardware_operations import write_and_verify_with_greaseweazle as operation

    return operation(source, readback, manifest, **kwargs)


def provenance_json(path: Path) -> dict:
    """Load a provenance sidecar for display."""

    return json.loads(path.read_text(encoding="utf-8"))


def format_hex_dump(data: bytes, *, width: int = 16, max_bytes: Optional[int] = None) -> str:
    from .application.text_operations import format_hex_dump as operation

    return operation(data, width=width, max_bytes=max_bytes)


def parse_hex_dump_text(text: str, *, expected_size: Optional[int] = None) -> bytes:
    from .application.text_operations import parse_hex_dump_text as operation

    return operation(text, expected_size=expected_size)


def apply_ascii_hex_dump_edits(text: str, original_data: bytes, *, width: int = 16) -> bytes:
    from .application.text_operations import apply_ascii_hex_dump_edits as operation

    return operation(text, original_data, width=width)


def runtime_version() -> str:
    """Return the fluxctl version used by Studio."""

    return __version__


def load_layout_options() -> list[dict[str, object]]:
    from .application.layout_operations import load_layout_options as operation

    return operation()
