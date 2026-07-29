from pathlib import Path

from fluxctl import studio_services as services
from fluxctl.filesystems import RawSectorImage
from fluxctl.filesystems.fat12 import FAT12


FIXTURE_IMG = Path("tests/fixtures/3.5inch/IBM/IBM-Generic-DSDD-MFM-IBMPC-720K.img")
FIXTURE_D64 = Path("tests/fixtures/5.25inch/Commodore/Commodore-1541-SSDD-GCR-C64-170K.d64")
FIXTURE_D71 = Path("tests/fixtures/5.25inch/Commodore/Commodore-1571-DSDD-GCR-C128-341K.d71")
FIXTURE_CPM_SCP = Path("tests/fixtures/5.25inch/Commodore/Commodore-1541-SSDD-GCR-C64CPM-170K.scp")
FIXTURE_CPM_D64 = Path("tests/fixtures/5.25inch/Commodore/Commodore-1541-SSDD-GCR-C64CPM-170K.d64")
FIXTURE_1581_D81 = Path("tests/fixtures/3.5inch/Commodore/Commodore-1581-DSDD-MFM-C64-800K.d81")
FIXTURE_ADF = Path("tests/fixtures/3.5inch/Commodore/Commodore-1010-DSDD-MFM-Amiga-880K.adf")


def test_studio_doctor_report_matches_cli_shape() -> None:
    report = services.doctor_report()

    assert report["tool"] == "fluxctl"
    assert "checks" in report
    assert any(check["name"] == "layouts" for check in report["checks"])


def test_studio_loads_layout_options() -> None:
    layouts = services.load_layout_options()

    assert any(layout["layout_id"] == "ibm_mfm_720k" for layout in layouts)


def test_studio_summarizes_flat_image() -> None:
    summary = services.summarize_image(FIXTURE_IMG)

    assert summary.path.endswith("IBM-Generic-DSDD-MFM-IBMPC-720K.img")
    assert summary.size > 0
    assert summary.layout_id == "ibm_mfm_720k"
    assert summary.encoding == "mfm"


def test_studio_builds_map_and_qc_for_flat_image() -> None:
    disk_map = services.build_disk_map_for_image(FIXTURE_IMG, "ibm_mfm_720k", "mfm")
    qc = services.build_qc_for_image(FIXTURE_IMG, "ibm_mfm_720k", "mfm")

    assert disk_map.total_tracks > 0
    assert disk_map.max_sectors_per_track > 0
    assert qc.total_sectors > 0


def test_studio_map_preserves_commodore_gcr_zones() -> None:
    disk_map = services.build_disk_map_for_image(FIXTURE_D64, "commodore_gcr_1541_170k", "gcr")

    row_lengths = [len(row) for row in disk_map.tracks]
    assert row_lengths[:17] == [21] * 17
    assert row_lengths[17:24] == [19] * 7
    assert row_lengths[24:30] == [18] * 6
    assert row_lengths[30:] == [17] * 10


def test_studio_builds_cbm_bam_block_map() -> None:
    disk_map = services.build_disk_map_for_image(FIXTURE_D64, "commodore_gcr_1541_170k", "gcr", map_view="bam")
    states = {state for row in disk_map.tracks for state in row}

    assert disk_map.render_style == "grid"
    assert disk_map.total_tracks == 40
    assert "bam_file" in states
    assert "bam_system" in states
    assert "bam_free" in states


def test_studio_builds_two_head_1571_bam_block_map() -> None:
    disk_map = services.build_disk_map_for_image(FIXTURE_D71, "commodore_gcr_1571_341k", "gcr", map_view="bam")
    heads = {head for _track, head in disk_map.track_ids}

    assert disk_map.render_style == "grid"
    assert heads == {0, 1}
    assert disk_map.total_tracks == 70


def test_studio_can_switch_c64_cpm_between_logical_and_physical_maps() -> None:
    logical = services.build_disk_map_for_image(
        FIXTURE_CPM_D64, "commodore_gcr_1541_170k", "gcr", map_view="logical"
    )
    physical = services.build_disk_map_for_image(
        FIXTURE_CPM_D64, "commodore_gcr_1541_170k", "gcr", map_view="physical"
    )

    logical_counts = {state: sum(row.count(state) for row in logical.tracks) for state in {"good", "unused"}}
    physical_counts = {state: sum(row.count(state) for row in physical.tracks) for state in {"good", "unused"}}
    assert logical_counts["unused"] > 0
    assert physical_counts["unused"] == 0
    assert physical_counts["good"] == 683


