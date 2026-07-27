from pathlib import Path

from fluxctl import studio_services as services


FIXTURE_IMG = Path("tests/fixtures/3.5inch/IBM/IBM-Generic-DSDD-MFM-IBMPC-720K.img")


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


def test_studio_command_runner_uses_current_fluxctl() -> None:
    result = services.run_fluxctl_command(["doctor", "--json"])

    assert result.returncode == 0
    assert '"tool": "fluxctl"' in result.stdout
