from pathlib import Path

import pytest
from typer.testing import CliRunner

from fluxctl import cli
from fluxctl.cli import _prepare_image
from fluxctl.exceptions import FilesystemError
from fluxctl.filesystems import TrackSectorImage
from fluxctl.filesystems.displaywriter import DisplaywriterFS
from fluxctl.layouts.loader import load_builtin_layouts
from fluxctl.sector.models import Sector, TrackSectors


FIXTURE_DISPLAYWRITER = Path("tests/fixtures/8inch/IBM/IBM-6580-SSDD-FM-DisplayWriter-284K.scp")


def _displaywriter_2d_image() -> TrackSectorImage:
    """Minimal 2D label geometry: FM index track plus a second head."""

    def sector(track: int, head: int, sector_id: int, size: int, data: bytes = b"") -> Sector:
        return Sector(track, head, sector_id, 1, data.ljust(size, b"\x40"), True, 1.0)

    index = [sector(0, 0, sector_id, 128) for sector_id in range(1, 27)]
    index[4] = sector(0, 0, 5, 128, "ERMAP".encode("cp037"))
    index[6] = sector(0, 0, 7, 128, "VOL1DYSAN".encode("cp037"))
    index[7] = sector(0, 0, 8, 128, "HDR1LEGK1".encode("cp037"))
    data_track = [sector(0, 1, sector_id, 256) for sector_id in range(1, 27)]
    return TrackSectorImage(
        [TrackSectors(0, 0, index), TrackSectors(0, 1, data_track)],
        bytes_per_sector=256,
    )


def test_displaywriter_accepts_double_sided_2d_label_geometry() -> None:
    load_builtin_layouts()
    fs = DisplaywriterFS()

    assert fs.probe(_displaywriter_2d_image())
    assert fs.metadata()["volume_label"] == "DYSAN"


def test_displaywriter_lists_wpe_container_and_metadata() -> None:
    load_builtin_layouts()
    image = _prepare_image(FIXTURE_DISPLAYWRITER, "ibm_displaywriter_fm_284k", "fm")
    fs = DisplaywriterFS()

    assert fs.probe(image)
    entries = fs.list_directory("/")

    assert [entry.name for entry in entries] == ["WPE"]
    assert entries[0].is_dir
    assert entries[0].cluster_start == 8
    assert entries[0].attributes == 256
    assert entries[0].size == 291840

    metadata = fs.metadata()
    assert metadata["volume_label"] == "JOBAP4"
    assert metadata["container"] == "WPE"
    assert metadata["container_payload_bytes"] == 291840
    assert metadata["document_catalog_decoded"] is False
    assert {"sector": 7, "kind": "VOL1", "name": "JOBAP4"} in metadata["track0_control_records"]
    assert {"sector": 8, "kind": "HDR1", "name": "WPE"} in metadata["track0_control_records"]


def test_displaywriter_exposes_raw_container_and_control_records() -> None:
    load_builtin_layouts()
    image = _prepare_image(FIXTURE_DISPLAYWRITER, "ibm_displaywriter_fm_284k", "fm")
    fs = DisplaywriterFS()

    assert fs.probe(image)
    wpe_entries = fs.list_directory("/WPE")
    assert [(entry.name, entry.is_dir) for entry in wpe_entries] == [
        ("WPE.DATA", False),
        ("_CONTROL", True),
    ]

    payload = fs.extract_file("/WPE/WPE.DATA")
    assert len(payload) == 291840
    assert b"\xc5\xd9\xd4\xc1\xd7" not in payload[:128]  # ERMAP is track-0 control data, not payload.

    control_entries = fs.list_directory("/WPE/_CONTROL")
    control_names = [entry.name for entry in control_entries]
    assert "05-ERMAP-ERMAP.txt" in control_names
    assert "07-VOL1-JOBAP4.txt" in control_names
    assert "08-HDR1-WPE.txt" in control_names
    assert "09-D-D-S09.raw" in control_names

    assert fs.extract_file("/WPE/_CONTROL/07-VOL1-JOBAP4.txt").startswith(b"VOL1JOBAP4")
    assert fs.extract_file("/WPE/_CONTROL/09-D-D-S09.raw").startswith(b"\xc4")


def test_displaywriter_cli_lists_nested_container_without_out() -> None:
    runner = CliRunner()

    result = runner.invoke(
        cli.app,
        [
            "extract",
            str(FIXTURE_DISPLAYWRITER),
            "--list",
            "--path",
            "/WPE",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "WPE.DATA\t291840 bytes" in result.output
    assert "_CONTROL\t<DIR>" in result.output


def test_displaywriter_file_extraction_is_explicitly_unsupported() -> None:
    load_builtin_layouts()
    image = _prepare_image(FIXTURE_DISPLAYWRITER, "ibm_displaywriter_fm_284k", "fm")
    fs = DisplaywriterFS()
    assert fs.probe(image)

    with pytest.raises(FilesystemError, match="document extraction not implemented"):
        fs.extract_file("/WPE/UNKNOWN.DOC")