def test_studio_scp_cpm_physical_map_recovers_all_sectors_as_weak() -> None:
    logical = services.build_disk_map_for_image(
        FIXTURE_CPM_SCP, "commodore_gcr_1541_170k", "gcr", map_view="logical"
    )
    physical = services.build_disk_map_for_image(
        FIXTURE_CPM_SCP, "commodore_gcr_1541_170k", "gcr", map_view="physical"
    )

    logical_counts = {state: sum(row.count(state) for row in logical.tracks) for state in {"bad", "unused", "weak"}}
    physical_counts = {state: sum(row.count(state) for row in physical.tracks) for state in {"bad", "unused", "weak"}}
    assert logical_counts == {"bad": 0, "unused": 431, "weak": 252}
    assert physical_counts == {"bad": 0, "unused": 0, "weak": 683}


def test_studio_qc_treats_standard_d64_as_complete_flat_image() -> None:
    qc = services.build_qc_for_image(FIXTURE_CPM_D64, "commodore_gcr_1541_170k", "gcr")

    assert qc.status == "good"
    assert qc.missing_tracks == 0
    assert qc.suspect_sectors == 0


def test_studio_qc_recovers_verified_sectors_from_cpm_scp() -> None:
    qc = services.build_qc_for_image(FIXTURE_CPM_SCP, "commodore_gcr_1541_170k", "gcr")

    assert qc.total_good_sectors > 300
    assert qc.total_bad_sectors < 10
    assert qc.total_missing_sectors == 0


def test_studio_lists_1581_cbm_dos_files() -> None:
    summary = services.summarize_image(FIXTURE_1581_D81)
    entries = services.list_files(FIXTURE_1581_D81, summary.layout_id, summary.encoding)

    assert summary.filesystem == "cbm_dos_1581"
    assert any(entry.name == "HOW TO USE" for entry in entries)
    assert any(entry.name == "PIC.DIR" and entry.kind == "<DIR>" for entry in entries)


def test_studio_lists_1581_cbm_dos_subdirectory() -> None:
    summary = services.summarize_image(FIXTURE_1581_D81)
    entries = services.list_files(FIXTURE_1581_D81, summary.layout_id, summary.encoding, "/PIC.DIR")

    assert any(entry.name == "SUE.C" and entry.path == "/PIC.DIR/SUE.C" for entry in entries)
    assert any(entry.name == "PAGODA.C" for entry in entries)


def test_studio_builds_1581_file_hex_dump() -> None:
    summary = services.summarize_image(FIXTURE_1581_D81)
    dump = services.file_hex_dump(FIXTURE_1581_D81, summary.layout_id, summary.encoding, "/PIC.DIR/SUE.C")

    assert dump.title == "File /PIC.DIR/SUE.C"
    assert dump.size > 5000
    assert "00 20 EA" in dump.text


def test_studio_lists_amiga_dos_root_entries() -> None:
    summary = services.summarize_image(FIXTURE_ADF)
    entries = services.list_files(FIXTURE_ADF, summary.layout_id, summary.encoding)

    assert summary.filesystem == "amiga_ffs"
    assert any(entry.name == "Devs" and entry.kind == "<DIR>" for entry in entries)
    assert any(entry.name == "Install" and entry.kind == "<DIR>" for entry in entries)


def test_studio_lists_amiga_dos_subdirectory() -> None:
    summary = services.summarize_image(FIXTURE_ADF)
    entries = services.list_files(FIXTURE_ADF, summary.layout_id, summary.encoding, "/C")

    assert any(entry.name == "Assign" and entry.path == "/C/Assign" for entry in entries)
    assert any(entry.name == "Execute" for entry in entries)


def test_studio_formats_hex_dump_with_ascii_column() -> None:
    text = services.format_hex_dump(b"ABC\x00\xff", width=4)

    assert text.splitlines() == [
        "00000000  41 42 43 00  |ABC.|",
        "00000004  FF           |.|",
    ]


