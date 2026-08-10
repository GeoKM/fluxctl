# Filesystem Capabilities

Fluxctl separates filesystem detection, directory listing, file extraction, and
copy-only mutation. A filesystem may be detectable before it has enough
format-specific code to safely extract or modify file data.

## Current Capability Matrix

| Filesystem | Detect | List files | Extract/export files | Directory traversal | Copy-only mutation | Main limitations |
| --- | --- | --- | --- | --- | --- | --- |
| FAT12 | Yes | Yes | Yes | Yes | Yes for flat `.img` | Uses 8.3 ASCII names for import/create. Delete supports files and empty directories. Replacement/import/write actions currently require flat `.img` containers. |
| CBM DOS 1541/1571 | Yes | Yes | Yes | Root only | Root file import, replace, and scratch/delete for `.d64`/`.d71` | Directory import and directory creation are pending. File import writes PRG-style files into a new image copy and does not overwrite entries. |
| CBM DOS 1581 | Yes | Yes | Yes | Yes | File and directory mutation for `.d81` | Real 1581 BAM allocation exists for file and directory writes. File import does not overwrite entries. |
| Amiga OFS/FFS | Yes | Yes | Yes | Yes | No | Reader supports file/directory export. `.adf` mutation is pending because allocation bitmap, block checksums, file headers, and directory hash chains must be updated correctly. |
| CP/M variants | Yes | Yes | Yes for modelled DPBs | Root only | Root file import and delete for modelled flat `.img` | CP/M 26-sector 256K FM, Osborne 1 SSDD 200K MFM, Kaypro II SSDD 200K MFM, Tandy Model 4 CP/M 2.2 180K MFM, and Tandy Model 4 CP/M Plus mixed-sector media are modelled for extraction. Mixed-sector CP/M Plus mutation is read-only until a safe writer exists. |
| DisplayWriter | Yes | Label directory only | No | No | No | The reader lists IBM standard-label `HDR1` records from track 0. Actual DisplayWriter document extraction is not implemented. |
| RT-11 | Yes | No | No | No | No | Probe and volume label metadata exist. Directory listing and extraction are not implemented. |
| RT-11 Interchange (RX01/IBM 3740) | Yes | Active `HDR1` dataset labels | Yes for nonempty labels | No | No | Exports fixed-length EBCDIC record streams from the `HDR1` start through its first-unused address. Labelled-empty datasets expose a separately named raw residual extent and JSON manifest for forensic recovery. |
| Raw sectors | Not a filesystem | N/A | Sector dump/export | N/A | Sector patch helpers only | Raw sector operations do not understand filesystem allocation or directory structures. |

## Fluxctl Studio Function Matrix

This table describes what the GUI currently enables after an image has been
opened and probed. Write/manipulation actions always create a new image copy.

| Filesystem/media | Probe, QC, physical map | Files panel | Directory drill-down | Sector HEX | File HEX | Export selected | Replace with copy | Delete from copy | Import file | Import directory | New directory | Blank image preset | Selected-file map overlay | BAM/logical map |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| FAT12 flat `.img` | Yes | Yes | Yes | Yes | Yes | Files and directories | Yes | Files and empty directories | Yes | Yes | Yes | 180K, 360K, 720K, 1.2M, 1.44M `.img` | Cluster-chain overlay | Filesystem logical map |
| FAT12 decoded `.scp`/`.imd` | Yes | Yes | Yes | Yes | Yes | Files and directories | No | No | No | No | No | No | Cluster-chain overlay | Filesystem logical map |
| CBM DOS 1541 `.d64` | Yes | Yes | Root only | Yes | Yes | Files | Yes | Files | Root PRG import only | No | No | Formatted `.d64` | Track/sector chain overlay | BAM block map |
| CBM DOS 1571 `.d71` | Yes | Yes | Root only | Yes | Yes | Files | Yes | Files | Root PRG import only | No | No | Formatted `.d71` | Track/sector chain overlay | BAM block map for both sides |
| CBM DOS decoded `.scp`/`.imd` | Yes | Yes when reconstruction is complete enough | Root only | Yes | Yes when file chain sectors decode | Files when file chain sectors decode | No | No | No | No | No | No | Track/sector chain overlay | BAM block map from decoded sectors |
| CBM DOS 1581 `.d81` | Yes | Yes | Yes | Yes | Yes | Files and directories | Root files | Root files | Files | Yes | Yes | Formatted `.d81` | 1581 logical block overlay | 1581 BAM block map |
| CBM DOS 1581 decoded `.scp`/`.imd` | Yes | Yes when reconstruction is complete enough | Yes | Yes | Yes when file chain sectors decode | Files and directories when chains decode | No | No | No | No | No | No | 1581 logical block overlay | 1581 BAM block map from decoded sectors |
| Amiga OFS/FFS `.adf` | Yes | Yes | Yes | Yes | Yes | Files and directories | No | No | No | No | No | Minimal OFS `.adf` | Current file block overlay is approximate | Filesystem logical map |
| Amiga decoded `.scp`/`.imd` | Yes | Yes when reconstruction is complete enough | Yes | Yes | Yes when file blocks decode | Files and directories when blocks decode | No | No | No | No | No | No | Current file block overlay is approximate | Filesystem logical map |
| CP/M variants | Yes | Yes | Root only | Yes | Yes for modelled CP/M DPBs | Files for modelled DPBs | No | Modelled flat `.img` only | Modelled flat `.img` only | No | No | Osborne 1, Kaypro II, and Tandy Model 4 CP/M 2.2 `.img` | Allocation-block overlay for modelled DPBs | Filesystem logical map |
| Tandy/TRS-80 `.dsk`/`.dmk`/`.imd`/`.scp` | Yes | Model III TRSDOS 1.3, NEWDOS/80, LDOS/TRSDOS 6, and CP/M where probes pass | Root only | Yes | TRSDOS 1.3, NEWDOS/80, LDOS/TRSDOS 6, and modelled Tandy CP/M files | TRSDOS 1.3, NEWDOS/80, LDOS/TRSDOS 6, and modelled Tandy CP/M files | No | No | No | No | No | No | Allocation-block/extent overlay where supported | Physical map; filesystem logical map where supported |
| DisplayWriter | Yes | Label entries only | No | Yes | No | No | No | No | No | No | No | No | No | Physical map only |
| RT-11 | Yes | No | No | Yes | No | No | No | No | No | No | No | No | No | Physical map only |
| RT-11 Interchange RX01/IBM 3740 | Yes | Active labels and labelled-empty raw recovery extents | No | Yes | Yes | Nonempty datasets; raw residual extent where labelled empty | No | No | No | No | No | No | No | Physical map only |

