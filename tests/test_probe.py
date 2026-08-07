import json
from pathlib import Path

from typer.testing import CliRunner

from fluxctl import cli, detection
from fluxctl.decoding import load_builtin_decoders
from fluxctl.imd import load_imd_image
from fluxctl.layouts.loader import load_builtin_layouts
from fluxctl.models import SCPImage, TrackFlux

FIXTURE = Path("tests/fixtures/5.25inch/Commodore/Commodore-1541-SSDD-GCR-C64-170K.scp")
FIXTURE_720K = Path("tests/fixtures/3.5inch/IBM/IBM-Generic-DSDD-MFM-IBMPC-720K.scp")
FIXTURE_1440K = Path("tests/fixtures/3.5inch/IBM/IBM-Generic-DSHD-MFM-IBMPC-1440K.scp")
FIXTURE_1200K = Path("tests/fixtures/5.25inch/IBM/IBM-Generic-DSHD-MFM-IBMPC-1200K.scp")
FIXTURE_CPM_340K = Path("tests/fixtures/5.25inch/Commodore/Commodore-1571-DSDD-GCR-C128CPM-340K.scp")
FIXTURE_CPM_170K = Path("tests/fixtures/5.25inch/Commodore/Commodore-1571-SSDD-GCR-C128CPM-170K.scp")
FIXTURE_8IN_500K = Path("tests/fixtures/8inch/DEC/DEC-RX02-DSDD-MFM-RT11-500K.scp")
FIXTURE_8IN_1200K = Path("tests/fixtures/8inch/IBM/IBM-Generic-DSDD-MFM-IBMPC-1200K.scp")
FIXTURE_8IN_FM_284K = Path("tests/fixtures/8inch/IBM/IBM-6580-SSDD-FM-DisplayWriter-284K.scp")
FIXTURE_AMIGA_880K = Path("tests/fixtures/3.5inch/Commodore/Commodore-1010-DSDD-MFM-Amiga-880K.scp")
FIXTURE_1541_CPM_170K = Path("tests/fixtures/5.25inch/Commodore/Commodore-1541-SSDD-GCR-C64CPM-170K.scp")
FIXTURE_D64 = Path("tests/fixtures/5.25inch/Commodore/Commodore-1541-SSDD-GCR-C64-170K.d64")
FIXTURE_DISK_DISECTOR_D64 = Path("tests/fixtures/5.25inch/Commodore/0008-DISC001-Disk_Disector-v5.d64")
FIXTURE_IMG_720K = Path("tests/fixtures/3.5inch/IBM/IBM-Generic-DSDD-MFM-IBMPC-720K.img")
FIXTURE_ADF = Path("tests/fixtures/3.5inch/Commodore/Commodore-1010-DSDD-MFM-Amiga-880K.adf")
FIXTURE_IMD_RX02 = Path("tests/fixtures/8inch/DEC/DEC-RX02-DSDD-MFM-RT11-500K.imd")
FIXTURE_IMD_8IN_1200K = Path("tests/fixtures/8inch/IBM/IBM-Generic-DSDD-MFM-IBMPC-1200K.imd")
FIXTURE_IMD_180K = Path("tests/fixtures/5.25inch/IBM/IBM-Generic-SSDD-MFM-IBMPC-180K.imd")
FIXTURE_IMD_DISPLAYWRITER = Path("tests/fixtures/8inch/IBM/IBM-6580-SSDD-FM-DisplayWriter-284K.imd")
FIXTURE_IMD_1440K = Path("tests/fixtures/3.5inch/IBM/IBM-Generic-DSHD-MFM-IBMPC-1440K.imd")
FIXTURE_RX02_IMG = Path("tests/fixtures/8inch/DEC/DEC-RX02-DSDD-MFM-RT11-500K.img")
FIXTURE_RX02_IMG_GENERIC = Path("tests/fixtures/8inch/DEC/DEC-RX02-DSDD-MFM.img")
FIXTURE_DISPLAYWRITER_IMG = Path("tests/fixtures/8inch/IBM/IBM-6580-SSDD-FM-DisplayWriter-284K.img")
FIXTURE_D64_CPM = Path("tests/fixtures/5.25inch/Commodore/Commodore-1541-SSDD-GCR-C64CPM-170K.d64")
FIXTURE_D71_CBM = Path("tests/fixtures/5.25inch/Commodore/Commodore-1571-DSDD-GCR-C128-341K.d71")
FIXTURE_D71_CPM = Path("tests/fixtures/5.25inch/Commodore/Commodore-1571-DSDD-MFM-C128CPM-340K.d71")
FIXTURE_ADF_REAL = Path("tests/fixtures/3.5inch/Commodore/Commodore-1010-DSDD-MFM-Amiga-880K.adf")
FIXTURE_AMIGA_IMG = FIXTURE_ADF_REAL
FIXTURE_1581_IMG = Path("tests/fixtures/3.5inch/Commodore/Commodore-1581-DSDD-MFM-C64-800K.img")
FIXTURE_1581_D81 = Path("tests/fixtures/3.5inch/Commodore/Commodore-1581-DSDD-MFM-C64-800K.d81")
FIXTURE_CPM_SRC1_IMG = Path("tests/fixtures/8inch/CPM/CPM-Generic-SSSD-FM-CPM22SRC1-256K.img")
FIXTURE_CPM_SRC2_IMG = Path("tests/fixtures/8inch/CPM/CPM-Generic-SSSD-FM-CPM22SRC2-256K.img")
FIXTURE_OSBORNE_CPM22_IMD = Path("tests/fixtures/5.25inch/CPM/Osbourne-CPM-SSDD-MFM-CPM22-200K.imd")
FIXTURE_OSBORNE_WSTR_IMD = Path("tests/fixtures/5.25inch/CPM/Osbourne-CPM-SSDD-MFM-WSTR-200K.imd")
FIXTURE_OSBORNE_CPM22_IMG = Path("tests/fixtures/5.25inch/CPM/Osbourne-CPM-SSDD-MFM-CPM22-200K.img")
FIXTURE_OSBORNE_WSTR_IMG = Path("tests/fixtures/5.25inch/CPM/Osbourne-CPM-SSDD-MFM-WSTR-200K.img")
FIXTURE_OSBORNE_CPM22_SCP = Path("tests/fixtures/5.25inch/CPM/Osbourne-CPM-SSDD-MFM-CPM22-200K.scp")
FIXTURE_OSBORNE_WSTR_SCP = Path("tests/fixtures/5.25inch/CPM/Osbourne-CPM-SSDD-MFM-WSTR-200K.scp")
FIXTURE_KAYPRO_CPM22_IMD = Path("tests/fixtures/5.25inch/CPM/KayproII-CPM-SSDD-MFM-CPM22-200K.imd")
FIXTURE_KAYPRO_WSTR_IMD = Path("tests/fixtures/5.25inch/CPM/KayproII-CPM-SSDD-MFM-WSTR-200K.imd")
FIXTURE_KAYPRO_CPM22_IMG = Path("tests/fixtures/5.25inch/CPM/KayproII-CPM-SSDD-MFM-CPM22-200K.img")
FIXTURE_KAYPRO_WSTR_IMG = Path("tests/fixtures/5.25inch/CPM/KayproII-CPM-SSDD-MFM-WSTR-200K.img")
FIXTURE_KAYPRO_CPM22_SCP = Path("tests/fixtures/5.25inch/CPM/KayproII-CPM-SSDD-MFM-CPM22-200K.scp")
FIXTURE_KAYPRO_WSTR_SCP = Path("tests/fixtures/5.25inch/CPM/KayproII-CPM-SSDD-MFM-WSTR-200K.scp")
FIXTURE_TANDY_MODEL3_TRSDOS_DSK = Path("tests/fixtures/5.25inch/TANDY/Tandy-Model3-SSDD-MFM-TRSDOS13-180K.dsk")
FIXTURE_TANDY_MODEL3_TRSDOS_IMD = Path("tests/fixtures/5.25inch/TANDY/Tandy-Model3-SSDD-MFM-TRSDOS13-180K.imd")
FIXTURE_TANDY_MODEL3_TRSDOS_SCP = Path("tests/fixtures/5.25inch/TANDY/Tandy-Model3-SSDD-MFM-TRSDOS13-180K.scp")
FIXTURE_TANDY_MODEL3_LDOS_DSK = Path("tests/fixtures/5.25inch/TANDY/Tandy-Model3-SSDD-MFM-LDOS531-180K.dsk")
FIXTURE_TANDY_MODEL3_LDOS_IMD = Path("tests/fixtures/5.25inch/TANDY/Tandy-Model3-SSDD-MFM-LDOS531-180K.imd")
FIXTURE_TANDY_MODEL3_LDOS_SCP = Path("tests/fixtures/5.25inch/TANDY/Tandy-Model3-SSDD-MFM-LDOS531-180K.scp")
FIXTURE_TANDY_MODEL3_NEWDOS80_DMK = Path("tests/fixtures/5.25inch/TANDY/Tandy-Model3-SSDD-MFM-NEWDOS80-180K.dmk")
FIXTURE_TANDY_MODEL3_NEWDOS80_IMD = Path("tests/fixtures/5.25inch/TANDY/Tandy-Model3-SSDD-MFM-NEWDOS80-180K.imd")
FIXTURE_TANDY_MODEL3_NEWDOS80_SCP = Path("tests/fixtures/5.25inch/TANDY/Tandy-Model3-SSDD-MFM-NEWDOS80-180K.scp")
FIXTURE_TANDY_MODEL4_TRSDOS_DSK = Path("tests/fixtures/5.25inch/TANDY/Tandy-Model4-SSDD-MFM-TRSDOS6-180K.dsk")
FIXTURE_TANDY_MODEL4_TRSDOS_IMD = Path("tests/fixtures/5.25inch/TANDY/Tandy-Model4-SSDD-MFM-TRSDOS6-180K.imd")
FIXTURE_TANDY_MODEL4_TRSDOS_SCP = Path("tests/fixtures/5.25inch/TANDY/Tandy-Model4-SSDD-MFM-TRSDOS6-180K.scp")
FIXTURE_TANDY_MODEL4_LDOS_DSK = Path("tests/fixtures/5.25inch/TANDY/Tandy-Model4-SSDD-MFM-LDOS631-180K.dsk")
FIXTURE_TANDY_MODEL4_LDOS_IMD = Path("tests/fixtures/5.25inch/TANDY/Tandy-Model4-SSDD-MFM-LDOS631-180K.imd")
FIXTURE_TANDY_MODEL4_LDOS_SCP = Path("tests/fixtures/5.25inch/TANDY/Tandy-Model4-SSDD-MFM-LDOS631-180K.scp")
FIXTURE_TANDY_CPM22_DSK = Path("tests/fixtures/5.25inch/TANDY/Tandy-Model4-SSDD-MFM-CPM22-180K.dsk")
FIXTURE_TANDY_CPM22_IMD = Path("tests/fixtures/5.25inch/TANDY/Tandy-Model4-SSDD-MFM-CPM22-180K.imd")
FIXTURE_TANDY_CPM22_SCP = Path("tests/fixtures/5.25inch/TANDY/Tandy-Model4-SSDD-MFM-CPM22-180K.scp")
FIXTURE_TANDY_CPMPLUS_DSK = Path("tests/fixtures/5.25inch/TANDY/Tandy-Model4-SSDD-MFM-CPMPlus-156K.dsk")
FIXTURE_TANDY_CPMPLUS_IMD = Path("tests/fixtures/5.25inch/TANDY/Tandy-Model4-SSDD-MFM-CPMPlus-156K.imd")
FIXTURE_TANDY_CPMPLUS_SCP = Path("tests/fixtures/5.25inch/TANDY/Tandy-Model4-SSDD-MFM-CPMPlus-156K.scp")


