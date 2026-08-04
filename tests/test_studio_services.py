from pathlib import Path

from fluxctl import studio_services as services
from fluxctl.exporters.d64 import DEFAULT_SECTORS_PER_TRACK, SECTOR_SIZE
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


def test_studio_reports_fat12_file_allocation_for_map_overlay() -> None:
    allocation = services.file_allocation_for_image(FIXTURE_IMG, "ibm_mfm_720k", "mfm", "/AUTOEXEC.BAT")

    assert allocation.path == "/AUTOEXEC.BAT"
    assert allocation.sectors == {(73, 1, 4), (73, 1, 5)}


def test_studio_reports_cbm_dos_file_allocation_for_map_overlay() -> None:
    allocation = services.file_allocation_for_image(FIXTURE_D64, "commodore_gcr_1541_170k", "gcr", "/C/FAT ASCII")

    assert allocation.sectors == {(16, 0, 6), (16, 0, 16)}
    assert allocation.logical_sectors == {(17, 0, 6), (17, 0, 16)}


def test_studio_reports_1581_file_allocation_for_map_overlay() -> None:
    allocation = services.file_allocation_for_image(
        FIXTURE_1581_D81,
        "commodore_mfm_1581_800k",
        "mfm",
        "/HOW TO USE",
    )

    assert len(allocation.sectors) == 26
    assert len(allocation.logical_sectors or set()) == 50
    assert (47, 0, 3) in allocation.sectors
    assert (48, 0, 5) in (allocation.logical_sectors or set())


def test_studio_reports_amiga_file_allocation_for_map_overlay() -> None:
    allocation = services.file_allocation_for_image(FIXTURE_ADF, "amiga_mfm_880k", "mfm", "/C/Assign")

    assert allocation.sectors == {
        (32, 1, 5),
        (32, 1, 6),
        (32, 1, 7),
        (32, 1, 8),
        (32, 1, 9),
        (32, 1, 10),
        (32, 1, 11),
    }


def test_studio_reports_c64_cpm_file_allocation_for_map_overlay() -> None:
    allocation = services.file_allocation_for_image(
        FIXTURE_CPM_D64,
        "commodore_gcr_1541_170k",
        "gcr",
        "/ASM.COM",
    )

    assert len(allocation.sectors) == 32
    assert (9, 0, 5) in allocation.sectors
    assert (10, 0, 2) in allocation.sectors


def test_studio_creates_blank_image_presets(tmp_path) -> None:
    expected_filesystems = {
        "fat12_180k": "fat12",
        "fat12_360k": "fat12",
        "fat12_720k": "fat12",
        "fat12_1200k": "fat12",
        "fat12_1440k": "fat12",
        "cbm_dos_1541_d64": "cbm_dos",
        "cbm_dos_1571_d71": "cbm_dos_1571",
        "cbm_dos_1581_d81": "cbm_dos_1581",
        "amiga_ofs_adf": "amiga_ofs",
    }

    for preset in services.blank_image_presets():
        output = tmp_path / f"{preset.preset_id}{preset.suffix}"
        result = services.create_blank_image(preset.preset_id, output)
        summary = services.summarize_image(output)
        entries = services.list_files(output, summary.layout_id, summary.encoding)

        assert output.stat().st_size == preset.size
        assert result.size == preset.size
        assert summary.layout_id == preset.layout_id
        assert summary.filesystem == expected_filesystems[preset.preset_id]
        assert entries == []


def test_studio_blank_image_overwrite_is_explicit(tmp_path) -> None:
    output = tmp_path / "blank.img"
    output.write_bytes(b"existing")

    try:
        services.create_blank_image("fat12_180k", output)
    except Exception as exc:
        assert "already exists" in str(exc)
    else:  # pragma: no cover - assertion clarity
        raise AssertionError("blank image creation should reject existing files by default")

    result = services.create_blank_image("fat12_180k", output, overwrite=True)

    assert result.size == 184320
    assert output.stat().st_size == 184320
    assert output.read_bytes() != b"existing"