def test_studio_builds_sector_hex_dump() -> None:
    summary = services.summarize_image(FIXTURE_IMG)
    dump = services.sector_hex_dump(FIXTURE_IMG, summary.layout_id, summary.encoding, 0, 0, 1)

    assert dump.title == "Sector T0 H0 S1"
    assert dump.size == 512
    assert "MSDOS4.0" in dump.text


def test_studio_lists_track_sectors_for_flat_image() -> None:
    summary = services.summarize_image(FIXTURE_IMG)
    report = services.sector_list(FIXTURE_IMG, summary.layout_id, summary.encoding, 0, 0)

    assert report.title == "Sectors T0 H0"
    assert "Track 0 head 0: 9 sectors" in report.text
    assert "ID 01 size=512 crc=ok" in report.text


def test_studio_builds_file_hex_dump() -> None:
    summary = services.summarize_image(FIXTURE_IMG)
    dump = services.file_hex_dump(FIXTURE_IMG, summary.layout_id, summary.encoding, "/AUTOEXEC.BAT")

    assert dump.title == "File /AUTOEXEC.BAT"
    assert dump.size == 39
    assert "@ECHO OFF" in dump.text


def test_studio_exports_selected_file(tmp_path: Path) -> None:
    summary = services.summarize_image(FIXTURE_IMG)
    destination = tmp_path / "AUTOEXEC.BAT"

    result = services.export_filesystem_entry(
        FIXTURE_IMG,
        summary.layout_id,
        summary.encoding,
        "/AUTOEXEC.BAT",
        destination,
    )

    assert result.files == 1
    assert result.bytes == 39
    assert destination.read_bytes() == b"@ECHO OFF\r\nCLS\r\nKEYB US\r\nSELECT MENU\r\n\x1a"


def test_studio_exports_selected_directory(tmp_path: Path) -> None:
    summary = services.summarize_image(FIXTURE_ADF)

    result = services.export_filesystem_entry(
        FIXTURE_ADF,
        summary.layout_id,
        summary.encoding,
        "/Expansion",
        tmp_path,
    )

    assert result.files == 2
    assert result.bytes > 0
    assert (tmp_path / "Expansion" / "HDDisk").exists()
    assert (tmp_path / "Expansion" / "HDDisk.info").exists()


def test_studio_exports_1581_selected_file(tmp_path: Path) -> None:
    summary = services.summarize_image(FIXTURE_1581_D81)
    destination = tmp_path / "HOW TO USE"

    result = services.export_filesystem_entry(
        FIXTURE_1581_D81,
        summary.layout_id,
        summary.encoding,
        "/HOW TO USE",
        destination,
    )

    assert result.files == 1
    assert result.bytes == destination.stat().st_size
    assert result.bytes > 12000


def test_studio_exports_1581_selected_directory(tmp_path: Path) -> None:
    summary = services.summarize_image(FIXTURE_1581_D81)

    result = services.export_filesystem_entry(
        FIXTURE_1581_D81,
        summary.layout_id,
        summary.encoding,
        "/PIC.DIR",
        tmp_path,
    )

    assert result.files == 15
    assert result.bytes > 70000
    assert (tmp_path / "PIC.DIR" / "SUE.C").exists()
    assert (tmp_path / "PIC.DIR" / "PAGODA.C").exists()


def test_studio_exports_multiple_selected_files(tmp_path: Path) -> None:
    summary = services.summarize_image(FIXTURE_IMG)

    result = services.export_filesystem_entries(
        FIXTURE_IMG,
        summary.layout_id,
        summary.encoding,
        ["/AUTOEXEC.BAT", "/CONFIG.SYS"],
        tmp_path,
    )

    assert result.files == 2
    assert result.bytes > 39
    assert (tmp_path / "AUTOEXEC.BAT").read_bytes().startswith(b"@ECHO OFF")
    assert (tmp_path / "CONFIG.SYS").exists()


