from __future__ import annotations

import hashlib
from pathlib import Path

from fluxctl import studio_services as services
from fluxctl.apple2 import (
    APPLE2_DO_ORDER,
    APPLE2_PO_ORDER,
    Apple2SectorImage,
    apple2_sector_image_bytes,
    load_apple2_tracks,
    parse_woz,
)
from fluxctl.cli import (
    _prepare_convert_payload,
    _prepare_image,
    load_builtin_decoders,
    load_builtin_layouts,
)
from fluxctl.detection import detect_layout_any
from fluxctl.filesystem_detection import detect_filesystem
from fluxctl.filesystems.apple_dos import AppleDOS33Filesystem
from fluxctl.fixtures import FixtureDescriptor
from fluxctl.layouts.loader import ensure_layout_loaded
from fluxctl.scp import parse_scp


FIXTURE_ROOT = Path("tests/fixtures/5.25inch/Apple")
FIXTURE_STEM = "Apple-AppleII-SSDD-Apple6A2-ProDOSvb1a-140K"
FIXTURE_WOZ = FIXTURE_ROOT / f"{FIXTURE_STEM}.woz"
FIXTURE_SCP = FIXTURE_ROOT / f"{FIXTURE_STEM}.scp"
FIXTURE_PO = FIXTURE_ROOT / f"{FIXTURE_STEM}.po"
FIXTURE_DOS33_PROTECTED = (
    FIXTURE_ROOT / "Apple-AppleII-SSDD-Apple6A2-AppleDOS-boulderdashii-140K.woz"
)
EXPECTED_PO_SHA256 = "c35c8e558b557558721601d6ca38ed1123d99dd046b49b5e78167a26aeb6a0de"


def test_apple_fixture_name_exposes_geometry_metadata() -> None:
    descriptor = FixtureDescriptor.from_path(FIXTURE_WOZ)
    assert descriptor.manufacturer == "Apple"
    assert descriptor.drive_style == "AppleII"
    assert descriptor.encoding == "Apple6A2"
    assert descriptor.os_name == "ProDOSvb1a"
    assert descriptor.approx_capacity == "140K"


def test_woz2_decodes_all_apple_ii_tracks_and_matches_canonical_po() -> None:
    image = parse_woz(FIXTURE_WOZ)

    assert image.version == 2
    assert image.metadata["title"] == "ProDOS"
    assert len(image.tracks) == 35
    assert all(len(track.sectors) == 16 for track in image.tracks)
    assert apple2_sector_image_bytes(image.tracks, APPLE2_PO_ORDER) == FIXTURE_PO.read_bytes()


def test_apple_ii_scp_auto_detection_and_decode_are_complete() -> None:
    load_builtin_decoders()
    load_builtin_layouts()
    candidate = detect_layout_any(parse_scp(FIXTURE_SCP))

    assert candidate is not None
    assert candidate.layout.layout_id == "apple2_gcr_nofs_140_140k"

    image = _prepare_image(FIXTURE_SCP, candidate.layout.layout_id, candidate.layout.encoding)
    assert isinstance(image, Apple2SectorImage)
    assert sum(len(track.sectors) for track in image.tracks) == 560
    assert sum(track.weak + track.missing for track in image.tracks) == 0
    payload = apple2_sector_image_bytes(image.tracks, APPLE2_PO_ORDER)
    assert hashlib.sha256(payload).hexdigest() == EXPECTED_PO_SHA256


def test_prodos_lists_extracts_and_maps_files() -> None:
    tracks, _metadata = load_apple2_tracks(FIXTURE_PO)
    image = Apple2SectorImage(tracks)
    load_builtin_layouts()
    image.layout = ensure_layout_loaded("apple2_gcr_nofs_140_140k")
    detection = detect_filesystem(image)

    assert detection.primary == "prodos"
    assert detection.plugin is not None
    entries = detection.plugin.list_directory("/")
    assert [(entry.name, entry.size) for entry in entries] == [
        ("EXERCISE.INTERP", 4096),
        ("PRO.KERNEL", 14336),
        ("S1", 0),
    ]
    assert len(detection.plugin.extract_file("/PRO.KERNEL")) == 14336
    assert detection.plugin.file_sector_addresses("/PRO.KERNEL")


def test_apple_ii_po_and_do_conversion_roundtrip_preserves_logical_blocks() -> None:
    do_payload = _prepare_convert_payload(FIXTURE_PO, "do", None, "mfm").payload
    tracks = load_apple2_tracks_from_bytes(do_payload, APPLE2_DO_ORDER)
    assert apple2_sector_image_bytes(tracks, APPLE2_PO_ORDER) == FIXTURE_PO.read_bytes()


