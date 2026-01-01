import hashlib
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from fluxctl.cli import app
from fluxctl.exporters import load_builtin_exporters
from fluxctl.exporters.imd import IMDExporter
from fluxctl.exporters.raw_img import RawIMGExporter
from fluxctl.filesystems import RawSectorImage, TrackSectorImage
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


def test_exporters_support_track_image(img_fixture: Path, layout_720k) -> None:
    load_builtin_exporters()
    image_obj = _build_track_image(img_fixture, layout_720k.layout_id, cylinders=1)

    assert RawIMGExporter().supports(image_obj)
    assert IMDExporter().supports(image_obj)


def test_imd_rejects_flat_image() -> None:
    assert not IMDExporter().supports(RawSectorImage(b""))
