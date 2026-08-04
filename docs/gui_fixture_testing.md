# GUI Fixture Testing Notes

Manual GUI fixture testing is tracked here when it reveals behavior that should
shape later implementation work.

## Confirmed Fixes

- `Commodore-1581-DSDD-MFM-C64-800K.scp` now probes as `cbm_dos_1581` and lists
  root directory entries.
- `Commodore-1010-DSDD-MFM-Amiga-880K.scp` now probes as `amiga_ffs` and lists
  root directory entries.
- `IBM-Generic-DSDD-MFM-IBMPC-1200K` 8-inch variants probe as
  `ibm_mfm_8inch_1200k` but do not contain a valid FAT boot sector; filesystem
  should stay unknown rather than falling back to `fat12`.
- `IBM-Generic-DSDD-MFM-IBMPC-1200K-B.scp` probes as 8-inch IBM MFM/FAT12 and
  lists the same four root files as HxC. The physical capture is 78 cylinders,
  2 heads, 15 sectors/track; the FAT boot sector reports 2400 total sectors,
  so filesystem listing must tolerate the boot-sector total exceeding the
  captured physical sectors.
- `IBM-Generic-DSDD-MFM-IBMPC-1200K-C.scp` probes as 8-inch IBM MFM/FAT12 and
  represents an empty DOS disk. An empty Files panel is expected when the FAT
  root directory contains no entries.
- `IBM-6580-SSDD-FM-DisplayWriter-284K.scp` probes as
  `ibm_displaywriter_fm_284k`, single-sided FM. Track 0 has 26 128-byte
  sectors; tracks 1-76 have 15 256-byte sectors each. QC reports all 1,166
  sectors good, and the Files panel lists IBM standard-label `HDR1` entries
  such as `WPE`. Document extraction is not implemented yet because only the
  label directory is decoded.

## Deferred GUI Work

- Find or create IBM DOS/FAT12 fixtures that contain real subdirectories, then
  manually verify Studio directory drill-down, multi-file export, recursive
  directory export, file hex viewing, and map-click sector hex viewing against
  those images.
- Find or create known-good 1.15 MB IBM DOS 8-inch floppy images for fixtures.
  Use them to verify 8-inch FAT12 geometry, filesystem detection, sector counts,
  QC, directory listing, and file export against media known to be valid outside
  Fluxctl.
- Keep `docs/filesystem_capabilities.md` updated whenever filesystem listing,
  extraction/export, or copy-only mutation support changes.
- Add an explicit Advanced "Directory Raw Dump" mode after defining per-filesystem
  behavior. FAT12 could dump raw 32-byte directory entries from directory
  clusters; CBM DOS, Amiga, CP/M, and other filesystems need their own directory
  record/block interpretation instead of overloading File Dump.
- Deepen selected-file disk-map overlay accuracy. Studio now highlights selected
  file sectors for FAT12, CBM DOS, 1581, Amiga's current contiguous-file model,
  and CP/M allocation blocks for modelled DPBs. Amiga should move from
  contiguous block spans to full file-list block traversal.
  Follow-up items:
  - Add more CP/M disk parameter blocks as formats are promoted from heuristic
    directory listing to extraction/export support.
  - Replace the Amiga overlay's contiguous-sector assumption with real OFS/FFS
    file header, data block, extension block, checksum, and hash-chain traversal.
  - Manually verify selected-file highlighting in Studio across physical,
    filesystem-logical, and BAM map modes for FAT12, CBM DOS, 1581, Amiga, and
    CP/M fixtures.
- Convert the Advanced HEX display panel into a HEX in-place editor. It should
  allow byte edits in both Sector Dump and File Dump modes, validate changed
  bytes, and write changes back through the same copy-only safety model used by
  current manipulation actions rather than modifying the original image.
- Extend copy-only file and directory manipulation beyond the current FAT12
  flat `.img`, CBM DOS `.d64/.d71` root-file import support, and CBM DOS 1581
  `.d81` root-file import support. Add delete, replace, import directory, and
  create-directory writers for CBM DOS 1541/1571. For CBM DOS 1581, the real BAM
  allocation structures and root-level file import now exist; next add 1581
  delete, replace, import directory, and create-directory support. Amiga
  OFS/FFS `.adf` mutation has not been implemented yet, so add file/directory
  writers there after the block allocation and checksum/update rules are
  modeled. CP/M variants and any other filesystem plugin should follow once
  each format's allocation structures can be updated correctly.
- Extend copy-only manipulation beyond flat `.img` containers. Define safe
  write-back/export paths for `.d64`, `.d71`, `.d81`, `.adf`, `.imd`, and
  decoded `.scp` workflows, including clear GUI gating when a filesystem is
  writable but the current image container cannot preserve or rewrite the
  required sector format safely.