def load_apple2_tracks_from_bytes(payload: bytes, order: tuple[int, ...]):
    from fluxctl.apple2 import tracks_from_apple2_sector_image

    return tracks_from_apple2_sector_image(payload, order)


def test_studio_exposes_prodos_files_map_and_file_overlay() -> None:
    summary = services.summarize_image(FIXTURE_PO)
    assert summary.layout_id == "apple2_gcr_nofs_140_140k"
    assert summary.encoding == "apple2_gcr"
    assert summary.filesystem == "prodos"

    entries = services.list_files(FIXTURE_PO, summary.layout_id, summary.encoding)
    assert {entry.name for entry in entries} == {"EXERCISE.INTERP", "PRO.KERNEL", "S1"}
    disk_map = services.build_disk_map_for_image(FIXTURE_PO, summary.layout_id, summary.encoding)
    assert disk_map.total_tracks == 35
    assert sum(len(track) for track in disk_map.tracks) == 560
    allocation = services.file_allocation_for_image(
        FIXTURE_PO, summary.layout_id, summary.encoding, "/PRO.KERNEL"
    )
    assert allocation.sectors


def test_apple_dos_33_catalog_extract_and_overlay(tmp_path: Path) -> None:
    payload = bytearray(35 * 16 * 256)

    def physical_sector(track: int, sector: int) -> memoryview:
        position = APPLE2_PO_ORDER.index(sector)
        offset = (track * 16 + position) * 256
        return memoryview(payload)[offset : offset + 256]

    vtoc = physical_sector(17, 0)
    vtoc[1:3] = bytes((17, 15))
    vtoc[6] = 254
    vtoc[0x34:0x38] = bytes((35, 16, 0, 1))
    catalog = physical_sector(17, APPLE2_DO_ORDER[15])
    entry_offset = 0x0B
    catalog[entry_offset : entry_offset + 3] = bytes((17, 14, 0x04))
    catalog[entry_offset + 3 : entry_offset + 33] = bytes(
        byte | 0x80 for byte in b"HELLO".ljust(30)
    )
    catalog[entry_offset + 33 : entry_offset + 35] = (2).to_bytes(2, "little")
    ts_list = physical_sector(17, APPLE2_DO_ORDER[14])
    ts_list[0x0C:0x0E] = bytes((18, 0))
    physical_sector(18, 0)[:11] = b"HELLO APPLE"

    tracks = load_apple2_tracks_from_bytes(bytes(payload), APPLE2_PO_ORDER)
    image = Apple2SectorImage(tracks)
    filesystem = AppleDOS33Filesystem()
    assert filesystem.probe(image)
    assert [(entry.name, entry.size) for entry in filesystem.list_directory()] == [("HELLO", 256)]
    assert filesystem.extract_file("/HELLO").startswith(b"HELLO APPLE")
    assert filesystem.file_sector_addresses("/HELLO") == {(17, 0, 2), (18, 0, 0)}

    dsk_path = tmp_path / "dos33.dsk"
    dsk_path.write_bytes(payload)
    dsk_tracks, metadata = load_apple2_tracks(dsk_path)
    assert metadata["format"] == "po"
    assert AppleDOS33Filesystem().probe(Apple2SectorImage(dsk_tracks))


def test_apple_dsk_container_is_content_detected_as_prodos(tmp_path: Path) -> None:
    dsk_path = tmp_path / "prodos.dsk"
    dsk_path.write_bytes(FIXTURE_PO.read_bytes())

    summary = services.summarize_image(dsk_path)
    assert summary.layout_id == "apple2_gcr_nofs_140_140k"
    assert summary.filesystem == "prodos"


def test_copy_protected_apple_dos_disk_reports_empty_catalog_without_losing_geometry() -> None:
    image = parse_woz(FIXTURE_DOS33_PROTECTED)
    assert len(image.tracks) == 35
    assert sum(len(track.sectors) for track in image.tracks) == 544
    assert next(track for track in image.tracks if track.track == 6).missing == 16

    filesystem = AppleDOS33Filesystem()
    assert filesystem.probe(Apple2SectorImage(image.tracks))
    assert filesystem.list_directory("/") == []
    assert filesystem.metadata() == {
        "filesystem": "apple_dos_3_3",
        "volume_number": 254,
        "catalog_track": 17,
        "catalog_sector": 15,
        "catalog_entries": 0,
        "sector_size": 256,
    }

    summary = services.summarize_image(FIXTURE_DOS33_PROTECTED)
    assert summary.filesystem == "apple_dos_3_3"
    listing = services.list_files_with_info(
        FIXTURE_DOS33_PROTECTED, summary.layout_id, summary.encoding
    )
    assert listing.entries == []
    assert listing.volume_text == "Apple DOS 3.3  Volume: 254  empty catalog"