def test_probe_includes_gcr_candidates() -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["probe", str(FIXTURE)])
    assert result.exit_code == 0
    assert "commodore_gcr_1541_170k" in result.stdout
    assert "ibm_mfm_1440k" not in result.stdout


def test_probe_prefers_720k_layout() -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["probe", str(FIXTURE_720K)])
    assert result.exit_code == 0
    assert "ibm_mfm_720k" in result.stdout
    assert "ibm_mfm_360k" not in result.stdout


def test_probe_prefers_1440k_layout() -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["probe", str(FIXTURE_1440K)])
    assert result.exit_code == 0
    assert "ibm_mfm_1440k" in result.stdout


def test_probe_prefers_1440k_for_raw_hd_capture_without_sector_decode(monkeypatch) -> None:
    load_builtin_layouts()
    load_builtin_decoders()
    image = SCPImage(Path("capture.scp"), version=0, revolutions_per_track=0, timebase_ns=25.0, tracks=[])
    tracks = [TrackFlux(track=track, side=head) for track in range(81) for head in range(2)]
    monkeypatch.setattr(detection, "_tracks_with_flux", lambda _image: tracks)
    monkeypatch.setattr(detection, "_geometry_tracks_for_encoding", lambda _image, _encoding: tracks)
    monkeypatch.setattr(detection, "_estimate_bitstream_length", lambda *_args, **_kwargs: 124_726)
    monkeypatch.setattr(
        detection,
        "_estimate_geometry",
        lambda *_args, **_kwargs: {"track_samples": 6, "tracks_with_sectors": 0},
    )
    monkeypatch.setattr(detection, "_average_confidence", lambda *_args, **_kwargs: None)

    candidate = detection.detect_layout_any(image, Path("capture.scp"))

    assert candidate is not None
    assert candidate.layout.layout_id == "ibm_mfm_1440k"


