"""IBM XDF logical FAT12 view.

XDF stores a 512-byte logical FAT12 disk in mixed-size physical sectors.  The
first cylinder is special: it contains the boot sector, one complete FAT,
the root directory, and a small compatibility disk.  The second FAT and five
data sectors are not physically present and are represented as unavailable
logical sectors.  This reader uses the first FAT, as the IBM XDF driver did.
"""
from __future__ import annotations

from typing import Iterable

from ..exceptions import FilesystemError
from . import RawSectorImage, SectorImage, TrackSectorImage
from .fat12 import FAT12


class XDFImage:
    """Logical 512-byte sector view over a reconstructed XDF track image."""

    bytes_per_sector = 512

    def __init__(self, logical: list[bytes], physical: list[tuple[int, int, int] | None]):
        if len(logical) != 3680 or len(physical) != len(logical):
            raise FilesystemError("XDF logical image must contain 3,680 sectors")
        self._logical = logical
        self._physical = physical

    @classmethod
    def from_track_image(cls, image: TrackSectorImage) -> "XDFImage":
        lookup = image._sector_lookup
        logical: list[bytes] = []
        physical: list[tuple[int, int, int] | None] = []

        def add(track: int, head: int, sector_id: int) -> None:
            try:
                data = lookup[(track, head, sector_id)]
            except KeyError as exc:
                raise FilesystemError(
                    f"XDF sector T{track} H{head} S{sector_id} is missing"
                ) from exc
            if len(data) != 512:
                raise FilesystemError("XDF boot/FAT compatibility sectors must be 512 bytes")
            logical.append(data)
            physical.append((track, head, sector_id))

        def add_zero(count: int) -> None:
            logical.extend([bytes(512)] * count)
            physical.extend([None] * count)

        # The first cylinder's physical order is deliberately not the
        # logical FAT order.  The second FAT is absent; its sectors are
        # synthesized so FAT12's standard offsets remain valid.
        add(0, 0, 129)  # boot sector
        for sector_id in range(130, 140):  # FAT1 sectors 1..10
            add(0, 0, sector_id)
        add(0, 1, 129)  # FAT1 sector 11
        add_zero(11)  # absent FAT2
        for sector_id in range(130, 144):  # root directory
            add(0, 1, sector_id)
        for sector_id in range(144, 148):  # four real data sectors
            data = lookup[(0, 1, sector_id)]
            if len(data) != 512:
                raise FilesystemError("XDF compatibility data sectors must be 512 bytes")
            logical.append(data)
            physical.append((0, 1, sector_id))
        add_zero(5)  # FAT marks these logical clusters bad/unavailable

        for track in range(1, 80):
            for head in range(2):
                # The physical IDs are the authoritative ordering within a
                # decoded track; split each mixed-size sector into logical
                # 512-byte sectors while retaining its physical identity.
                sectors = sorted(
                    ((sid, data) for (t, h, sid), data in image._sector_lookup.items() if t == track and h == head),
                    key=lambda item: item[0],
                )
                if sum(len(data) for _, data in sectors) != 11776:
                    raise FilesystemError(f"XDF track {track} head {head} has incomplete mixed sectors")
                for sector_id, data in sectors:
                    for offset in range(0, len(data), 512):
                        logical.append(data[offset : offset + 512])
                        physical.append((track, head, sector_id))

        return cls(logical, physical)

    @property
    def total_sectors(self) -> int:
        return len(self._logical)

    def read_sector(self, lba: int, count: int = 1) -> bytes:
        if lba < 0 or lba + count > len(self._logical):
            raise FilesystemError("Requested XDF logical sector range exceeds image size")
        return b"".join(self._logical[lba : lba + count])

    def iter_sectors(self) -> Iterable[bytes]:
        return iter(self._logical)

    def physical_addresses_for_lba(self, lba: int) -> set[tuple[int, int, int]]:
        if lba < 0 or lba >= len(self._physical):
            raise FilesystemError("XDF logical sector is outside the image")
        address = self._physical[lba]
        return {address} if address is not None else set()


class XDF12(FAT12):
    """Read-only FAT12 access for IBM 1.88 MB XDF media."""

    def probe(self, image: SectorImage) -> bool:
        if not isinstance(image, (XDFImage, RawSectorImage)):
            return False
        boot = image.read_sector(0, 1)
        if boot[3:11] not in {b"IBM 20.0", b"XDFv1.3i"}:
            return False
        return super().probe(image)

    def file_sector_addresses(self, path: str) -> set[tuple[int, int, int]]:
        entry = self._entry_for_file(path)
        if isinstance(self.image, RawSectorImage):
            return super().file_sector_addresses(path)
        if not isinstance(self.image, XDFImage):
            raise FilesystemError("XDF image mapping is unavailable")
        addresses: set[tuple[int, int, int]] = set()
        for cluster in self._cluster_chain(entry.start_cluster):
            start_lba = self._cluster_to_lba(cluster)
            for sector_offset in range(self.sectors_per_cluster):
                addresses.update(self.image.physical_addresses_for_lba(start_lba + sector_offset))
        return addresses
