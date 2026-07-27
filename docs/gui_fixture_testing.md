# GUI Fixture Testing Notes

Manual GUI fixture testing is tracked here when it reveals behavior that should
shape later implementation work.

## Confirmed Fixes

- `Commodore-1581-DSDD-MFM-C64-800K.scp` now probes as `cbm_dos_1581` and lists
  root directory entries.
- `Commodore-1010-DSDD-MFM-Amiga-880K.scp` now probes as `amiga_ffs` and lists
  root directory entries.

## Deferred GUI Work

- The Files panel currently lists only the root directory. 1581 CBM DOS and
  AmigaDOS both support directories and subdirectories, so Fluxctl Studio needs
  filesystem navigation before workflows that operate on nested files can be
  complete.
- Expected direction: make directory rows actionable, maintain a current path
  breadcrumb, and call `list_directory(path)` on filesystem plugins that support
  nested paths.