def test_probe_prefers_amiga_for_raw_dd_capture_without_ibm_sector_decode(monkeypatch) -> None:
    load_builtin_layouts()
    load_builtin_decoders()
    image = SCPImage(Path("amiga.scp"), version=0, revolutions_per_track=0, timebase_ns=25.0, tracks=[])
    tracks = [TrackFlux(track=track, side=head) for track in range(80) for head in range(2)]
    monkeypatch.setattr(detection, "_tracks_with_flux", lambda _image: tracks)
    monkeypatch.setattr(detection, "_geometry_tracks_for_encoding", lambda _image, _encoding: tracks)
    monkeypatch.setattr(detection, "_estimate_bitstream_length", lambda *_args, **_kwargs: 101_000)
    monkeypatch.setattr(
        detection,
        "_estimate_geometry",
        lambda *_args, **_kwargs: {"track_samples": 6, "tracks_with_sectors": 0},
    )
    monkeypatch.setattr(detection, "_average_confidence", lambda *_args, **_kwargs: None)

    candidate = detection.detect_layout_any(image, Path("amiga.scp"))

    assert candidate is not None
    assert candidate.layout.layout_id == "amiga_mfm_880k"


def test_probe_prefers_1200k_layout() -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["probe", str(FIXTURE_1200K)])
    assert result.exit_code == 0
    assert "ibm_mfm_1200k" in result.stdout


