"""Blank image creation operations shared by Fluxctl frontends."""
from __future__ import annotations

from pathlib import Path
from .models import BlankImagePreset, BlankImageResult
from ..filesystems.cpm import cpm_disk_parameters_for_layout
from ..layouts.loader import ensure_layout_loaded
from ..output import atomic_write_bytes


FAT12_PRESETS = {
    "fat12_180k": {"label": "IBM DOS FAT12 180K (.img)", "layout_id": "ibm_mfm_180k", "total_sectors": 360, "media": 0xFC, "sectors_per_cluster": 1, "root_entries": 64, "sectors_per_fat": 2, "sectors_per_track": 9, "heads": 1},
    "fat12_360k": {"label": "IBM DOS FAT12 360K (.img)", "layout_id": "ibm_mfm_360k", "total_sectors": 720, "media": 0xFD, "sectors_per_cluster": 2, "root_entries": 112, "sectors_per_fat": 2, "sectors_per_track": 9, "heads": 2},
    "fat12_720k": {"label": "IBM DOS FAT12 720K (.img)", "layout_id": "ibm_mfm_720k", "total_sectors": 1440, "media": 0xF9, "sectors_per_cluster": 2, "root_entries": 112, "sectors_per_fat": 3, "sectors_per_track": 9, "heads": 2},
    "fat12_1200k": {"label": "IBM DOS FAT12 1.2M (.img)", "layout_id": "ibm_mfm_1200k", "total_sectors": 2400, "media": 0xF9, "sectors_per_cluster": 1, "root_entries": 224, "sectors_per_fat": 7, "sectors_per_track": 15, "heads": 2},
    "fat12_1440k": {"label": "IBM DOS FAT12 1.44M (.img)", "layout_id": "ibm_mfm_1440k", "total_sectors": 2880, "media": 0xF0, "sectors_per_cluster": 1, "root_entries": 224, "sectors_per_fat": 9, "sectors_per_track": 18, "heads": 2},
}

_PRESETS = tuple(
    BlankImagePreset(key, str(spec["label"]), ".img", str(spec["layout_id"]), "fat12", int(spec["total_sectors"]) * 512, "Formatted MS-DOS FAT12 image.")
    for key, spec in FAT12_PRESETS.items()
) + (
    BlankImagePreset("cbm_dos_1541_d64", "Commodore 1541 CBM DOS 170K (.d64)", ".d64", "commodore_gcr_1541_170k", "cbm_dos", 174848, "Formatted CBM DOS image."),
    BlankImagePreset("cbm_dos_1571_d71", "Commodore 1571 CBM DOS 341K (.d71)", ".d71", "commodore_gcr_1571_341k", "cbm_dos_1571", 349696, "Formatted two-sided CBM DOS image."),
    BlankImagePreset("cbm_dos_1581_d81", "Commodore 1581 CBM DOS 800K (.d81)", ".d81", "commodore_mfm_1581_800k", "cbm_dos_1581", 819200, "Formatted CBM DOS 1581 image."),
    BlankImagePreset("amiga_ofs_adf", "AmigaDOS OFS 880K (.adf)", ".adf", "amiga_mfm_880k", "amiga_ofs", 901120, "Formatted AmigaDOS OFS image."),
    BlankImagePreset("cpm_osborne_200k_img", "Osborne 1 CP/M SSDD 200K (.img)", ".img", "osborne_mfm_ssdd_200k", "cpm", 204800, "Formatted Osborne CP/M image."),
    BlankImagePreset("cpm_kaypro_200k_img", "Kaypro II CP/M SSDD 200K (.img)", ".img", "kaypro_mfm_ssdd_40_200k", "cpm", 204800, "Formatted Kaypro CP/M image."),
    BlankImagePreset("cpm_tandy_model4_180k_img", "Tandy Model 4 CP/M 2.2 SSDD 180K (.img)", ".img", "tandy_mfm_ssdd_180k", "cpm", 184320, "Formatted Tandy CP/M image."),
)


