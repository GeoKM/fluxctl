import hashlib
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from fluxctl.cli import app
from fluxctl.exporters import load_builtin_exporters
from fluxctl.exporters.imd import IMDExporter
from fluxctl.exporters.raw_img import RawIMGExporter
from fluxctl.exceptions import ExportError
from fluxctl.filesystems import RawSectorImage, TrackSectorImage
from fluxctl.imd import load_imd_image
from fluxctl.layouts.loader import ensure_layout_loaded, load_builtin_layouts
from fluxctl.scp import sha256_file
from fluxctl.sector.models import Sector, TrackSectors

runner = CliRunner()


@pytest.fixture(scope="module")
def img_fixture() -> Path:
    return Path("tests/fixtures/3.5inch/IBM/IBM-Generic-DSDD-MFM-IBMPC-720K.img")


@pytest.fixture(scope="module")
def layout_720k():
    load_builtin_layouts()
    return ensure_layout_loaded("ibm_mfm_720k")


def _build_track_image(raw_path: Path, layout_id: str, cylinders: int = 1) -> TrackSectorImage:
    layout = ensure_layout_loaded(layout_id)
    data = raw_path.read_bytes()
    lba = 0
    tracks: list[TrackSectors] = []
    for cyl in range(cylinders):
        for head in range(layout.sides):
            sectors: list[Sector] = []
            for sector_id in range(1, layout.sectors_per_track + 1):
                start = lba * layout.sector_size
                end = start + layout.sector_size
                payload = data[start:end]
                sectors.append(
                    Sector(
                        cylinder=cyl,
                        head=head,
                        sector_id=sector_id,
                        size_code=2,
                        data=payload,
                        crc_ok=True,
                        confidence=1.0,
                    )
                )
                lba += 1
            tracks.append(TrackSectors(track=cyl, head=head, sectors=sectors))
    return TrackSectorImage(tracks, bytes_per_sector=layout.sector_size)


def test_raw_export_cli(tmp_path, img_fixture: Path) -> None:
    out_path = tmp_path / "disk.img"
    result = runner.invoke(
        app,
        ["convert", str(img_fixture), "--to", "raw", "--out", str(out_path)],
    )
    assert result.exit_code == 0, result.output
    assert out_path.exists()
    assert out_path.stat().st_size == img_fixture.stat().st_size
    provenance = json.loads(out_path.with_suffix(out_path.suffix + ".provenance.json").read_text())
    assert provenance["input_sha256"] == sha256_file(img_fixture)
    assert provenance["output_sha256"] == hashlib.sha256(out_path.read_bytes()).hexdigest()