def test_probe_prefers_commodore_cpm_layout() -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["probe", str(FIXTURE_CPM_340K)])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload[0]["encoding"] == "gcr"


def test_probe_uses_gcr_when_mfm_has_no_sectors() -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["probe", str(FIXTURE_CPM_170K)])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload[0]["encoding"] == "gcr"


def test_probe_prefers_8inch_500k_layout() -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["probe", str(FIXTURE_8IN_500K)])
    assert result.exit_code == 0
    assert "generic_mfm_8inch_500k" in result.stdout


def test_probe_prefers_8inch_1200k_layout() -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["probe", str(FIXTURE_8IN_1200K)])
    assert result.exit_code == 0
    assert "ibm_mfm_8inch_1200k" in result.stdout


def test_probe_prefers_8inch_fm_284k_layout() -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["probe", str(FIXTURE_8IN_FM_284K)])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload[0]["layout_id"] == "ibm_displaywriter_fm_284k"
    assert payload[0]["filesystem"] == "displaywriter"


def test_probe_prefers_amiga_880k_over_hd() -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["probe", str(FIXTURE_AMIGA_880K)])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload[0]["layout_id"] == "amiga_mfm_880k"
    assert payload[0]["filesystem"] == "amiga_ffs"


def test_probe_prefers_commodore_cpm_over_apple_gcr() -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["probe", str(FIXTURE_1541_CPM_170K)])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload[0]["layout_id"] == "commodore_gcr_1541_170k"
    assert payload[0]["filesystem"] == "c64_cpm_2_2"
    assert "apple2_gcr_nofs_140_140k" not in result.stdout