def test_studio_blank_fat12_image_accepts_file_import(tmp_path) -> None:
    output = tmp_path / "blank.img"
    host_file = tmp_path / "README.TXT"
    host_file.write_text("HELLO", encoding="ascii")
    services.create_blank_image("fat12_720k", output)

    imported = services.import_file_with_copy(
        output,
        "ibm_mfm_720k",
        "mfm",
        "/",
        host_file,
        tmp_path / "with-file.img",
    )
    entries = services.list_files(Path(imported.path), "ibm_mfm_720k", "mfm")

    assert imported.filesystem == "fat12"
    assert [entry.name for entry in entries] == ["README.TXT"]
    assert entries[0].size == 5


def test_studio_blank_cbm_dos_images_accept_file_import(tmp_path) -> None:
    for preset_id, suffix, layout_id, filesystem_name, entry_name in [
        ("cbm_dos_1541_d64", ".d64", "commodore_gcr_1541_170k", "cbm_dos", "HELLO64"),
        ("cbm_dos_1571_d71", ".d71", "commodore_gcr_1571_341k", "cbm_dos_1571", "HELLO71"),
    ]:
        output = tmp_path / f"blank{suffix}"
        host_file = tmp_path / f"{entry_name}.PRG"
        host_file.write_bytes(bytes(range(256)) * 2)
        services.create_blank_image(preset_id, output)

        imported = services.import_file_with_copy(
            output,
            layout_id,
            "gcr",
            "/",
            host_file,
            tmp_path / f"with-file{suffix}",
        )
        entries = services.list_files(Path(imported.path), layout_id, "gcr")
        dump = services.file_hex_dump(Path(imported.path), layout_id, "gcr", "/" + entry_name)

        assert imported.filesystem == filesystem_name
        assert [entry.name for entry in entries] == [entry_name]
        assert entries[0].size == 512
        assert dump.size == 512


def test_studio_cbm_dos_images_replace_and_delete_files(tmp_path) -> None:
    for preset_id, suffix, layout_id, filesystem_name, entry_name in [
        ("cbm_dos_1541_d64", ".d64", "commodore_gcr_1541_170k", "cbm_dos", "HELLO64"),
        ("cbm_dos_1571_d71", ".d71", "commodore_gcr_1571_341k", "cbm_dos_1571", "HELLO71"),
    ]:
        output = tmp_path / f"blank{suffix}"
        imported_path = tmp_path / f"with-file{suffix}"
        replaced_path = tmp_path / f"replaced{suffix}"
        deleted_path = tmp_path / f"deleted{suffix}"
        host_file = tmp_path / f"{entry_name}.PRG"
        replacement = tmp_path / f"{entry_name}-replacement.bin"
        host_file.write_bytes(b"A" * 512)
        replacement.write_bytes(b"B" * 900)
        services.create_blank_image(preset_id, output)
        services.import_file_with_copy(output, layout_id, "gcr", "/", host_file, imported_path)

        replaced = services.replace_file_with_copy(
            imported_path,
            layout_id,
            "gcr",
            "/" + entry_name,
            replacement,
            replaced_path,
        )
        entries = services.list_files(replaced_path, layout_id, "gcr")
        dump = services.file_hex_dump(replaced_path, layout_id, "gcr", "/" + entry_name)
        extracted = services.export_filesystem_entries(
            replaced_path,
            layout_id,
            "gcr",
            ["/" + entry_name],
            tmp_path / f"exported{suffix}",
        )

        assert replaced.filesystem == filesystem_name
        assert entries[0].name == entry_name
        assert entries[0].size == 900
        assert dump.size == 900
        assert (Path(extracted.path) / entry_name).read_bytes() == b"B" * 900

        deleted = services.delete_filesystem_entry_with_copy(
            replaced_path,
            layout_id,
            "gcr",
            "/" + entry_name,
            deleted_path,
        )
        assert deleted.filesystem == filesystem_name
        assert services.list_files(deleted_path, layout_id, "gcr") == []


