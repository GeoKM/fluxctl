from pathlib import Path

from fluxctl import studio_services as services


FIXTURE_IMG = Path("tests/fixtures/3.5inch/IBM/IBM-Generic-DSDD-MFM-IBMPC-720K.img")
FIXTURE_D64 = Path("tests/fixtures/5.25inch/Commodore/Commodore-1541-SSDD-GCR-C64-170K.d64")
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


def test_studio_lists_1581_cbm_dos_files() -> None:
    summary = services.summarize_image(FIXTURE_1581_D81)
    entries = services.list_files(FIXTURE_1581_D81, summary.layout_id, summary.encoding)

    assert summary.filesystem == "cbm_dos_1581"
    assert any(entry.name == "HOW TO USE" for entry in entries)
    assert any(entry.name == "PIC.DIR" and entry.kind == "<DIR>" for entry in entries)


def test_studio_lists_amiga_dos_root_entries() -> None:
    summary = services.summarize_image(FIXTURE_ADF)
    entries = services.list_files(FIXTURE_ADF, summary.layout_id, summary.encoding)

    assert summary.filesystem == "amiga_ffs"
    assert any(entry.name == "Devs" and entry.kind == "<DIR>" for entry in entries)
    assert any(entry.name == "Install" and entry.kind == "<DIR>" for entry in entries)


def test_studio_command_runner_uses_current_fluxctl() -> None:
    result = services.run_fluxctl_command(["doctor", "--json"])

    assert result.returncode == 0
    assert '"tool": "fluxctl"' in result.stdout
