"""IBM Displaywriter standard-label and container reader."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from ..exceptions import FilesystemError
from . import FileEntry, Filesystem, SectorImage, TrackSectorImage


@dataclass(slots=True)
class DisplaywriterLabel:
    """A track-0 IBM/DisplayWriter label or control-sector record."""

    sector_id: int
    kind: str
    name: str
    text: str
    data: bytes


class DisplaywriterFS(Filesystem):
    """Detect Displaywriter mixed-sector FM 8-inch format.

    Characteristics (per preservation reports):
    - Single-sided FM
    - Track 0: 26 sectors, 128 bytes
    - Tracks 1-76: 15 sectors, 256 bytes
    Track 0 carries IBM standard labels plus DisplayWriter control slots in
    EBCDIC. The user document catalogue inside the WPE container is not fully
    decoded yet, so this reader exposes WPE as a virtual container with raw
    payload/control exports rather than claiming each document as a file.
    """

    def __init__(self) -> None:
        self._probed = False
        self.image: SectorImage | None = None
        self._entries: List[FileEntry] = []
        self._labels: List[DisplaywriterLabel] = []
        self._volume_label = ""
        self._container_name = "WPE"
        self._container_block_len = 0
        self._container_payload: bytes = b""
        self._virtual_files: Dict[str, bytes] = {}

    def probe(self, image: SectorImage) -> bool:
        try:
            sectors: List[bytes] = []
            for idx, data in enumerate(image.iter_sectors()):
                sectors.append(data)
                if len(sectors) >= 26 + 15:  # enough for first two tracks if flattened T/S order
                    break
        except Exception:
            return False
        if not sectors:
            return False
        # Infer sector size pattern: first 26 sectors ~128 bytes, next 15 ~256 bytes.
        first = sectors[:26]
        rest = sectors[26:26 + 15]
        if len(first) < 26 or len(rest) < 10:
            return False
        if not all(112 <= len(s) <= 144 for s in first):
            return False
        if not all(240 <= len(s) <= 280 for s in rest):
            return False
        self.image = image
        self._labels = self._read_track0_labels(image)
        self._container_name = self._first_hdr1_name() or "WPE"
        self._container_block_len = self._first_hdr1_block_len()
        self._volume_label = self._read_volume_label()
        self._container_payload = self._read_container_payload(image)
        self._virtual_files = self._build_virtual_files()
        self._entries = [
            FileEntry(
                name=self._container_name,
                is_dir=True,
                size=len(self._container_payload),
                cluster_start=8,
                attributes=self._container_block_len,
            )
        ]
        self._probed = True
        return True

    def list_directory(self, path: str = "/") -> List[FileEntry]:
        normalised = self._normalise_path(path)
        if normalised == "/":
            return list(self._entries)
        if normalised == f"/{self._container_name}":
            return [
                FileEntry(
                    name=f"{self._container_name}.DATA",
                    is_dir=False,
                    size=len(self._container_payload),
                    cluster_start=1,
                    attributes=None,
                ),
                FileEntry(
                    name="_CONTROL",
                    is_dir=True,
                    size=0,
                    cluster_start=0,
                    attributes=len(self._labels),
                ),
            ]
        if normalised == f"/{self._container_name}/_CONTROL":
            return [
                FileEntry(
                    name=name.rsplit("/", 1)[-1],
                    is_dir=False,
                    size=len(data),
                    cluster_start=idx,
                    attributes=None,
                )
                for idx, (name, data) in enumerate(sorted(self._virtual_files.items()))
                if name.startswith(f"/{self._container_name}/_CONTROL/")
            ]
        raise FilesystemError("Displaywriter path not found")

    def extract_file(self, path: str) -> bytes:
        normalised = self._normalise_path(path)
        data = self._virtual_files.get(normalised)
        if data is not None:
            return data
        if normalised == f"/{self._container_name}/{self._container_name}.DATA":
            return self._container_payload
        raise FilesystemError(
            "Displaywriter document extraction not implemented; only raw WPE container/control exports are available"
        )

    def metadata(self) -> Dict[str, Any]:
        return {
            "filesystem": "displaywriter",
            "probed": self._probed,
            "entries": len(self._entries),
            "volume_label": self._volume_label,
            "container": self._container_name,
            "container_payload_bytes": len(self._container_payload),
            "track0_control_records": [
                {"sector": label.sector_id, "kind": label.kind, "name": label.name}
                for label in self._labels
            ],
            "document_catalog_decoded": False,
        }

    def _read_track0_labels(self, image: SectorImage) -> List[DisplaywriterLabel]:
        labels: List[DisplaywriterLabel] = []
        for sector_id in range(1, 27):
            data = self._read_track0_sector(image, sector_id)
            if data is None or not data.strip(b"\x00\x40"):
                continue
            text = data[:128].decode("cp037", errors="replace")
            prefix = text[:4].strip()
            if text.startswith("HDR1"):
                name = text[4:21].strip() or f"HDR1-S{sector_id:02d}"
                kind = "HDR1"
            elif text.startswith("VOL1"):
                name = text[4:10].strip() or f"VOL1-S{sector_id:02d}"
                kind = "VOL1"
            elif text.startswith("ERMAP"):
                name = "ERMAP"
                kind = "ERMAP"
            elif text.startswith("D"):
                name = f"D-S{sector_id:02d}"
                kind = "D"
            else:
                name = prefix or f"TRACK0-S{sector_id:02d}"
                kind = "TRACK0"
            labels.append(DisplaywriterLabel(sector_id=sector_id, kind=kind, name=name, text=text, data=data))
        return labels

    def _first_hdr1_name(self) -> str:
        for label in self._labels:
            if label.kind == "HDR1":
                return label.name.strip()
        return ""

    def _first_hdr1_block_len(self) -> int:
        for label in self._labels:
            if label.kind != "HDR1":
                continue
            try:
                return int(label.text[22:27].strip())
            except ValueError:
                return 0
        return 0

    def _read_volume_label(self) -> str:
        for label in self._labels:
            if label.kind == "VOL1":
                return label.name
        return ""

    def _read_container_payload(self, image: SectorImage) -> bytes:
        if isinstance(image, TrackSectorImage):
            payload: List[bytes] = []
            for track in sorted(image.tracks, key=lambda item: (item.track, item.head)):
                if track.track == 0 and track.head == 0:
                    continue
                for sector in sorted(track.sectors, key=lambda item: item.sector_id):
                    payload.append(sector.data)
            return b"".join(payload)

        sectors = list(image.iter_sectors())
        return b"".join(sectors[26:])

    def _build_virtual_files(self) -> Dict[str, bytes]:
        files: Dict[str, bytes] = {
            f"/{self._container_name}/{self._container_name}.DATA": self._container_payload
        }
        for label in self._labels:
            suffix = "txt" if label.kind in {"VOL1", "HDR1", "ERMAP"} else "raw"
            name = f"{label.sector_id:02d}-{label.kind}-{label.name}.{suffix}"
            path = f"/{self._container_name}/_CONTROL/{name}"
            if suffix == "txt":
                files[path] = label.text.rstrip().encode("utf-8") + b"\n"
            else:
                files[path] = label.data
        return files

    @staticmethod
    def _normalise_path(path: str) -> str:
        if not path or path == "/":
            return "/"
        return "/" + "/".join(part for part in path.split("/") if part)

    def _read_track0_sector(self, image: SectorImage, sector_id: int) -> bytes | None:
        if isinstance(image, TrackSectorImage):
            for track in image.tracks:
                if track.track != 0 or track.head != 0:
                    continue
                for sector in track.sectors:
                    if sector.sector_id == sector_id:
                        return sector.data
            return None
        try:
            # DisplayWriter label records begin at physical track 0 sector 8.
            return image.read_sector(sector_id - 1)
        except Exception:
            return None