def test_studio_replaces_fat12_file_in_new_copy(tmp_path: Path) -> None:
    summary = services.summarize_image(FIXTURE_IMG)
    replacement = tmp_path / "AUTOEXEC.BAT"
    replacement.write_bytes(b"@ECHO OFF\r\nCLS\r\nREM FLUXCTL COPY TEST\r\n")
    output = tmp_path / "patched.img"
    original = FIXTURE_IMG.read_bytes()

    result = services.replace_file_with_copy(
        FIXTURE_IMG,
        summary.layout_id,
        summary.encoding,
        "/AUTOEXEC.BAT",
        replacement,
        output,
    )

    assert result.filesystem == "fat12"
    assert result.path == str(output)
    assert result.bytes == 39
    assert FIXTURE_IMG.read_bytes() == original
    filesystem = FAT12()
    assert filesystem.probe(RawSectorImage(output.read_bytes()))
    assert filesystem.extract_file("/AUTOEXEC.BAT") == replacement.read_bytes()


def test_studio_replaces_fat12_file_with_shorter_copy(tmp_path: Path) -> None:
    summary = services.summarize_image(FIXTURE_IMG)
    replacement = tmp_path / "AUTOEXEC.BAT"
    replacement.write_bytes(b"@ECHO OFF\r\nREM SHORTER\r\n")
    output = tmp_path / "patched.img"

    result = services.replace_file_with_copy(
        FIXTURE_IMG,
        summary.layout_id,
        summary.encoding,
        "/AUTOEXEC.BAT",
        replacement,
        output,
    )

    filesystem = FAT12()
    assert result.bytes == 24
    assert filesystem.probe(RawSectorImage(output.read_bytes()))
    assert filesystem.extract_file("/AUTOEXEC.BAT") == replacement.read_bytes()


def test_studio_replaces_fat12_file_with_longer_existing_allocation_copy(tmp_path: Path) -> None:
    summary = services.summarize_image(FIXTURE_IMG)
    replacement = tmp_path / "AUTOEXEC.BAT"
    replacement.write_bytes(b"REM FLUXCTL LONGER REPLACEMENT\r\n" * 8)
    output = tmp_path / "patched.img"

    result = services.replace_file_with_copy(
        FIXTURE_IMG,
        summary.layout_id,
        summary.encoding,
        "/AUTOEXEC.BAT",
        replacement,
        output,
    )

    filesystem = FAT12()
    assert result.bytes == 256
    assert filesystem.probe(RawSectorImage(output.read_bytes()))
    assert filesystem.extract_file("/AUTOEXEC.BAT") == replacement.read_bytes()


def test_studio_replaces_fat12_file_by_allocating_more_clusters(tmp_path: Path) -> None:
    summary = services.summarize_image(FIXTURE_IMG)
    replacement = tmp_path / "AUTOEXEC.BAT"
    replacement.write_bytes(b"REM GROWN FILE\r\n" * 100)
    output = tmp_path / "patched.img"

    result = services.replace_file_with_copy(
        FIXTURE_IMG,
        summary.layout_id,
        summary.encoding,
        "/AUTOEXEC.BAT",
        replacement,
        output,
    )

    filesystem = FAT12()
    assert result.bytes == 1_600
    assert filesystem.probe(RawSectorImage(output.read_bytes()))
    assert filesystem.extract_file("/AUTOEXEC.BAT") == replacement.read_bytes()


def test_studio_rejects_fat12_replacement_larger_than_free_space(tmp_path: Path) -> None:
    summary = services.summarize_image(FIXTURE_IMG)
    replacement = tmp_path / "AUTOEXEC.BAT"
    replacement.write_bytes(b"x" * 2_000_000)

    try:
        services.replace_file_with_copy(
            FIXTURE_IMG,
            summary.layout_id,
            summary.encoding,
            "/AUTOEXEC.BAT",
            replacement,
            tmp_path / "patched.img",
        )
    except Exception as exc:
        assert "free FAT12 cluster" in str(exc)
    else:  # pragma: no cover - assertion clarity
        raise AssertionError("replacement beyond free space should fail")


def test_studio_rejects_replacement_over_original(tmp_path: Path) -> None:
    summary = services.summarize_image(FIXTURE_IMG)
    replacement = tmp_path / "AUTOEXEC.BAT"
    replacement.write_bytes(b"@ECHO OFF\r\nCLS\r\nREM FLUXCTL COPY TEST\r\n")

    try:
        services.replace_file_with_copy(
            FIXTURE_IMG,
            summary.layout_id,
            summary.encoding,
            "/AUTOEXEC.BAT",
            replacement,
            FIXTURE_IMG,
        )
    except Exception as exc:
        assert "new copy" in str(exc)
    else:  # pragma: no cover - assertion clarity
        raise AssertionError("replacement over original should fail")


