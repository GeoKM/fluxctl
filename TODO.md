# Fluxctl TODO

## Conversion Fidelity

- Complete the remaining copy-only manipulation paths for `.imd` and decoded
  `.scp` workflows, including clear GUI gating when a filesystem is writable
  but the current image container cannot preserve or rewrite the required
  sector format safely. Container-specific writes already exist for the
  supported `.d64`, `.d71`, `.d81`, and same-size `.adf` operations.
- Add logical IBM XDF unpacking/export/filesystem support. Fluxctl now
  recognises the physical XDF track layout used by OS/2 media, including the
  mixed 512/1024/2048/8192-byte data-track sectors. A follow-up should unpack
  those physical sectors into the logical 23x512-sector view expected by OS/2
  tooling, then enable FAT-style filesystem listing/export if the logical image
  contains a compatible filesystem.

## Filesystem Modelling

- Expand Apple II support beyond standard 35-track 16-sector media. Add DOS
  3.2 13-sector decoding, 40-track variants, protected/nonstandard WOZ track
  handling, and WOZ/NIB writing only after bitstream fidelity and round-trip
  expectations are defined. Current support reads WOZ1/WOZ2, NIB, PO, DO,
  140K IMG, and Apple 6-and-2 SCP captures, with ProDOS and DOS 3.3 extraction.
- Complete Apple II regression coverage with known-good, independently verified
  fixtures. Locate or create the following images, retaining the original
  preservation container and a trusted sector-image conversion where possible:
  - A WOZ1 35-track, 16-sector disk with an ordinary readable filesystem.
  - A standard 35-track `.nib` image and matching `.woz` or `.do`/`.po` image
    so decoded sectors can be compared byte-for-byte.
  - A real Apple DOS 3.3 disk in `.woz`, `.scp`, and `.do` form with several
    extractable file types and known file contents.
  - An Apple DOS 3.3 disk with multiple T/S-list sectors and a file large enough
    to validate chained T/S lists and selected-file map overlays.
  - A ProDOS disk with nested directories and known file contents.
  - ProDOS disks containing seedling, sapling, tree, and extended files so each
    storage type and data-fork path can be tested independently.
  - A standard 140K Apple `.img` image with a matching `.po` or `.do` reference
    to verify content-based sector-order detection.
  - A DOS 3.2 13-sector disk in WOZ and, if available, SCP/NIB form for future
    5-and-3 GCR decoder work.
  - A genuine 40-track Apple II image to define and test nonstandard geometry.
  - A copy-protected or otherwise nonstandard WOZ image with quarter-track or
    weak/fake-bit behavior, plus emulator confirmation of expected behavior.
  - A deliberately damaged Apple capture with known missing, weak, or bad
    sectors for QC, partial extraction, and error-reporting tests.
  Fluxctl can generate malformed/truncated WOZ, invalid CRC, missing-chunk, and
  missing-sector negative fixtures locally; these do not need to be sourced
  from original media.

- Extend Tandy CP/M write support beyond uniform CP/M 2.2 flat `.img` images.
  Tandy CP/M 2.2 and CP/M Plus now have modelled DPBs for extraction and
  selected-file overlays, but CP/M Plus mixed-sector mutation needs a writer
  that can preserve the 18x256 boot track plus 8x512 data tracks.
- Extend LDOS/TRSDOS 6 beyond root list/extract if needed. The current reader
  handles the Model III LDOS 5.3.1, Model 4 TRSDOS 6, and Model 4 LDOS 6.3.1
  fixtures with separate 32-byte FDE handling, but does not mutate those disks.
- Extend NEWDOS/80 support beyond the current root directory list/extract path
  when more fixtures are available. Follow-ups include validating alternate
  PDRIVE geometries, FXDE continuation records, and nonstandard directory sizes.
- Keep `docs/filesystem_capabilities.md` updated whenever filesystem listing,
  extraction/export, or copy-only mutation support changes.
- Add read-only Wang OIS system/software-disk support. The 315K HS32 geometry
  is now identified and mapped in the logical 16x256-byte view, but system
  disks such as ACMS80228 do not match the supported package catalog. Wang's
  documented standard VTOC uses 2 KiB blocks and begins at block 4 with FDAV,
  FDX1, FDX2, and FDR structures; ACMS80228 has executable/system data there.
  Obtain a known-good system-volume VTOC/module-table sample, prove file
  extents against physical sectors, then enable listing, selected-file
  overlays, and export. Do not infer file extents from embedded program
  strings. See `docs/wang_ois_system_disk_research.md`.
- Extend copy-only file and directory manipulation beyond the currently enabled
  writers:
  - CBM DOS 1581 `.d81`: continue expanding delete, replace, directory import,
    and directory creation beyond the currently supported root-oriented paths.
  - Amiga OFS/FFS `.adf`: add file/directory writers only after bitmap
    allocation, block checksums, file headers, and directory hash chains are
    modelled correctly.
  - CP/M and other filesystem plugins: enable writers only after each format's
    allocation structures can be updated safely.

## GUI And Manual Testing

- Manually verify Studio directory drill-down, multi-file export, recursive
  directory export, file hex viewing, and map-click sector hex viewing against
  the IBMPCDIR FAT12 fixtures in the 3.5-inch and 5.25-inch IBM fixture sets.
- Find or create known-good 1.15 MB IBM DOS 8-inch floppy images for fixtures.
  Use them to verify 8-inch FAT12 geometry, filesystem detection, sector counts,
  QC, directory listing, and file export against media known to be valid outside
  Fluxctl.
- Add an explicit Advanced "Directory Raw Dump" mode after defining
  per-filesystem behavior. FAT12 could dump raw 32-byte directory entries from
  directory clusters; CBM DOS, Amiga, CP/M, and other filesystems need their own
  directory record/block interpretation instead of overloading File Dump.
- Deepen selected-file disk-map overlay accuracy. Studio now highlights selected
  file sectors for FAT12, CBM DOS, 1581, Amiga OFS/FFS header/data/extension
  chains, and CP/M allocation blocks for modelled DPBs. CP/M overlays now also
  honour each extent's 128-byte record count rather than highlighting unused
  trailing allocation slots. Follow-ups:
  - Add more CP/M disk parameter blocks as formats are promoted from heuristic
    directory listing to extraction/export support.
  - Manually verify selected-file highlighting in Studio across physical,
    filesystem-logical, and BAM map modes for FAT12, CBM DOS, 1581, Amiga, and
    CP/M fixtures.