def test_probe_supports_flat_d64_images() -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["probe", str(FIXTURE_D64)])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload[0]["layout_id"] == "commodore_gcr_1541_170k"
    assert payload[0]["encoding"] == "gcr"
    assert payload[0]["filesystem"] == "cbm_dos"


def test_probe_supports_35_track_d64_as_varied_sector_cbm_dos() -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["probe", str(FIXTURE_DISK_DISECTOR_D64)])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload[0]["layout_id"] == "commodore_gcr_1541_170k"
    assert payload[0]["encoding"] == "gcr"
    assert payload[0]["filesystem"] == "cbm_dos"


def test_probe_supports_flat_img_images() -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["probe", str(FIXTURE_IMG_720K)])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload[0]["layout_id"] == "ibm_mfm_720k"
    assert payload[0]["encoding"] == "mfm"


def test_probe_supports_flat_adf_images() -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["probe", str(FIXTURE_ADF)])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload[0]["layout_id"] == "amiga_mfm_880k"
    assert payload[0]["filesystem"] is not None


def test_probe_supports_imd_rx02() -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["probe", str(FIXTURE_IMD_RX02)])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload[0]["layout_id"] == "generic_mfm_8inch_500k"
    assert payload[0]["filesystem"] == "rt11"


def test_probe_supports_imd_ibm_1200k() -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["probe", str(FIXTURE_IMD_8IN_1200K)])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload[0]["layout_id"] == "ibm_mfm_8inch_1200k"
    assert payload[0]["filesystem"] is None


def test_probe_supports_imd_ibm_180k_with_fat() -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["probe", str(FIXTURE_IMD_180K)])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload[0]["layout_id"] == "ibm_mfm_180k"
    assert payload[0]["filesystem"] == "fat12"


def test_probe_supports_imd_displaywriter() -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["probe", str(FIXTURE_IMD_DISPLAYWRITER)])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload[0]["layout_id"] == "ibm_displaywriter_fm_284k"
    assert payload[0]["filesystem"] == "displaywriter"


def test_probe_supports_imd_1440k_fat() -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["probe", str(FIXTURE_IMD_1440K)])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload[0]["layout_id"] == "ibm_mfm_1440k"
    assert payload[0]["filesystem"] == "fat12"


def test_probe_supports_rx02_img_rt11() -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["probe", str(FIXTURE_RX02_IMG)])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload[0]["filesystem"] == "rt11"
    assert payload[0]["layout_id"] in {"dec_dec_rx02_rx02_250k", "generic_mfm_8inch_500k"}


def test_probe_supports_rx02_img_generic() -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["probe", str(FIXTURE_RX02_IMG_GENERIC)])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload[0]["layout_id"] in {"dec_dec_rx02_rx02_250k", "generic_mfm_8inch_500k"}


def test_probe_supports_8inch_cpm_source_images() -> None:
    runner = CliRunner()
    for fixture in (FIXTURE_CPM_SRC1_IMG, FIXTURE_CPM_SRC2_IMG):
        result = runner.invoke(cli.app, ["probe", str(fixture)])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload[0]["layout_id"] == "generic_fm_8inch_cpm_256k"
        assert payload[0]["encoding"] == "fm"
        assert payload[0]["filesystem"] == "cpm"


