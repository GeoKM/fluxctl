from __future__ import annotations

from pathlib import Path

from fluxctl.apple2 import decode_apple2_bitstream
from fluxctl.decoding.apple2 import Apple2GCRDecoder
from fluxctl.decoding.fm import FMDecoder
from fluxctl.decoding.gcr import GCRDecoder
from fluxctl.decoding.mfm import MFMDecoder
from fluxctl.exporters.scp import SCPExporter
from fluxctl.filesystems import TrackSectorImage
from fluxctl.layouts.loader import ensure_layout_loaded, load_builtin_layouts
from fluxctl.scp import parse_scp
from fluxctl.sector.models import Sector, TrackSectors
from fluxctl.models import LayoutDescriptor
from fluxctl.sector.reconstruct import reconstruct_track
from fluxctl.sector.reconstruct_fm import reconstruct_fm_track
from fluxctl.sector.reconstruct_gcr import reconstruct_gcr_track
from fluxctl.sector.reconstruct_amiga import reconstruct_amiga_track


def _image(*, bad_second: bool = False) -> TrackSectorImage:
    load_builtin_layouts()
    layout = ensure_layout_loaded("ibm_mfm_720k")
    sectors = [
        Sector(0, 0, 1, 2, bytes(range(256)) * 2, True, 1.0),
        Sector(0, 0, 2, 2, b"second".ljust(512, b"\x00"), not bad_second, 1.0, deleted=True),
    ]
    image = TrackSectorImage([TrackSectors(0, 0, sectors)], bytes_per_sector=512)
    image.layout = layout
    return image


def test_native_scp_is_deterministic_parseable_and_checksummed(tmp_path: Path) -> None:
    exporter = SCPExporter()
    image = _image()

    first = exporter.export(image)
    second = exporter.export(image)

    assert first == second
    assert first[:3] == b"SCP"
    assert int.from_bytes(first[12:16], "little") == sum(first[16:]) & 0xFFFFFFFF
    path = tmp_path / "synthetic.scp"
    path.write_bytes(first)
    parsed = parse_scp(path)
    assert len(parsed.tracks) == 1
    assert not parsed.warnings
    assert sum(parsed.tracks[0].revolutions[0].interval_ns) <= 200_000_000


def test_native_scp_mfm_roundtrip_preserves_ids_data_deleted_and_crc(tmp_path: Path) -> None:
    path = tmp_path / "synthetic.scp"
    path.write_bytes(SCPExporter().export(_image(bad_second=True)))
    parsed = parse_scp(path)
    bitstream = MFMDecoder().decode_revolution(parsed.tracks[0].revolutions[0])
    decoded = reconstruct_track(bitstream, cylinder=0, head=0, expected_sectors=2)

    assert [sector.sector_id for sector in decoded.sectors] == [1, 2]
    assert decoded.sectors[0].data == bytes(range(256)) * 2
    assert decoded.sectors[0].crc_ok
    assert decoded.sectors[1].data == b"second".ljust(512, b"\x00")
    assert decoded.sectors[1].deleted
    assert not decoded.sectors[1].crc_ok


def test_native_scp_requires_a_supported_layout() -> None:
    sector = Sector(0, 0, 1, 2, bytes(512), True, 1.0)
    image = TrackSectorImage([TrackSectors(0, 0, [sector])], bytes_per_sector=512)
    exporter = SCPExporter()

    assert not exporter.supports(image)


def _single_track_image(layout_id: str, sectors: list[Sector]) -> TrackSectorImage:
    layout = ensure_layout_loaded(layout_id)
    image = TrackSectorImage(
        [TrackSectors(0, 0, sectors)],
        bytes_per_sector=layout.sector_size,
    )
    image.layout = layout
    return image


def _first_revolution(tmp_path: Path, image: TrackSectorImage):
    path = tmp_path / "encoded.scp"
    path.write_bytes(SCPExporter().export(image))
    return parse_scp(path).tracks[0].revolutions[0]


def test_native_scp_fm_track_roundtrip(tmp_path: Path) -> None:
    layout = LayoutDescriptor(
        "layout.v1", "test_fm", "Test FM", "fm", 300, 1, 1, 1, 128,
        11, {"sector_number_base": 1}, {}, {},
    )
    data = bytes(range(128))
    sector = Sector(0, 0, 1, 0, data, True, 1.0)
    image = TrackSectorImage([TrackSectors(0, 0, [sector])], bytes_per_sector=128)
    image.layout = layout

    bitstream = FMDecoder().decode_revolution(_first_revolution(tmp_path, image))
    decoded = reconstruct_fm_track(bitstream, 0, 0, 1)

    assert [(item.sector_id, item.data, item.crc_ok) for item in decoded.sectors] == [(1, data, True)]


def test_native_scp_commodore_and_apple_gcr_tracks_roundtrip(tmp_path: Path) -> None:
    data = bytes(range(256))
    sector = Sector(0, 0, 0, 1, data, True, 1.0)

    commodore_rev = _first_revolution(
        tmp_path,
        _single_track_image("commodore_gcr_1541_170k", [sector]),
    )
    commodore = reconstruct_gcr_track(GCRDecoder(cell_ns=3250).decode_revolution(commodore_rev), 0, 0, 1)
    assert [(item.sector_id, item.data, item.crc_ok) for item in commodore.sectors] == [(0, data, True)]

    apple_rev = _first_revolution(
        tmp_path,
        _single_track_image("apple2_gcr_nofs_140_140k", [sector]),
    )
    apple_decoder = Apple2GCRDecoder()
    apple_decoder.set_track(0)
    apple = decode_apple2_bitstream(apple_decoder.decode_revolution(apple_rev).bits, 0)
    assert [(item.sector_id, item.data, item.crc_ok) for item in apple.sectors] == [(0, data, True)]


def test_native_scp_amiga_track_uses_self_contained_decoder(tmp_path: Path) -> None:
    sectors = [
        Sector(0, 0, sector_id, 2, bytes((sector_id,)) * 512, True, 1.0)
        for sector_id in range(11)
    ]
    rev = _first_revolution(tmp_path, _single_track_image("amiga_mfm_880k", sectors))
    decoded = reconstruct_amiga_track(MFMDecoder().decode_revolution(rev), 0, 0)

    assert [item.sector_id for item in decoded.sectors] == list(range(11))
    assert all(item.crc_ok for item in decoded.sectors)
    assert [item.data for item in decoded.sectors] == [item.data for item in sectors]