def test_raw_export_from_scp_respects_layout_bounds(tmp_path: Path, img_fixture: Path) -> None:
    scp_fixture = Path("tests/fixtures/3.5inch/IBM/IBM-Generic-DSDD-MFM-IBMPC-720K.scp")
    out_path = tmp_path / "from_scp.img"

    result = runner.invoke(
        app,
        [
            "convert",
            str(scp_fixture),
            "--layout",
            "ibm_mfm_720k",
            "--to",
            "raw",
            "--out",
            str(out_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert out_path.read_bytes() == img_fixture.read_bytes()


def test_imd_export_from_tracks(tmp_path, img_fixture: Path, layout_720k) -> None:
    image_obj = _build_track_image(img_fixture, layout_720k.layout_id, cylinders=1)
    exporter = IMDExporter()
    assert exporter.supports(image_obj)
    payload = exporter.export(image_obj)
    out_path = tmp_path / "disk.imd"
    out_path.write_bytes(payload)
    provenance = {
        "input_sha256": sha256_file(img_fixture),
        "tool_name": "fluxctl-test",
    }
    out_path.with_suffix(out_path.suffix + ".provenance.json").write_text(json.dumps(provenance))
    assert payload.startswith(b"IMD")


def test_imd_export_from_flat_img_uses_layout(tmp_path: Path, img_fixture: Path) -> None:
    out_path = tmp_path / "disk.imd"

    result = runner.invoke(
        app,
        [
            "convert",
            str(img_fixture),
            "--layout",
            "ibm_mfm_720k",
            "--to",
            "imd",
            "--out",
            str(out_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert out_path.read_bytes().startswith(b"IMD")
    provenance = json.loads(out_path.with_suffix(out_path.suffix + ".provenance.json").read_text())
    assert provenance["parameters"]["layout"] == "ibm_mfm_720k"
    assert provenance["encoder"] == "imd"

    layout = ensure_layout_loaded("ibm_mfm_720k")
    tracks, geometry, _meta = load_imd_image(out_path)
    assert len(tracks) == layout.tracks * layout.sides
    assert geometry.sector_size == 512


def test_exporters_support_track_image(img_fixture: Path, layout_720k) -> None:
    load_builtin_exporters()
    image_obj = _build_track_image(img_fixture, layout_720k.layout_id, cylinders=1)

    assert RawIMGExporter().supports(image_obj)
    assert IMDExporter().supports(image_obj)


def test_imd_rejects_flat_image() -> None:
    assert not IMDExporter().supports(RawSectorImage(b""))


def test_raw_export_rejects_mixed_sector_sizes() -> None:
    tracks = [
        TrackSectors(
            track=0,
            head=0,
            sectors=[
                Sector(
                    cylinder=0,
                    head=0,
                    sector_id=1,
                    size_code=2,
                    data=b"\x00" * 512,
                    crc_ok=True,
                    confidence=1.0,
                ),
                Sector(
                    cylinder=0,
                    head=0,
                    sector_id=2,
                    size_code=3,
                    data=b"\x00" * 1024,
                    crc_ok=True,
                    confidence=1.0,
                ),
            ],
        )
    ]

    with pytest.raises(ExportError):
        RawIMGExporter().export(TrackSectorImage(tracks, bytes_per_sector=512))


def test_raw_export_preserves_zero_based_sector_zero() -> None:
    track = TrackSectors(
        track=0,
        head=0,
        sectors=[
            Sector(
                cylinder=0,
                head=0,
                sector_id=0,
                size_code=1,
                data=b"A" * 256,
                crc_ok=True,
                confidence=1.0,
            ),
            Sector(
                cylinder=0,
                head=0,
                sector_id=1,
                size_code=1,
                data=b"B" * 256,
                crc_ok=True,
                confidence=1.0,
            ),
        ],
    )

    exported = RawIMGExporter().export(TrackSectorImage([track], bytes_per_sector=256))

    assert exported[:256] == b"A" * 256
    assert exported[256:512] == b"B" * 256


def test_raw_export_uses_layout_geometry_for_missing_tracks() -> None:
    load_builtin_layouts()
    layout = ensure_layout_loaded("commodore_gcr_1541_170k")
    track = TrackSectors(
        track=0,
        head=0,
        sectors=[
            Sector(
                cylinder=0,
                head=0,
                sector_id=0,
                size_code=1,
                data=b"A" * 256,
                crc_ok=True,
                confidence=1.0,
            )
        ],
    )
    image = TrackSectorImage([track], bytes_per_sector=256)
    image.layout = layout

    exported = RawIMGExporter().export(image)

    assert len(exported) == sum(layout.track_sectors or []) * layout.sector_size
    assert exported[:256] == b"A" * 256
    assert exported[256:] == b"\x00" * (len(exported) - 256)


def test_imd_export_rejects_mixed_sector_sizes() -> None:
    tracks = [
        TrackSectors(
            track=1,
            head=0,
            sectors=[
                Sector(
                    cylinder=1,
                    head=0,
                    sector_id=1,
                    size_code=2,
                    data=b"\x00" * 512,
                    crc_ok=True,
                    confidence=1.0,
                ),
                Sector(
                    cylinder=1,
                    head=0,
                    sector_id=2,
                    size_code=1,
                    data=b"\x00" * 256,
                    crc_ok=True,
                    confidence=1.0,
                ),
            ],
        )
    ]

    with pytest.raises(ExportError):
        IMDExporter().export(TrackSectorImage(tracks, bytes_per_sector=512))


def test_convert_scp_auto_detects_layout_before_decoding(tmp_path: Path) -> None:
    scp_fixture = Path("tests/fixtures/5.25inch/Commodore/Commodore-1541-SSDD-GCR-C64-170K.scp")
    out_path = tmp_path / "disk.d64"

    result = runner.invoke(
        app,
        [
            "convert",
            str(scp_fixture),
            "--to",
            "d64",
            "--out",
            str(out_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Auto-detected layout commodore_gcr_1541_170k (gcr)" in result.output
    assert out_path.exists()
    provenance = json.loads(out_path.with_suffix(out_path.suffix + ".provenance.json").read_text())
    assert provenance["parameters"]["resolved_layout"] == "commodore_gcr_1541_170k"
    assert provenance["parameters"]["encoding"] == "gcr"


def test_convert_amiga_scp_to_adf_preserves_filesystem(tmp_path: Path) -> None:
    scp_fixture = Path("tests/fixtures/3.5inch/Commodore/Commodore-1010-DSDD-MFM-Amiga-880K.scp")
    out_path = tmp_path / "amiga.adf"

    result = runner.invoke(
        app,
        [
            "convert",
            str(scp_fixture),
            "--to",
            "adf",
            "--out",
            str(out_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert out_path.stat().st_size == 901120

    list_result = runner.invoke(app, ["extract", str(out_path), "--list"])

    assert list_result.exit_code == 0, list_result.output
    assert "C\t<DIR>" in list_result.output
    assert "Installer\t61640 bytes" in list_result.output


def test_convert_1581_img_to_d81_matches_fixture(tmp_path: Path) -> None:
    img_fixture = Path("tests/fixtures/3.5inch/Commodore/Commodore-1581-DSDD-MFM-C64-800K.img")
    d81_fixture = Path("tests/fixtures/3.5inch/Commodore/Commodore-1581-DSDD-MFM-C64-800K.d81")
    out_path = tmp_path / "disk.d81"

    result = runner.invoke(
        app,
        [
            "convert",
            str(img_fixture),
            "--layout",
            "commodore_mfm_1581_800k",
            "--to",
            "d81",
            "--out",
            str(out_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert out_path.read_bytes() == d81_fixture.read_bytes()


def test_convert_1581_d81_to_raw_img_matches_fixture(tmp_path: Path) -> None:
    img_fixture = Path("tests/fixtures/3.5inch/Commodore/Commodore-1581-DSDD-MFM-C64-800K.img")
    d81_fixture = Path("tests/fixtures/3.5inch/Commodore/Commodore-1581-DSDD-MFM-C64-800K.d81")
    out_path = tmp_path / "disk.img"

    result = runner.invoke(
        app,
        [
            "convert",
            str(d81_fixture),
            "--to",
            "raw",
            "--out",
            str(out_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert out_path.read_bytes() == img_fixture.read_bytes()


def test_convert_1581_scp_to_d81_matches_fixture(tmp_path: Path) -> None:
    scp_fixture = Path("tests/fixtures/3.5inch/Commodore/Commodore-1581-DSDD-MFM-C64-800K.scp")
    d81_fixture = Path("tests/fixtures/3.5inch/Commodore/Commodore-1581-DSDD-MFM-C64-800K.d81")
    out_path = tmp_path / "disk.d81"

    result = runner.invoke(
        app,
        [
            "convert",
            str(scp_fixture),
            "--layout",
            "commodore_mfm_1581_800k",
            "--to",
            "d81",
            "--out",
            str(out_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert out_path.read_bytes() == d81_fixture.read_bytes()


def test_convert_1571_scp_to_d71_matches_fixture(tmp_path: Path) -> None:
    scp_fixture = Path("tests/fixtures/5.25inch/Commodore/Commodore-1571-DSDD-GCR-C128-341K.scp")
    d71_fixture = Path("tests/fixtures/5.25inch/Commodore/Commodore-1571-DSDD-GCR-C128-341K.d71")
    out_path = tmp_path / "disk.d71"

    result = runner.invoke(
        app,
        [
            "convert",
            str(scp_fixture),
            "--layout",
            "commodore_gcr_1571_341k",
            "--encoding",
            "gcr",
            "--to",
            "d71",
            "--out",
            str(out_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert out_path.read_bytes() == d71_fixture.read_bytes()


def test_roundtrip_1571_d71_through_d71_matches() -> None:
    d71_fixture = Path("tests/fixtures/5.25inch/Commodore/Commodore-1571-DSDD-GCR-C128-341K.d71")

    result = runner.invoke(
        app,
        [
            "roundtrip",
            str(d71_fixture),
            "--layout",
            "commodore_gcr_1571_341k",
            "--encoding",
            "gcr",
            "--to",
            "d71",
            "--back-to",
            "d71",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Forward check: MATCH" in result.output
    assert "Round-trip check: MATCH" in result.output


def test_convert_amiga_adf_to_imd_can_be_read_back(tmp_path: Path) -> None:
    adf_fixture = Path("tests/fixtures/3.5inch/Commodore/Commodore-1010-DSDD-MFM-Amiga-880K.adf")
    out_path = tmp_path / "amiga.imd"

    result = runner.invoke(
        app,
        [
            "convert",
            str(adf_fixture),
            "--layout",
            "amiga_mfm_880k",
            "--to",
            "imd",
            "--out",
            str(out_path),
        ],
    )

    assert result.exit_code == 0, result.output
    tracks, geometry, _meta = load_imd_image(out_path)
    assert len(tracks) == 160
    assert geometry.heads == 2
    assert geometry.spt == 11
    assert geometry.sector_size == 512

    probe_result = runner.invoke(app, ["probe", str(out_path)])

    assert probe_result.exit_code == 0, probe_result.output
    assert "amiga_mfm_880k" in probe_result.output

    list_result = runner.invoke(app, ["extract", str(out_path), "--list"])

    assert list_result.exit_code == 0, list_result.output
    assert "C\t<DIR>" in list_result.output
    assert "Installer\t61640 bytes" in list_result.output


def test_roundtrip_flat_adf_through_raw_matches(tmp_path: Path) -> None:
    adf_fixture = Path("tests/fixtures/3.5inch/Commodore/Commodore-1010-DSDD-MFM-Amiga-880K.adf")
    report_path = tmp_path / "roundtrip.json"
    work_dir = tmp_path / "work"

    result = runner.invoke(
        app,
        [
            "roundtrip",
            str(adf_fixture),
            "--to",
            "raw",
            "--back-to",
            "adf",
            "--layout",
            "amiga_mfm_880k",
            "--work-dir",
            str(work_dir),
            "--json-out",
            str(report_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Forward check: MATCH" in result.output
    assert "Round-trip check: MATCH" in result.output
    report = json.loads(report_path.read_text())
    assert report["forward_match"] is True
    assert report["roundtrip_match"] is True
    assert report["layout"] == "amiga_mfm_880k"
    assert Path(report["first_path"]).exists()
    assert Path(report["final_path"]).exists()