def test_studio_blank_1571_image_has_validated_side_bam(tmp_path) -> None:
    output = tmp_path / "blank.d71"
    services.create_blank_image("cbm_dos_1571_d71", output)
    data = output.read_bytes()
    sectors_per_track = list(DEFAULT_SECTORS_PER_TRACK) * 2

    def offset(track: int, sector: int) -> int:
        return (sum(sectors_per_track[: track - 1]) + sector) * SECTOR_SIZE

    primary_bam = data[offset(18, 0) : offset(18, 0) + SECTOR_SIZE]
    side_bam = data[offset(53, 0) : offset(53, 0) + SECTOR_SIZE]

    assert primary_bam[3] == 0x80
    assert list(primary_bam[221:238]) == [21] * 17
    assert primary_bam[238] == 0
    assert list(primary_bam[239:245]) == [19] * 6
    assert list(primary_bam[245:251]) == [18] * 6
    assert list(primary_bam[251:256]) == [17] * 5
    assert side_bam[(53 - 36) * 3 : (53 - 36) * 3 + 3] == b"\x00\x00\x00"


def test_studio_1571_import_updates_side_two_bam_counts(tmp_path) -> None:
    output = tmp_path / "blank.d71"
    imported_path = tmp_path / "imported.d71"
    host_file = tmp_path / "BIG.PRG"
    host_file.write_bytes(b"X" * 170_000)
    services.create_blank_image("cbm_dos_1571_d71", output)

    services.import_file_with_copy(
        output,
        "commodore_gcr_1571_341k",
        "gcr",
        "/",
        host_file,
        imported_path,
    )
    data = imported_path.read_bytes()
    sectors_per_track = list(DEFAULT_SECTORS_PER_TRACK) * 2

    def offset(track: int, sector: int) -> int:
        return (sum(sectors_per_track[: track - 1]) + sector) * SECTOR_SIZE

    primary_bam = data[offset(18, 0) : offset(18, 0) + SECTOR_SIZE]
    side_bam = data[offset(53, 0) : offset(53, 0) + SECTOR_SIZE]

    assert primary_bam[221] < 21
    assert side_bam[0] != 0xFF


def test_studio_blank_1581_image_has_bam_and_accepts_file_import(tmp_path) -> None:
    output = tmp_path / "blank.d81"
    host_file = tmp_path / "HELLO81.PRG"
    host_file.write_bytes(b"1581 DATA" * 80)
    services.create_blank_image("cbm_dos_1581_d81", output)

    before_map = services.build_disk_map_for_image(output, "commodore_mfm_1581_800k", "mfm", map_view="bam")
    before_states = {state for row in before_map.tracks for state in row}
    imported = services.import_file_with_copy(
        output,
        "commodore_mfm_1581_800k",
        "mfm",
        "/",
        host_file,
        tmp_path / "with-file.d81",
    )
    entries = services.list_files(Path(imported.path), "commodore_mfm_1581_800k", "mfm")
    dump = services.file_hex_dump(Path(imported.path), "commodore_mfm_1581_800k", "mfm", "/HELLO81")
    after_map = services.build_disk_map_for_image(
        Path(imported.path), "commodore_mfm_1581_800k", "mfm", map_view="bam"
    )
    after_file_blocks = sum(row.count("bam_file") for row in after_map.tracks)

    assert imported.filesystem == "cbm_dos_1581"
    assert before_map.render_style == "grid"
    assert before_map.address_style == "cbm_logical"
    assert before_map.total_tracks == 80
    assert before_map.max_sectors_per_track == 40
    assert before_states >= {"bam_free", "bam_system"}
    assert [entry.name for entry in entries] == ["HELLO81"]
    assert entries[0].size == len(host_file.read_bytes())
    assert dump.size == len(host_file.read_bytes())
    assert after_file_blocks >= 3