Notes:

- `.scp` and `.imd` write/manipulation actions are disabled even when a
  filesystem can be listed, because Fluxctl does not yet have a preservation-safe
  way to rewrite those containers after filesystem metadata changes.
- Tandy/TRS-80 JV3 and DMK containers can be opened and converted to IMD.
  Model III TRSDOS 1.3, NEWDOS/80, and LDOS/TRSDOS 6 can list and extract root
  files. Tandy Model 4 CP/M 2.2 and CP/M Plus allocation maps are modelled for
  extraction and selected-file overlays.
- CBM DOS `.d64`/`.d71` import support currently creates root-level PRG
  entries only. CBM DOS 1581 `.d81` supports directory creation and recursive
  directory import. Existing names are not overwritten. Replace and
  scratch/delete are enabled for existing root-level files.
- Amiga selected-file highlighting is useful as a locator but remains less exact
  than FAT12 and CBM DOS until full OFS/FFS file header, extension block, and
  hash-chain traversal is implemented.
- CP/M export is enabled only when Fluxctl has a modelled disk parameter block.
  The CP/M 26-sector 256K FM, Osborne 1 SSDD 200K MFM, Kaypro II SSDD 200K MFM,
  Tandy Model 4 CP/M 2.2 180K MFM, and Tandy Model 4 CP/M Plus mixed-sector DPBs
  are supported; C64/C128 and other generic CP/M layouts still need their own
  allocation maps.

## Export Behavior

CLI and Studio file export require a filesystem plugin that can both list an
entry and extract its content. If a filesystem can list entries but extraction is
not implemented, Fluxctl should keep the listing available but reject file
export with a format-specific error.

Known list-without-extract cases:

- **CP/M**: exports files for modelled disk parameter blocks such as CP/M
  26-sector 256K FM, Osborne 1 SSDD 200K MFM, and Kaypro II SSDD 200K MFM
  media. Other CP/M variants still list root entries but reject extraction until
  their DPB and sector translation are implemented.
- **DisplayWriter**: lists standard-label `HDR1` records only; the document data
  format has not been decoded.
- **RT-11**: can identify likely RT-11 volumes, but directory listing and
  extraction are both pending.
- **RT-11 Interchange (RX01/IBM 3740)**: exports a nonempty `HDR1` dataset as
  its fixed-length EBCDIC record stream. When a label declares its extent empty,
  Studio exposes a clearly named `.RESIDUAL.RAW` sector-for-sector recovery
  export plus a JSON manifest; it does not claim this residual payload is a
  valid logical file.

## Mutation Safety

All GUI file manipulation writes a new image copy and never modifies the
original image in place. Support is intentionally enabled per filesystem and per
container only after allocation and metadata update rules are implemented.

Currently enabled copy-only mutation:

- FAT12 flat `.img`: replace, delete, import file, import directory, create
  directory.
- CP/M modelled flat `.img`: root file import and delete.
- CBM DOS `.d64`/`.d71`: root-level file import, replace, and scratch/delete.
- CBM DOS 1581 `.d81`: file import, directory import, directory creation,
  root-level replace, and root-level scratch/delete.

Studio can also create new blank formatted images for FAT12 `.img`, CBM DOS
`.d64`, CBM DOS `.d71`, CBM DOS 1581 `.d81`, minimal AmigaDOS OFS `.adf`,
and modelled CP/M `.img` layouts for Osborne 1, Kaypro II, and Tandy Model 4
CP/M 2.2 media.

Currently disabled mutation:

- CBM DOS directory import and directory creation.
- CBM DOS 1581 nested replace/delete.
- Amiga OFS/FFS `.adf` writes.
- CP/M replace, directory import, and directory creation.
- DisplayWriter writes.
- RT-11 writes.
- `.scp` and `.imd` filesystem-level writes, until a safe container rewrite path
  is designed for each target.