def test_probe_supports_osborne_5inch_cpm_images() -> None:
    runner = CliRunner()
    for fixture in (
        FIXTURE_OSBORNE_CPM22_IMD,
        FIXTURE_OSBORNE_WSTR_IMD,
        FIXTURE_OSBORNE_CPM22_IMG,
        FIXTURE_OSBORNE_WSTR_IMG,
        FIXTURE_OSBORNE_CPM22_SCP,
        FIXTURE_OSBORNE_WSTR_SCP,
    ):
        result = runner.invoke(cli.app, ["probe", str(fixture)])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload[0]["layout_id"] == "osborne_mfm_ssdd_200k"
        assert payload[0]["encoding"] == "mfm"
        assert payload[0]["filesystem"] == "cpm"


def test_probe_supports_kaypro_5inch_cpm_images() -> None:
    runner = CliRunner()
    for fixture in (
        FIXTURE_KAYPRO_CPM22_IMD,
        FIXTURE_KAYPRO_WSTR_IMD,
        FIXTURE_KAYPRO_CPM22_IMG,
        FIXTURE_KAYPRO_WSTR_IMG,
        FIXTURE_KAYPRO_CPM22_SCP,
        FIXTURE_KAYPRO_WSTR_SCP,
    ):
        result = runner.invoke(cli.app, ["probe", str(fixture)])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload[0]["layout_id"] == "kaypro_mfm_ssdd_40_200k"
        assert payload[0]["encoding"] == "mfm"
        assert payload[0]["filesystem"] == "cpm"


def test_probe_supports_tandy_trsdos_images_without_false_filesystem() -> None:
    runner = CliRunner()
    expectations = {
        FIXTURE_TANDY_MODEL3_TRSDOS_DSK: ("tandy_mfm_ssdd_180k", "trsdos_1_3"),
        FIXTURE_TANDY_MODEL3_TRSDOS_IMD: ("tandy_mfm_ssdd_180k", "trsdos_1_3"),
        FIXTURE_TANDY_MODEL3_TRSDOS_SCP: ("tandy_mfm_ssdd_180k", "trsdos_1_3"),
        FIXTURE_TANDY_MODEL3_LDOS_DSK: ("tandy_mfm_ssdd_180k_s0", "ldos_trsdos6"),
        FIXTURE_TANDY_MODEL3_LDOS_IMD: ("tandy_mfm_ssdd_180k_s0", "ldos_trsdos6"),
        FIXTURE_TANDY_MODEL3_LDOS_SCP: ("tandy_mfm_ssdd_180k_s0", "ldos_trsdos6"),
        FIXTURE_TANDY_MODEL3_NEWDOS80_DMK: ("tandy_mfm_ssdd_180k_s0", "newdos80"),
        FIXTURE_TANDY_MODEL3_NEWDOS80_IMD: ("tandy_mfm_ssdd_180k_s0", "newdos80"),
        FIXTURE_TANDY_MODEL3_NEWDOS80_SCP: ("tandy_mfm_ssdd_180k_s0", "newdos80"),
        FIXTURE_TANDY_MODEL4_TRSDOS_DSK: ("tandy_mfm_ssdd_180k_s0", "ldos_trsdos6"),
        FIXTURE_TANDY_MODEL4_TRSDOS_IMD: ("tandy_mfm_ssdd_180k_s0", "ldos_trsdos6"),
        FIXTURE_TANDY_MODEL4_TRSDOS_SCP: ("tandy_mfm_ssdd_180k_s0", "ldos_trsdos6"),
        FIXTURE_TANDY_MODEL4_LDOS_DSK: ("tandy_mfm_ssdd_180k_s0", "ldos_trsdos6"),
        FIXTURE_TANDY_MODEL4_LDOS_IMD: ("tandy_mfm_ssdd_180k_s0", "ldos_trsdos6"),
        FIXTURE_TANDY_MODEL4_LDOS_SCP: ("tandy_mfm_ssdd_180k_s0", "ldos_trsdos6"),
    }
    for fixture, (layout_id, filesystem) in expectations.items():
        result = runner.invoke(cli.app, ["probe", str(fixture)])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload[0]["layout_id"] == layout_id
        assert payload[0]["encoding"] == "mfm"
        assert payload[0]["filesystem"] == filesystem