def test_studio_1581_image_replaces_and_deletes_root_file(tmp_path) -> None:
    output = tmp_path / "blank.d81"
    imported_path = tmp_path / "with-file.d81"
    replaced_path = tmp_path / "replaced.d81"
    deleted_path = tmp_path / "deleted.d81"
    host_file = tmp_path / "HELLO81.PRG"
    replacement = tmp_path / "replacement.bin"
    host_file.write_bytes(b"A" * 512)
    replacement.write_bytes(b"C" * 1200)
    services.create_blank_image("cbm_dos_1581_d81", output)
    services.import_file_with_copy(output, "commodore_mfm_1581_800k", "mfm", "/", host_file, imported_path)

    replaced = services.replace_file_with_copy(
        imported_path,
        "commodore_mfm_1581_800k",
        "mfm",
        "/HELLO81",
        replacement,
        replaced_path,
    )
    entries = services.list_files(replaced_path, "commodore_mfm_1581_800k", "mfm")
    dump = services.file_hex_dump(replaced_path, "commodore_mfm_1581_800k", "mfm", "/HELLO81")
    extracted = services.export_filesystem_entries(
        replaced_path,
        "commodore_mfm_1581_800k",
        "mfm",
        ["/HELLO81"],
        tmp_path / "exported81",
    )

    assert replaced.filesystem == "cbm_dos_1581"
    assert entries[0].name == "HELLO81"
    assert entries[0].size == 1200
    assert dump.size == 1200
    assert (Path(extracted.path) / "HELLO81").read_bytes() == b"C" * 1200

    deleted = services.delete_filesystem_entry_with_copy(
        replaced_path,
        "commodore_mfm_1581_800k",
        "mfm",
        "/HELLO81",
        deleted_path,
    )
    assert deleted.filesystem == "cbm_dos_1581"
    assert services.list_files(deleted_path, "commodore_mfm_1581_800k", "mfm") == []


def test_studio_1581_image_creates_directory_and_imports_file_into_it(tmp_path) -> None:
    output = tmp_path / "blank.d81"
    directory_path = tmp_path / "with-dir.d81"
    imported_path = tmp_path / "with-dir-file.d81"
    host_file = tmp_path / "NOTE.PRG"
    host_file.write_bytes(b"inside 1581 directory")
    services.create_blank_image("cbm_dos_1581_d81", output)

    created = services.create_directory_with_copy(
        output,
        "commodore_mfm_1581_800k",
        "mfm",
        "/",
        "TOOLS",
        directory_path,
    )
    root_entries = services.list_files(directory_path, "commodore_mfm_1581_800k", "mfm")

    assert created.filesystem == "cbm_dos_1581"
    assert any(entry.name == "TOOLS" and entry.is_dir for entry in root_entries)
    assert services.list_files(directory_path, "commodore_mfm_1581_800k", "mfm", "/TOOLS") == []

    imported = services.import_file_with_copy(
        directory_path,
        "commodore_mfm_1581_800k",
        "mfm",
        "/TOOLS",
        host_file,
        imported_path,
    )
    tool_entries = services.list_files(imported_path, "commodore_mfm_1581_800k", "mfm", "/TOOLS")
    extracted = services.export_filesystem_entries(
        imported_path,
        "commodore_mfm_1581_800k",
        "mfm",
        ["/TOOLS/NOTE"],
        tmp_path / "exported-tools",
    )

    assert imported.filesystem == "cbm_dos_1581"
    assert [entry.name for entry in tool_entries] == ["NOTE"]
    assert (Path(extracted.path) / "NOTE").read_bytes() == host_file.read_bytes()


