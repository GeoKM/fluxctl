from types import SimpleNamespace

import pytest

from fluxctl.exceptions import FilesystemError
from fluxctl.filesystem_detection import detect_filesystem
from fluxctl.filesystems import TrackSectorImage, load_builtin_filesystems
from fluxctl.sector.models import Sector, TrackSectors


def _sector(track: int, head: int, sector_id: int, data: bytes) -> Sector:
    return Sector(track, head, sector_id, 1, data.ljust(256, b"\x40"), True, 1.0)


def _ebcdic(text: str) -> bytes:
    return text.encode("cp037")


def _seiko_dataset_image() -> TrackSectorImage:
    labels = [
        _sector(0, 1, sector_id, _ebcdic(f"DDR1 DATA{sector_id:02d}"))
        for sector_id in range(1, 27)
    ]
    records = []
    for sector_id in range(1, 27):
        if (sector_id - 1) % 3 == 0:
            group = (sector_id - 1) // 3
            data = _ebcdic(f"A{group:03d} DATASET {group:02d}                         000{{0000000{{")
        else:
            data = b""
        records.append(_sector(1, 0, sector_id, data))
    image = TrackSectorImage(
        [TrackSectors(0, 1, labels), TrackSectors(1, 0, records)], bytes_per_sector=256
    )
    image.layout = SimpleNamespace(layout_id="luxor_mfm_1000_program_994k")
    return image


def test_detects_seiko_ebcdic_indexed_dataset_without_claiming_cpm() -> None:
    load_builtin_filesystems()
    detection = detect_filesystem(_seiko_dataset_image(), path_name="unrelated.img")

    assert detection.primary == "seiko_8300_ebcdic_dataset"
    assert detection.plugin is not None
    assert detection.plugin.metadata()["record_group_sectors"] == 3
    assert len(detection.plugin.list_directory("/")) == 9
    with pytest.raises(FilesystemError, match="extraction is not implemented"):
        detection.plugin.extract_file("/A000 DATASET 00")
