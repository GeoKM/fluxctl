# Commodore CP/M Media Notes

These notes capture the Commodore 128 behavior that affects filesystem detection.
They are derived from the local Commodore 128 Programmer's Reference Guide PDF
in this directory and are intended to guide tests and heuristics, not replace
on-disk probing.

## Detection Policy

- Do not infer CBM DOS or CP/M from fixture filenames. Fixture names are hints
  for humans only and may be wrong.
- Prefer filesystem structures:
  - CBM DOS requires a valid BAM/directory probe.
  - CP/M requires directory entries with valid user numbers, 8.3 names, and
    enough local directory-entry density to avoid matching program text.
- Layouts may provide a low-confidence family hint only when no filesystem
  structure is present.

## C128 CP/M And 1571

- C128 CP/M 3.0 normally targets the 1571 fast disk drive.
- C128 CP/M can also be used with a 1541, but then only single-sided GCR media
  are available.
- The C128 CP/M BIOS has separate 1541 GCR routines and 1571 setup routines
  that can handle MFM or GCR.
- The BIOS can query media status and reports whether the current mode is GCR
  or MFM. For MFM it also reports sector size.
- The manual's MFM table covers multiple third-party CP/M layouts, with sector
  sizes of 256, 512, or 1024 bytes and different sector numbering rules.

## Current fluxctl Implications

- `commodore_gcr_1541_cpm_170k` should identify C64/1541 CP/M 2.2 only when a
  CP/M directory probes successfully.
- `commodore_gcr_1571_341k` can represent plain 1571 CBM DOS or a D71-shaped
  CP/M logical image. The detector must inspect the BAM/directory and CP/M
  directory entries to distinguish them.
- `commodore_mfm_1571_cpm_170k` and `commodore_mfm_1571_cpm_340k` are native
  1571 CP/M layouts and should identify C128 CP/M 3.0 when CP/M directory
  entries are present.
- Future MFM CP/M layout work should model the exact sector size, sector count,
  reserved tracks, starting position, and sector numbering from the target
  format rather than folding all CP/M disks into one geometry.