def test_studio_1581_image_imports_directory_tree(tmp_path) -> None:
    output = tmp_path / "blank.d81"
    imported_path = tmp_path / "imported-tree.d81"
    host_root = tmp_path / "PROJECT"
    host_bin = host_root / "BIN"
    host_bin.mkdir(parents=True)
    (host_root / "README.PRG").write_bytes(b"root note")
    (host_bin / "RUN.PRG").write_bytes(b"nested note")
    services.create_blank_image("cbm_dos_1581_d81", output)

    imported = services.import_directory_with_copy(
        output,
        "commodore_mfm_1581_800k",
        "mfm",
        "/",
        host_root,
        imported_path,
    )
    root_entries = services.list_files(imported_path, "commodore_mfm_1581_800k", "mfm")
    project_entries = services.list_files(imported_path, "commodore_mfm_1581_800k", "mfm", "/PROJECT")
    bin_entries = services.list_files(imported_path, "commodore_mfm_1581_800k", "mfm", "/PROJECT/BIN")
    exported = services.export_filesystem_entry(
        imported_path,
        "commodore_mfm_1581_800k",
        "mfm",
        "/PROJECT",
        tmp_path / "exported-tree",
    )

    assert imported.filesystem == "cbm_dos_1581"
    assert imported.entries == 4
    assert imported.bytes == len(b"root note") + len(b"nested note")
    assert any(entry.name == "PROJECT" and entry.is_dir for entry in root_entries)
    assert {entry.name for entry in project_entries} == {"README", "BIN"}
    assert [entry.name for entry in bin_entries] == ["RUN"]
    assert (Path(exported.path) / "README").read_bytes() == b"root note"
    assert (Path(exported.path) / "BIN" / "RUN").read_bytes() == b"nested note"


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
    heads = {head for _track, head in disk_map.track_ids}

    assert disk_map.render_style == "grid"
    assert disk_map.address_style == "cbm_logical"
    assert disk_map.total_tracks == 40
    assert heads == {0}
    assert disk_map.track_ids[0] == (1, 0)
    assert disk_map.track_ids[-1] == (40, 0)
    assert (18, 0) in disk_map.track_ids
    assert "bam_file" in states
    assert "bam_system" in states
    assert "bam_free" in states


def test_studio_builds_two_head_1571_bam_block_map() -> None:
    disk_map = services.build_disk_map_for_image(FIXTURE_D71, "commodore_gcr_1571_341k", "gcr", map_view="bam")
    heads = {head for _track, head in disk_map.track_ids}

    assert disk_map.render_style == "grid"
    assert heads == {0, 1}
    assert disk_map.total_tracks == 70
    assert (1, 0) in disk_map.track_ids
    assert (35, 0) in disk_map.track_ids
    assert (36, 1) in disk_map.track_ids
    assert (70, 1) in disk_map.track_ids


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


def test_studio_reports_cbm_volume_metadata_for_files_panel() -> None:
    summary = services.summarize_image(FIXTURE_D64)
    listing = services.list_files_with_info(FIXTURE_D64, summary.layout_id, summary.encoding)

    assert "Name: KBBS FONTS" in listing.volume_text
    assert "DOS: 2A" in listing.volume_text
    assert any(entry.name == "C/FAT ASCII" for entry in listing.entries)


def test_studio_reports_1571_and_1581_volume_metadata_for_files_panel() -> None:
    d71_summary = services.summarize_image(FIXTURE_D71)
    d71_listing = services.list_files_with_info(FIXTURE_D71, d71_summary.layout_id, d71_summary.encoding)
    d81_summary = services.summarize_image(FIXTURE_1581_D81)
    d81_listing = services.list_files_with_info(FIXTURE_1581_D81, d81_summary.layout_id, d81_summary.encoding)

    assert "Name: KBBS BACKUP 1" in d71_listing.volume_text
    assert "ID: KB" in d71_listing.volume_text
    assert "DOS: 2A" in d71_listing.volume_text
    assert "Name: 1581 UTILITY V02" in d81_listing.volume_text
    assert "ID: GB" in d81_listing.volume_text
    assert "DOS: 3D" in d81_listing.volume_text


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
