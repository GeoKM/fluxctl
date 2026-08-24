from fluxctl.filesystems import RawSectorImage
from fluxctl.filesystems.wang_vtoc import inspect_wang_vtoc


def _image_with_vtoc(*kinds: str) -> RawSectorImage:
    data = bytearray(16 * 8 * 256)
    for relative, kind in enumerate(kinds[:4]):
        offset = (4 + relative) * 2048 + 32
        data[offset : offset + 4] = kind.encode("ascii")
    return RawSectorImage(bytes(data), bytes_per_sector=256)


def test_inspect_wang_vtoc_requires_all_control_block_ids() -> None:
    report = inspect_wang_vtoc(_image_with_vtoc("FDAV", "FDX1"))

    assert report.recognised is False
    assert report.missing_kinds == ("FDX2", "FDR1")
    assert report.to_dict()["file_extents_modelled"] is False


def test_inspect_wang_vtoc_reports_verified_control_block_locations() -> None:
    report = inspect_wang_vtoc(_image_with_vtoc("FDAV", "FDX1", "FDX2", "FDR1"))

    assert report.recognised is True
    assert [(item.block, item.kind, item.offset) for item in report.control_blocks] == [
        (4, "FDAV", 32),
        (5, "FDX1", 32),
        (6, "FDX2", 32),
        (7, "FDR1", 32),
    ]


def test_inspect_wang_vtoc_does_not_treat_utility_text_as_a_vtoc() -> None:
    image = RawSectorImage(
        b"CREATE VTOC ENTRY" + bytes(16 * 8 * 256 - len("CREATE VTOC ENTRY")),
        bytes_per_sector=256,
    )

    report = inspect_wang_vtoc(image)

    assert report.recognised is False
    assert not report.control_blocks
