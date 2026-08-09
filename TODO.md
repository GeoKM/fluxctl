# Fluxctl TODO

## Conversion Fidelity

- Add an SCP exporter so sector images can be written back into synthetic flux
  containers. This should support round-trip validation such as
  `fluxctl roundtrip disk.adf --to scp --back-to adf` and
  `fluxctl roundtrip disk.img --layout ibm_mfm_720k --to scp --back-to raw`,
  plus Commodore 1581 validation such as
  `fluxctl roundtrip disk.d81 --to scp --back-to raw`.
  The success criterion is decoded sector equality, not byte-identical flux
  timing compared with an original hardware capture.
- Extend copy-only manipulation beyond flat image containers. Define safe
  write-back/export paths for `.d64`, `.d71`, `.d81`, `.adf`, `.imd`, and
  decoded `.scp` workflows, including clear GUI gating when a filesystem is
  writable but the current image container cannot preserve or rewrite the
  required sector format safely.
- Add logical IBM XDF unpacking/export/filesystem support. Fluxctl now
  recognises the physical XDF track layout used by OS/2 media, including the
  mixed 512/1024/2048/8192-byte data-track sectors. A follow-up should unpack
  those physical sectors into the logical 23x512-sector view expected by OS/2
  tooling, then enable FAT-style filesystem listing/export if the logical image
  contains a compatible filesystem.

## Hardware Workflows

- Couple Fluxctl Studio to Greaseweazle for real-drive workflows. Add guided
  GUI actions for reading physical floppy disks into flux/images and writing
  supported images back to real disks, with drive selection, media type/layout
  confirmation, index/side handling, write-protect detection, pre-write
  warnings, post-read/post-write verification, and clear logs of the exact
  Greaseweazle commands and captured artifacts.

## Filesystem Modelling

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
- Extend copy-only file and directory manipulation beyond the currently enabled
  writers:
  - CBM DOS 1541/1571 `.d64/.d71`: add directory-safe replace/delete/import
    behavior where the format supports it; classic CBM DOS has no
    subdirectories, so do not expose directory creation/import there.
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
  file sectors for FAT12, CBM DOS, 1581, Amiga's current contiguous-file model,
  and CP/M allocation blocks for modelled DPBs. Follow-ups:
  - Add more CP/M disk parameter blocks as formats are promoted from heuristic
    directory listing to extraction/export support.
  - Replace the Amiga overlay's contiguous-sector assumption with real OFS/FFS
    file header, data block, extension block, checksum, and hash-chain traversal.
  - Manually verify selected-file highlighting in Studio across physical,
    filesystem-logical, and BAM map modes for FAT12, CBM DOS, 1581, Amiga, and
    CP/M fixtures.