def test_studio_imports_fat12_file_into_new_copy(tmp_path: Path) -> None:
    summary = services.summarize_image(FIXTURE_IMG)
    host_file = tmp_path / "README.TXT"
    host_file.write_bytes(b"Imported from Fluxctl Studio\r\n")
    output = tmp_path / "imported.img"

    result = services.import_file_with_copy(
        FIXTURE_IMG,
        summary.layout_id,
        summary.encoding,
        "/",
        host_file,
        output,
    )

    filesystem = FAT12()
    assert result.operation == "import-file"
    assert result.entries == 1
    assert result.bytes == host_file.stat().st_size
    assert filesystem.probe(RawSectorImage(output.read_bytes()))
    assert filesystem.extract_file("/README.TXT") == host_file.read_bytes()


def test_studio_creates_and_deletes_fat12_directory_in_new_copies(tmp_path: Path) -> None:
    summary = services.summarize_image(FIXTURE_IMG)
    created = tmp_path / "created.img"
    deleted = tmp_path / "deleted.img"

    create_result = services.create_directory_with_copy(
        FIXTURE_IMG,
        summary.layout_id,
        summary.encoding,
        "/",
        "TOOLS",
        created,
    )
    delete_result = services.delete_filesystem_entry_with_copy(
        created,
        summary.layout_id,
        summary.encoding,
        "/TOOLS",
        deleted,
    )

    created_fs = FAT12()
    deleted_fs = FAT12()
    assert create_result.operation == "create-directory"
    assert delete_result.operation == "delete"
    assert created_fs.probe(RawSectorImage(created.read_bytes()))
    assert any(entry.name == "TOOLS" and entry.is_dir for entry in created_fs.list_directory("/"))
    assert created_fs.list_directory("/TOOLS") == []
    assert deleted_fs.probe(RawSectorImage(deleted.read_bytes()))
    assert all(entry.name != "TOOLS" for entry in deleted_fs.list_directory("/"))


def test_studio_imports_fat12_directory_tree_into_new_copy(tmp_path: Path) -> None:
    summary = services.summarize_image(FIXTURE_IMG)
    host_dir = tmp_path / "TOOLS"
    nested = host_dir / "BIN"
    nested.mkdir(parents=True)
    (host_dir / "README.TXT").write_bytes(b"root file\r\n")
    (nested / "RUN.BAT").write_bytes(b"echo nested\r\n")
    output = tmp_path / "tree.img"

    result = services.import_directory_with_copy(
        FIXTURE_IMG,
        summary.layout_id,
        summary.encoding,
        "/",
        host_dir,
        output,
    )

    filesystem = FAT12()
    assert result.operation == "import-directory"
    assert result.entries == 4
    assert result.bytes == 24
    assert filesystem.probe(RawSectorImage(output.read_bytes()))
    assert any(entry.name == "TOOLS" and entry.is_dir for entry in filesystem.list_directory("/"))
    assert filesystem.extract_file("/TOOLS/README.TXT") == b"root file\r\n"
    assert filesystem.extract_file("/TOOLS/BIN/RUN.BAT") == b"echo nested\r\n"


def test_studio_rejects_fat12_import_with_long_name(tmp_path: Path) -> None:
    summary = services.summarize_image(FIXTURE_IMG)
    host_file = tmp_path / "LONGFILENAME.TXT"
    host_file.write_bytes(b"too long")

    try:
        services.import_file_with_copy(
            FIXTURE_IMG,
            summary.layout_id,
            summary.encoding,
            "/",
            host_file,
            tmp_path / "imported.img",
        )
    except Exception as exc:
        assert "8.3" in str(exc)
    else:  # pragma: no cover - assertion clarity
        raise AssertionError("long FAT12 import name should fail")


def test_studio_command_runner_uses_current_fluxctl() -> None:
    result = services.run_fluxctl_command(["doctor", "--json"])

    assert result.returncode == 0
    assert '"tool": "fluxctl"' in result.stdout