def blank_image_presets():
    return list(_PRESETS)


def create_blank_image(preset_id: str, output_path: Path, *, overwrite: bool = False):
    preset = next((item for item in _PRESETS if item.preset_id == preset_id), None)
    if preset is None:
        raise ValueError(f"Unsupported blank image preset: {preset_id}")
    output_path = output_path.with_suffix(preset.suffix) if output_path.suffix == "" else output_path
    if preset_id in FAT12_PRESETS:
        payload = _blank_fat12(FAT12_PRESETS[preset_id])
    elif preset_id == "cbm_dos_1541_d64":
        payload = _blank_cbm_dos(sides=1)
    elif preset_id == "cbm_dos_1571_d71":
        payload = _blank_cbm_dos(sides=2)
    elif preset_id == "cbm_dos_1581_d81":
        payload = _blank_1581()
    elif preset_id == "amiga_ofs_adf":
        payload = _blank_amiga()
    else:
        payload = _blank_cpm(preset.layout_id)
    atomic_write_bytes(output_path, payload, overwrite=overwrite)
    return BlankImageResult(str(output_path), preset.preset_id, preset.label, preset.layout_id, preset.filesystem, len(payload))


def _blank_fat12(spec: dict) -> bytes:
    total_sectors = int(spec["total_sectors"])
    image = bytearray(total_sectors * 512)
    boot = bytearray(512)
    boot[0:3] = b"\xEB\x3C\x90"; boot[3:11] = b"FLUXCTL "
    boot[11:13] = (512).to_bytes(2, "little"); boot[13] = int(spec["sectors_per_cluster"])
    boot[14:16] = (1).to_bytes(2, "little"); boot[16] = 2
    boot[17:19] = int(spec["root_entries"]).to_bytes(2, "little")
    boot[19:21] = total_sectors.to_bytes(2, "little"); boot[21] = int(spec["media"])
    boot[22:24] = int(spec["sectors_per_fat"]).to_bytes(2, "little")
    boot[24:26] = int(spec["sectors_per_track"]).to_bytes(2, "little")
    boot[26:28] = int(spec["heads"]).to_bytes(2, "little"); boot[38] = 0x29
    boot[39:43] = b"FCTL"; boot[43:54] = b"NO NAME    "; boot[54:62] = b"FAT12   "; boot[510:512] = b"\x55\xAA"
    image[:512] = boot
    for index in range(2):
        offset = (1 + index * int(spec["sectors_per_fat"])) * 512
        image[offset : offset + 3] = bytes([int(spec["media"]), 0xFF, 0xFF])
    return bytes(image)


def _blank_cpm(layout_id: str) -> bytes:
    layout = ensure_layout_loaded(layout_id)
    params = cpm_disk_parameters_for_layout(layout_id)
    if params is None:
        raise ValueError(f"No modelled CP/M DPB for {layout_id}")
    image = bytearray(b"\xE5" * (layout.tracks * layout.sides * layout.sectors_per_track * layout.sector_size))
    marker = {"kaypro_mfm_ssdd_40_200k": b"FLUXCTL CPM KAYPROII", "osborne_mfm_ssdd_200k": b"FLUXCTL CPM OSBORNE", "tandy_mfm_ssdd_180k": b"FLUXCTL CPM TANDY4"}.get(layout_id, b"FLUXCTL CPM")
    image[:len(marker)] = marker
    start = params.reserved_tracks * params.sectors_per_track * params.sector_size
    image[start : start + params.directory_blocks * params.block_size] = b"\xE5" * (params.directory_blocks * params.block_size)
    return bytes(image)


