from pathlib import Path

from fluxctl.cli import _probe_flat_image
from fluxctl.decoding.wang import wang_crc16
from fluxctl.layouts.loader import load_builtin_layouts
from fluxctl.cli import _prepare_image
from fluxctl.filesystem_detection import detect_filesystem
from fluxctl import studio_services as services


FIXTURE_IMG = Path(
    "tests/fixtures/8inch/Wang/Wang-OIS100-HS32-FM-PeripheralsII-315K.img"
)


def test_wang_crc_includes_sync_bits() -> None:
    assert wang_crc16(b"\x00" * 256) == 0x0A88
    assert wang_crc16(b"Wang OIS") == 0x6E71


def test_wang_flat_image_uses_logical_geometry() -> None:
    load_builtin_layouts()
    candidates = _probe_flat_image(FIXTURE_IMG)
    assert candidates
    candidate = candidates[0]
    assert candidate.layout_id == "wang_ois_hs32_fm_315k"
    assert candidate.encoding == "wang_fm"
    assert "wang_geometry=77x16x256" in candidate.evidence
    assert "wang_label=SP042060" in candidate.evidence


def test_wang_ois_catalog_lists_and_extracts_package_files(tmp_path: Path) -> None:
    image = _prepare_image(FIXTURE_IMG, "wang_ois_hs32_fm_315k", "wang_fm")
    detection = detect_filesystem(image)
    assert detection.primary == "wang_ois"
    assert detection.plugin is not None
    assert [entry.name for entry in detection.plugin.list_directory("/")] == ["INSTALL", "PRINT"]
    assert "T300" in [entry.name for entry in detection.plugin.list_directory("/PRINT")]
    assert [entry.name for entry in detection.plugin.list_directory("/PRINT/T407")] == ["IPL", "OBJ"]

    install = detection.plugin.extract_file("/INSTALL")
    assert len(install) == 1074
    assert b"installation package for the WANG word processing printer code" in install
    assert detection.plugin.extract_file("/PRINT/T300/OBJ").startswith(b"\xc3\x4a\x34")
    assert len(detection.plugin.extract_file("/PRINT/T300/OBJ")) == 1008
    assert detection.plugin.file_sector_addresses("/INSTALL") == {
        (38, 0, sector) for sector in range(6)
    }

    hex_view = services.file_hex_dump(
        FIXTURE_IMG,
        "wang_ois_hs32_fm_315k",
        "wang_fm",
        "/PRINT/T300/OBJ",
    )
    assert hex_view.size == 1008
    exported = services.export_filesystem_entry(
        FIXTURE_IMG,
        "wang_ois_hs32_fm_315k",
        "wang_fm",
        "/INSTALL",
        tmp_path / "INSTALL",
    )
    assert exported.bytes == 1074
    assert (tmp_path / "INSTALL").read_bytes() == install

    exported_directory = services.export_filesystem_entry(
        FIXTURE_IMG,
        "wang_ois_hs32_fm_315k",
        "wang_fm",
        "/PRINT/T407",
        tmp_path,
    )
    assert exported_directory.files == 2
    assert (tmp_path / "T407" / "IPL").is_file()
    assert (tmp_path / "T407" / "OBJ").is_file()


def test_wang_scp_reconstructs_all_logical_sectors() -> None:
    scp = FIXTURE_IMG.with_suffix(".scp")
    image = _prepare_image(scp, "wang_ois_hs32_fm_315k", "wang_fm")
    sectors = list(image.iter_sectors())
    assert len(sectors) == 77 * 16
    assert sectors[0].startswith(b"SP042060")
    assert all(len(sector) == 256 for sector in sectors)
    assert b"".join(sectors) == FIXTURE_IMG.read_bytes()
    detection = detect_filesystem(image)
    assert detection.primary == "wang_ois"
    assert detection.plugin is not None
    assert b"installation package for the WANG" in detection.plugin.extract_file("/INSTALL")


def test_wang_flat_probe_uses_catalog_structure_not_package_id(tmp_path: Path) -> None:
    data = bytearray(77 * 16 * 256)
    data[:8] = b"OTHER001"
    data[22:24] = (616).to_bytes(2, "little")
    catalog = 616 * 256
    data[catalog + 1 : catalog + 8] = b"Catalog"
    data[catalog + 31] = 0
    data[catalog + 34] = 48

    root = catalog + 48
    data[root : root + 4] = b"ROOT"
    data[root + 26] = 0
    data[root + 27] = 1
    data[root + 29] = 0
    child = catalog + 256 + 0
    data[child : child + 4] = b"FILE"
    data[child + 26] = 1
    data[child + 27] = 20
    data[child + 29] = 1
    data[child + 37] = 10
    data[20 * 1024 : 20 * 1024 + 10] = b"Wang bytes"

    image_path = tmp_path / "unrelated-name.img"
    image_path.write_bytes(data)
    candidates = _probe_flat_image(image_path)

    assert candidates
    assert candidates[0].layout_id == "wang_ois_hs32_fm_315k"
    assert candidates[0].filesystem == "wang_ois"


def test_wang_catalog_uses_header_root_pointer_and_2k_unit(tmp_path: Path) -> None:
    data = bytearray(77 * 16 * 256)
    data[:8] = b"ARBITRAR"
    data[22:24] = (77).to_bytes(2, "little")
    catalog = 77 * 2048
    data[catalog + 1 : catalog + 8] = b"Catalog"
    data[catalog + 31] = 0
    data[catalog + 34] = 96

    root = catalog + 96
    data[root : root + 4] = b"ROOT"
    data[root + 26] = 0
    data[root + 27] = 1
    data[root + 29] = 0
    child = catalog + 256
    data[child : child + 4] = b"FILE"
    data[child + 26] = 1
    data[child + 27] = 20
    data[child + 29] = 1
    data[child + 37] = 10
    data[20 * 1024 : 20 * 1024 + 10] = b"Wang bytes"

    image_path = tmp_path / "later-package.img"
    image_path.write_bytes(data)
    image = _prepare_image(image_path, "wang_ois_hs32_fm_315k", "wang_fm")
    detection = detect_filesystem(image)

    assert detection.primary == "wang_ois"
    assert detection.plugin is not None
    assert [entry.name for entry in detection.plugin.list_directory("/")] == ["ROOT"]
    assert detection.plugin.extract_file("/ROOT/FILE") == b"Wang bytes"
    assert detection.plugin.metadata()["catalog_pointer_unit"] == "double_allocation_block"


def test_wang_layout_detection_wins_over_generic_filesystem_false_positives() -> None:
    image = _prepare_image(FIXTURE_IMG, "wang_ois_hs32_fm_315k", "wang_fm")
    detection = detect_filesystem(image)

    assert detection.primary == "wang_ois"
    assert detection.confidence == 0.99
    assert "wang_ois_catalog_probe=1" in detection.evidence
