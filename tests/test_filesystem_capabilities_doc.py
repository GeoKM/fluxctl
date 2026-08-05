from pathlib import Path

from fluxctl import studio_services as services


DOC = Path("docs/filesystem_capabilities.md")


def test_filesystem_capabilities_documents_current_studio_media() -> None:
    text = DOC.read_text(encoding="utf-8")

    expected_rows = [
        "FAT12 flat `.img`",
        "FAT12 decoded `.scp`/`.imd`",
        "CBM DOS 1541 `.d64`",
        "CBM DOS 1571 `.d71`",
        "CBM DOS decoded `.scp`/`.imd`",
        "CBM DOS 1581 `.d81`",
        "CBM DOS 1581 decoded `.scp`/`.imd`",
        "Amiga OFS/FFS `.adf`",
        "Amiga decoded `.scp`/`.imd`",
        "CP/M variants",
        "Tandy/TRS-80 `.dsk`/`.dmk`/`.imd`/`.scp`",
        "DisplayWriter",
        "RT-11",
    ]

    for row in expected_rows:
        assert row in text

    for preset in services.blank_image_presets():
        assert preset.suffix in text
        if preset.filesystem == "fat12":
            assert "FAT12" in text
        elif preset.filesystem.startswith("cbm_dos"):
            assert "CBM DOS" in text
        elif preset.filesystem == "amiga_ofs":
            assert "AmigaDOS OFS" in text
        if preset.filesystem == "fat12":
            assert preset.label.split("FAT12 ", maxsplit=1)[1].split(" ", maxsplit=1)[0] in text

    assert "Root PRG import only" in text
    assert "Files and empty directories" in text
    assert "CP/M export is enabled only when Fluxctl has a modelled disk parameter block" in text
    assert "`.scp` and `.imd` write/manipulation actions are disabled" in text