def _blank_cbm_dos(*, sides: int) -> bytes:
    from ..exporters.d64 import DEFAULT_SECTORS_PER_TRACK, SECTOR_SIZE
    counts = list(DEFAULT_SECTORS_PER_TRACK) * sides
    image = bytearray(sum(counts) * SECTOR_SIZE)
    def offset(track: int, sector: int) -> int:
        return (sum(counts[: track - 1]) + sector) * SECTOR_SIZE
    bam = bytearray(SECTOR_SIZE)
    bam[0:3] = bytes([18, 1, 0x41])
    bam[3] = 0x80 if sides == 2 else 0
    bam[0x90:0xA0] = b"FLUXCTL BLANK".ljust(16, b"\xA0")
    bam[0xA2:0xA4] = b"2A"
    for track, count in enumerate(DEFAULT_SECTORS_PER_TRACK, 1):
        at = 4 + (track - 1) * 4
        bam[at] = count
        for sector in range(count):
            bam[at + 1 + sector // 8] |= 1 << (sector % 8)
    for track, sector in ((18, 0), (18, 1)):
        at = 4 + (track - 1) * 4
        bit = at + 1 + sector // 8
        bam[bit] &= ~(1 << (sector % 8)); bam[at] -= 1
    directory = bytearray(SECTOR_SIZE); directory[0:2] = b"\x00\xFF"
    image[offset(18, 0):offset(18, 0) + SECTOR_SIZE] = bam
    image[offset(18, 1):offset(18, 1) + SECTOR_SIZE] = directory
    if sides == 2:
        side_bam = bytearray(SECTOR_SIZE)
        for index, count in enumerate(DEFAULT_SECTORS_PER_TRACK):
            logical_track = index + 36
            if logical_track == 53:
                count = 0
            bam[221 + index] = count
            side_bam[index * 3:index * 3 + 3] = bytes([count, 0xFF, 0xFF]) if count else b"\0\0\0"
        image[offset(18, 0):offset(18, 0) + SECTOR_SIZE] = bam
        image[offset(53, 0):offset(53, 0) + SECTOR_SIZE] = side_bam
    return bytes(image)


def _blank_1581() -> bytes:
    image = bytearray(819200)
    def offset(track: int, sector: int) -> int:
        return ((track - 1) * 40 + sector) * 256
    header = bytearray(256); header[0:3] = bytes([40, 3, ord("D")])
    header[4:20] = b"FLUXCTL BLANK".ljust(16, b"\xA0"); header[22:24] = b"FC"; header[25:27] = b"3D"
    image[offset(40, 0):offset(40, 0) + 256] = header
    bam = bytearray(256); bam[0:8] = bytes([40, 2, ord("D"), 0xBB, ord("F"), ord("C"), 0xC0, 0])
    bam2 = bytearray(256); bam2[0:8] = bytes([0, 0xFF, ord("D"), 0xBB, ord("F"), ord("C"), 0xC0, 0])
    for index in range(40):
        at = 16 + index * 6; bam[at] = 40
        for sector in range(40): bam[at + 1 + sector // 8] |= 1 << (sector % 8)
        at = 16 + index * 6; bam2[at] = 40
        for sector in range(40): bam2[at + 1 + sector // 8] |= 1 << (sector % 8)
    for sector in range(4):
        at = 16 + 39 * 6; bam[at + 1 + sector // 8] &= ~(1 << (sector % 8)); bam[at] -= 1
    image[offset(40, 1):offset(40, 1) + 256] = bam
    image[offset(40, 2):offset(40, 2) + 256] = bam2
    directory = bytearray(256); directory[0:2] = b"\x00\xFF"
    image[offset(40, 3):offset(40, 3) + 256] = directory
    return bytes(image)


def _blank_amiga() -> bytes:
    image = bytearray(901120); image[0:4] = b"DOS\0"
    root = bytearray(512); root[0:4] = (2).to_bytes(4, "big"); root[508:512] = (-2).to_bytes(4, "big", signed=True)
    image[880 * 512:881 * 512] = root
    return bytes(image)
