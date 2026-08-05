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

## Deferred Work

Deferred GUI and manual-testing items have moved to the main project backlog in
`TODO.md`. Keep this file focused on confirmed fixture observations that explain
why future work exists.
