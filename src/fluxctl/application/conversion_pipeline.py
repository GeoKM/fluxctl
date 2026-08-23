"""Application-owned conversion preparation pipeline."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..apple2 import Apple2SectorImage, load_apple2_tracks
from ..decoding import load_builtin_decoders
from ..detection import detect_encoding, detect_layout
from ..exceptions import ExportError, FluxDecodeError
from ..exporters import load_builtin_exporters
from ..filesystems import RawSectorImage, TrackSectorImage, load_builtin_filesystems
from ..filesystem_detection import detect_filesystem
from ..imd import load_imd_image
from ..layouts.loader import ensure_layout_loaded, load_builtin_layouts
from ..plugins import registry
from ..scp import parse_scp
from ..trs80 import load_trs80_image
from .conversion_planner import ConversionContext, ConversionPlan, plan_conversion
from .decode_operations import decode_tracks, image_from_tracks
from .image_operations import prepare_image


@dataclass(slots=True)
class ConvertPayload:
    payload: bytes
    layout: object
    encoding: str
    track_data: Optional[list]
    exporter_name: str
    exporter_version: str
    exporter_metadata: dict
    conversion_plan: ConversionPlan

    @property
    def layout_id(self) -> str:
        return self.layout.layout_id if self.layout else ""


def prepare_convert_payload(path: Path, to: str, layout: Optional[str], encoding: str) -> ConvertPayload:
    load_builtin_decoders(); load_builtin_layouts(); load_builtin_exporters()
    layout_desc = ensure_layout_loaded(layout) if layout else None
    decoder_used = layout_desc.encoding if layout_desc else encoding
    if layout_desc is None and path.suffix.lower() == ".d81":
        layout_desc = ensure_layout_loaded("commodore_mfm_1581_800k"); decoder_used = layout_desc.encoding
    track_data = None
    track_nibbles = []
    ext = path.suffix.lower()
    if ext == ".scp":
        if layout_desc is None:
            scp = parse_scp(path); detected_encoding = detect_encoding(scp)
            if detected_encoding is None:
                raise FluxDecodeError("Unable to auto-detect SCP encoding; pass --layout and --encoding")
            detected_layout = detect_layout(scp, detected_encoding.encoding)
            if detected_layout is None:
                raise FluxDecodeError("Unable to auto-detect SCP layout; pass --layout explicitly")
            layout_desc = detected_layout.layout; decoder_used = layout_desc.encoding
        if layout_desc.layout_id.startswith("amiga_"):
            from ..sector.reconstruct_amiga import reconstruct_amiga_greaseweazle, reconstruct_amiga_with_pll
            scp = parse_scp(path); track_data = []
            for ts in scp.tracks:
                if ts.track >= layout_desc.tracks or ts.side >= layout_desc.sides or not ts.revolutions: continue
                candidate = reconstruct_amiga_greaseweazle(ts.revolutions, ts.track, ts.side, timebase_ns=scp.timebase_ns)
                track_data.append(candidate or reconstruct_amiga_with_pll(ts.revolutions, ts.track, ts.side, timebase_ns=scp.timebase_ns))
        else:
            decoded = decode_tracks(path, layout_desc.layout_id, encoding=decoder_used, capture_nibbles=to == "g64")
            track_data, track_nibbles = decoded if isinstance(decoded, tuple) else (decoded, [])
        image_obj = Apple2SectorImage(track_data, layout_desc) if layout_desc.layout_id.startswith("apple2_") else TrackSectorImage(track_data, bytes_per_sector=layout_desc.sector_size)
        if track_nibbles: image_obj.tracks_nibbles = track_nibbles
        image_obj.layout = layout_desc
        image_obj.set_geometry(layout_desc.sectors_per_track, layout_desc.sides, int(layout_desc.id_rules.get("sector_number_base", 1)))
    elif ext in {".woz", ".po", ".do", ".nib"}:
        layout_desc = layout_desc or ensure_layout_loaded("apple2_gcr_nofs_140_140k"); decoder_used = layout_desc.encoding
        track_data, _ = load_apple2_tracks(path); image_obj = Apple2SectorImage(track_data, layout_desc)
    elif ext == ".imd":
        track_data, geometry, _ = load_imd_image(path); image_obj = image_from_tracks(track_data, geometry, layout_desc)
    elif ext == ".dsk" and layout_desc and layout_desc.layout_id.startswith("apple2_"):
        track_data, _ = load_apple2_tracks(path); image_obj = Apple2SectorImage(track_data, layout_desc)
    elif ext in {".dsk", ".dmk"}:
        track_data, geometry, _ = load_trs80_image(path); image_obj = image_from_tracks(track_data, geometry, layout_desc)
    elif layout_desc:
        image_obj = prepare_image(path, layout_desc.layout_id, decoder_used)
        track_data = getattr(image_obj, "tracks", None)
    else:
        image_obj = RawSectorImage(path.read_bytes())
    plugin = registry.exporter.get(to)
    if plugin is None: raise ExportError(f"Unsupported exporter '{to}'")
    filesystem = ""
    try:
        load_builtin_filesystems(); filesystem = detect_filesystem(image_obj).primary or ""
    except Exception: pass
    plan = plan_conversion(ConversionContext.from_image(image_obj, source_kind=ext.lstrip("."), layout=layout_desc, encoding=decoder_used, filesystem=filesystem), to)
    if not plan.allowed or not plugin.entry.supports(image_obj):
        raise ExportError(plan.reason or f"Exporter '{to}' does not support this image type")
    return ConvertPayload(plugin.entry.export(image_obj), layout_desc, decoder_used, track_data, plugin.name, plugin.version, plugin.entry.metadata(), plan)