def test_probe_supports_tandy_cpm_images() -> None:
    runner = CliRunner()
    expectations = {
        FIXTURE_TANDY_CPM22_DSK: "tandy_mfm_ssdd_180k",
        FIXTURE_TANDY_CPM22_IMD: "tandy_mfm_ssdd_180k",
        FIXTURE_TANDY_CPM22_SCP: "tandy_mfm_ssdd_180k",
        FIXTURE_TANDY_CPMPLUS_DSK: "tandy_mfm_cpmplus_156k",
        FIXTURE_TANDY_CPMPLUS_IMD: "tandy_mfm_cpmplus_156k",
        FIXTURE_TANDY_CPMPLUS_SCP: "tandy_mfm_cpmplus_156k",
    }
    for fixture, layout_id in expectations.items():
        result = runner.invoke(cli.app, ["probe", str(fixture)])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload[0]["layout_id"] == layout_id
        assert payload[0]["encoding"] == "mfm"
        assert payload[0]["filesystem"] == "cpm"


def test_tandy_cpm_plus_imd_preserves_mixed_track_sector_counts() -> None:
    tracks, geometry, _meta = load_imd_image(FIXTURE_TANDY_CPMPLUS_IMD)

    assert geometry.tracks == 40
    assert [len(track.sectors) for track in tracks[:3]] == [18, 8, 8]
    assert sum(len(track.sectors) for track in tracks) == 330
    assert sum(track.missing for track in tracks) == 0


def test_probe_supports_displaywriter_img() -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["probe", str(FIXTURE_DISPLAYWRITER_IMG)])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload[0]["layout_id"] == "ibm_displaywriter_fm_284k"
    assert payload[0]["filesystem"] == "displaywriter"


def test_probe_supports_d64_cpm_filesystem() -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["probe", str(FIXTURE_D64_CPM)])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload[0]["layout_id"] == "commodore_gcr_1541_170k"
    assert payload[0]["filesystem"] == "c64_cpm_2_2"


def test_probe_supports_d71_cbm_filesystem() -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["probe", str(FIXTURE_D71_CBM)])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload[0]["layout_id"] == "commodore_gcr_1571_341k"
    assert payload[0]["filesystem"] == "cbm_dos_1571"
    assert "filesystem_region=head0:cbm_dos_1541_compatible" in payload[0]["evidence"]
    assert "filesystem_region=head1:cbm_dos_1571_extended_side" in payload[0]["evidence"]


def test_probe_supports_d71_cpm_filesystem() -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["probe", str(FIXTURE_D71_CPM)])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload[0]["layout_id"] == "commodore_gcr_1571_341k"
    assert payload[0]["filesystem"] == "c128_cpm_3_0"
    assert "filesystem_region=disk:c128_cpm_3_0" in payload[0]["evidence"]


def test_probe_supports_adf_amiga_filesystem() -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["probe", str(FIXTURE_ADF_REAL)])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload[0]["layout_id"] == "amiga_mfm_880k"
    assert payload[0]["filesystem"] == "amiga_ffs"


def test_probe_supports_amiga_img() -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["probe", str(FIXTURE_AMIGA_IMG)])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload[0]["layout_id"] == "amiga_mfm_880k"
    assert payload[0]["filesystem"] == "amiga_ffs"


def test_probe_supports_1581_images_as_cbm_dos() -> None:
    runner = CliRunner()
    for fixture in (FIXTURE_1581_IMG, FIXTURE_1581_D81):
        result = runner.invoke(cli.app, ["probe", str(fixture)])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload[0]["layout_id"] == "commodore_mfm_1581_800k"
        assert payload[0]["filesystem"] == "cbm_dos_1581"
