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

## Filesystem Modelling

- Model exact Tandy/TRS-80 CP/M disk parameter blocks and skew/allocation rules
  for the newly supported Model 4 CP/M 2.2 and CP/M Plus media. Current support
  can identify the physical layouts and list CP/M directory entries from
  `.dsk`/`.dmk`, `.imd`, and `.scp`, but file extraction and mutation should stay
  disabled until the allocation mapping is verified against known-good files.
- Add an LS-DOS/TRSDOS 6 filesystem plugin for the Model III LDOS 5.3.1,
  Model 4 TRSDOS 6, and Model 4 LDOS 6.3.1 fixtures. These should remain
  separate from the existing TRSDOS 1.3 reader because the directory/system
  structures differ.
- Extend NEWDOS/80 support beyond the current root directory list/extract path
  when more fixtures are available. Follow-ups include validating alternate
  PDRIVE geometries, FXDE continuation records, and nonstandard directory sizes.
