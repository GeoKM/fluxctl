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
