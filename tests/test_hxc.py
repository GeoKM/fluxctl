"""Unit tests for the HxC CLI integration."""

from fluxctl.external.hxc import HxcMetadata, parse_hxc_infos_output


def test_parse_hxc_infos_output_populates_geometry() -> None:
    sample_output = """
HxC Floppy Emulator : Floppy image file converter v2.16.14.1
Input file : tests/fixtures/8inch/DEC/DEC-RX02-DSDD-MFM.imd
---------------------------------------------------------------------------
-                        File informations                                -
---------------------------------------------------------------------------
File: tests/fixtures/8inch/DEC/DEC-RX02-DSDD-MFM.imd
Checking tests/fixtures/8inch/DEC/DEC-RX02-DSDD-MFM.imd
File loader found : IMD_IMG (ImageDisk IMD file Loader)
Loading tests/fixtures/8inch/DEC/DEC-RX02-DSDD-MFM.imd
file loader found!

File type : IMD_IMG - ImageDisk IMD file Loader
Floppy interface mode : GENERIC_SHUGART_DD_FLOPPYMODE - Shugart Interface
Number of Track : 77
Number of Side : 2
Total Size : 512384 Bytes, Number of sectors : 4003
"""

    metadata = parse_hxc_infos_output(sample_output)
    assert metadata.loader == "IMD_IMG"
    assert metadata.interface == "GENERIC_SHUGART_DD_FLOPPYMODE"
    assert metadata.tracks == 77
    assert metadata.sides == 2
    assert metadata.total_size == 512384
    assert metadata.total_sectors == 4003
    hint = metadata.to_layout_hint()
    assert hint.loader == "IMD_IMG"
    assert hint.tracks == 77


def test_parse_hxc_infos_output_records_errors() -> None:
    sample_output = "No loader support the file tests/fixtures/3.5inch/Commodore/Commodore-1010-DSDD-MFM-Amiga-880K.img !"

    metadata = parse_hxc_infos_output(sample_output)
    assert metadata.tracks is None
    assert metadata.loader is None
    assert metadata.error is not None
