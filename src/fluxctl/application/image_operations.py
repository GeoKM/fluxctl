"""Stable image-operation boundary for CLI and Studio.

The implementations are still hosted by the legacy CLI module during the
incremental refactor.  Keeping this compatibility bridge here lets callers
depend on application operations rather than private CLI helpers while the
implementation is moved in smaller, testable steps.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional


def prepare_image(path: Path, layout_id: Optional[str], encoding: str):
    """Reconstruct an image container for reports and filesystem operations."""

    from .. import cli
    from ..apple2 import Apple2SectorImage
    from ..filesystems import RawSectorImage, TrackSectorImage
    from ..layouts.loader import ensure_layout_loaded

    layout_desc = ensure_layout_loaded(layout_id) if layout_id else None
    ext = path.suffix.lower()
    if ext in {".woz", ".po", ".do", ".nib"} or (
        ext in {".img", ".dsk"}
        and layout_desc is not None
        and layout_desc.layout_id.startswith("apple2_")
    ):
        tracks, _metadata = cli.load_apple2_tracks(path)
        return Apple2SectorImage(tracks, layout_desc)

    if layout_desc and ext == ".d81" and layout_desc.layout_id == "commodore_mfm_1581_800k":
        from ..exporters.d81 import d81_bytes_to_physical_tracks

        image = TrackSectorImage(
            d81_bytes_to_physical_tracks(path.read_bytes()),
            bytes_per_sector=layout_desc.sector_size,
        )
        image.layout = layout_desc
        _apply_layout_geometry(image, layout_desc)
        return image

    if layout_desc and ext not in {".scp", ".imd", ".dsk", ".dmk"}:
        track_data = cli._sectors_from_blob(
            layout_desc,
            path.read_bytes(),
            allow_pad=True,
            allow_prefix=ext in {".d64"},
        )
        if track_data:
            image = TrackSectorImage(track_data, bytes_per_sector=layout_desc.sector_size)
            image.layout = layout_desc
            _apply_layout_geometry(image, layout_desc)
            return image

    if ext == ".img":
        return RawSectorImage(path.read_bytes())
    if ext == ".scp":
        if layout_desc and layout_desc.layout_id.startswith("amiga_"):
            track_data = cli._decode_amiga_tracks(path, layout_desc)
            image = TrackSectorImage(track_data, bytes_per_sector=layout_desc.sector_size)
            image.layout = layout_desc
            _apply_layout_geometry(image, layout_desc)
            return image
        if layout_desc and layout_desc.layout_id in {
            "wang_ois_hs32_fm_315k",
            "wang_ois_hs32_fm_315k_128",
        }:
            from ..sector.reconstruct_wang import reconstruct_wang_track

            scp = cli.parse_scp(path)
            track_data = [
                reconstruct_wang_track(ts.revolutions, ts.track, ts.side, layout_desc.sectors_per_track)
                for ts in scp.tracks
                if ts.track < layout_desc.tracks and ts.side < layout_desc.sides and ts.revolutions
            ]
            if not any(track.sectors for track in track_data):
                image = RawSectorImage(path.read_bytes(), bytes_per_sector=layout_desc.sector_size)
                image.layout = layout_desc
                return image
            image = TrackSectorImage(track_data, bytes_per_sector=layout_desc.sector_size)
            image.layout = layout_desc
            _apply_layout_geometry(image, layout_desc)
            return image
        track_data = cli._decode_tracks(path, layout_id, encoding=encoding)
        if layout_desc and layout_desc.layout_id.startswith("apple2_"):
            return Apple2SectorImage(track_data, layout_desc)
        image = TrackSectorImage(
            track_data,
            bytes_per_sector=layout_desc.sector_size if layout_desc else None,
        )
        if layout_desc:
            image.layout = layout_desc
            _apply_layout_geometry(image, layout_desc)
        return image
    if ext == ".imd":
        tracks, geom, _meta = cli.load_imd_image(path)
        return cli._image_from_tracks(tracks, geom, layout_desc)
    if ext in {".dsk", ".dmk"}:
        tracks, geom, _meta = cli.load_trs80_image(path)
        return cli._image_from_tracks(tracks, geom, layout_desc)
    if layout_desc:
        track_data = cli._decode_tracks(path, layout_id, encoding=encoding)
        image = TrackSectorImage(track_data, bytes_per_sector=layout_desc.sector_size)
        image.layout = layout_desc
        return image
    return RawSectorImage(path.read_bytes())


def _apply_layout_geometry(image, layout) -> None:
    image.set_geometry(
        layout.sectors_per_track,
        layout.sides,
        int(layout.id_rules.get("sector_number_base", 1)),
    )


def _cli_module():
    # Lazy import avoids making the application layer depend on Typer during
    # module import and prevents a CLI/application import cycle.
    from .. import cli

    return cli


def get_decoder(encoding: str):
    from ..decoding import load_builtin_decoders
    from ..decoding.mfm import mfm_decoder
    from ..exceptions import FluxDecodeError
    from ..plugins import registry

    load_builtin_decoders()
    if encoding == "mfm":
        return mfm_decoder
    plugin = registry.encoding.get(encoding)
    if plugin:
        return plugin.entry
    raise FluxDecodeError(f"Unknown encoding '{encoding}'")


def probe_flat_image(path: Path):
    return _cli_module()._probe_flat_image(path)


def prefix_track_count_for_size(layout, data_len: int):
    if layout.sides != 1 or not layout.track_sectors or layout.sector_size <= 0:
        return None
    offset = 0
    for index, sectors in enumerate(layout.track_sectors):
        offset += sectors * layout.sector_size
        if offset == data_len:
            return index + 1
        if offset > data_len:
            return None
    return None


def track_in_range(range_expr: str, track: int) -> bool:
    if "-" in range_expr:
        start, end = range_expr.split("-", 1)
        try:
            return int(start) <= track <= int(end)
        except ValueError:
            return False
    try:
        return track == int(range_expr)
    except ValueError:
        return False


def maybe_hxc_hint(path: Path, hxcfe: Optional[Path]):
    return _cli_module()._maybe_hxc_hint(path, hxcfe)


def doctor_report(hxcfe: Optional[Path] = None) -> dict:
    return _cli_module()._doctor_report(hxcfe)
