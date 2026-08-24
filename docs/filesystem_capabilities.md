# Filesystem Capabilities

Fluxctl separates filesystem detection, directory listing, file extraction, and
copy-only mutation. A filesystem may be detectable before it has enough
format-specific code to safely extract or modify file data.

## Current Capability Matrix

This is the user-facing capability reference for the CLI and Fluxctl Studio.
The regression test `tests/test_filesystem_capabilities_doc.py` checks that the
document includes every current blank-image preset and the major media families;
format-specific behavior remains deliberately explicit below rather than being
inferred from filenames.

The machine-readable source of truth is the [capability registry](capability_registry.md),
generated from `src/fluxctl/capabilities.py`. The GUI action states and generated
registry table use the same declarations.

| Filesystem | Detect | List files | Extract/export files | Directory traversal | Copy-only mutation | Main limitations |
| --- | --- | --- | --- | --- | --- | --- |
| FAT12 | Yes | Yes | Yes | Yes | Yes for flat `.img` | Uses 8.3 ASCII names for import/create. Delete supports files and empty directories. Replacement/import/write actions currently require flat `.img` containers. |
| CBM DOS 1541/1571 | Yes | Yes | Yes | Root only | Root file import, replace, and scratch/delete for `.d64`/`.d71` | Directory import and directory creation are pending. Import infers `.PRG`, `.SEQ`, and `.USR`; unknown suffixes default to PRG. REL import is pending because side-sector allocation is required. |
| CBM DOS 1581 | Yes | Yes | Yes | Yes | Root-file replace/delete; file import, directory import, and directory creation for `.d81` | Real 1581 BAM allocation exists for file and directory writes. Replace/delete are still root-file-only; REL side-sector mutation is not implemented. |
| Amiga OFS/FFS | Yes | Yes | Yes | Yes | No | Reader supports file/directory export. `.adf` mutation is pending because allocation bitmap, block checksums, file headers, and directory hash chains must be updated correctly. |
| Apple ProDOS | Yes | Yes | Yes | Yes | No | Read-only 140K Apple II support across WOZ, NIB, PO, DO, flat IMG, and decoded SCP. Seedling, sapling, tree, and data-fork extraction are supported. |
| Apple DOS 3.3 | Yes | Yes | Yes | Root only | No | Reads the 16-sector VTOC/catalog and T/S lists. Extracted size is sector-granular because DOS 3.3 catalog entries do not store an exact byte EOF. |
| CP/M variants | Yes | Yes | Yes for modelled DPBs and Commodore GCR translations | Root only | Root file import and delete for modelled flat `.img` | Modelled formats include CP/M 26-sector 256K FM, Osborne 1, Kaypro II, Tandy Model 4 CP/M 2.2/Plus, C64 CP/M 2.2 GCR, and C128 CP/M 3 GCR. Commodore GCR and mixed-sector CP/M Plus media are read-only. |
| DisplayWriter | Yes | Label directory only | No | No | No | The reader lists IBM standard-label `HDR1` records from track 0. Actual DisplayWriter document extraction is not implemented. |
| RT-11 | Yes | Yes | Yes | Root only | No | Read-only RAD50 directory and extent reader. Directory entries are flat; filesystem mutation is not implemented. |
| RT-11 Interchange (RX01/IBM 3740) | Yes | Active `HDR1` dataset labels | Yes for nonempty labels | No | No | Exports fixed-length EBCDIC record streams from the `HDR1` start through its first-unused address. Labelled-empty datasets expose a separately named raw residual extent and JSON manifest for forensic recovery. |
| Wang OIS package disks | Yes | Yes | Yes | Yes | No | Read-only support for the hierarchical catalog on 315K OIS installation media. File prologues, allocation-block starts, sector EOF counts, and final-sector byte counts are honoured. Wang system/software disks and user-document/archive volumes are separate formats and still need their own VTOC/file-table modelling. |
| Seiko 8300 catalog/dataset readers | Yes | Yes | No | Root only | No | Read-only EBCDIC catalog/header readers for Seiko-family mixed-density media. Catalog fields and record headers are exposed, but physical allocation and file extents remain unproven. |
| Raw sectors | Not a filesystem | N/A | Sector dump/export | N/A | Sector patch helpers only | Raw sector operations do not understand filesystem allocation or directory structures. |

## Fluxctl Studio Function Matrix

This table describes what the GUI currently enables after an image has been
opened and probed. Write/manipulation actions always create a new image copy.

| Filesystem/media | Probe, QC, physical map | Files panel | Directory drill-down | Sector HEX | File HEX | Export selected | Replace with copy | Delete from copy | Import file | Import directory | New directory | Blank image preset | Selected-file map overlay | BAM/logical map |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| FAT12 flat `.img` | Yes | Yes | Yes | Yes | Yes | Files and directories | Yes | Files and empty directories | Yes | Yes | Yes | 180K, 360K, 720K, 1.2M, 1.44M `.img` | Cluster-chain overlay | Filesystem logical map |
| FAT12 decoded `.scp`/`.imd` | Yes | Yes | Yes | Yes | Yes | Files and directories | No | No | No | No | No | No | Cluster-chain overlay | Filesystem logical map |
| CBM DOS 1541 `.d64` | Yes | Yes | Root only | Yes | Yes | Files with CBM type | Yes | Files | Root file import with type suffix inference | No | No | Formatted `.d64` | Track/sector chain overlay | BAM block map |
| CBM DOS 1571 `.d71` | Yes | Yes | Root only | Yes | Yes | Files with CBM type | Yes | Files | Root file import with type suffix inference | No | No | Formatted `.d71` | Track/sector chain overlay | BAM block map for both sides |
| CBM DOS decoded `.scp`/`.imd` | Yes | Yes when reconstruction is complete enough | Root only | Yes | Yes when file chain sectors decode | Files when file chain sectors decode | No | No | No | No | No | No | Track/sector chain overlay | BAM block map from decoded sectors |
| CBM DOS 1581 `.d81` | Yes | Yes | Yes | Yes | Yes | Files and directories | Root files | Root files | Files | Yes | Yes | Formatted `.d81` | 1581 logical block overlay | 1581 BAM block map |
| CBM DOS 1581 decoded `.scp`/`.imd` | Yes | Yes when reconstruction is complete enough | Yes | Yes | Yes when file chain sectors decode | Files and directories when chains decode | No | No | No | No | No | No | 1581 logical block overlay | 1581 BAM block map from decoded sectors |
| Amiga OFS/FFS `.adf` | Yes | Yes | Yes | Yes | Yes | Files and directories | No | No | No | No | No | Minimal OFS `.adf` | Current file block overlay is approximate | Filesystem logical map |
| Amiga decoded `.scp`/`.imd` | Yes | Yes when reconstruction is complete enough | Yes | Yes | Yes when file blocks decode | Files and directories when blocks decode | No | No | No | No | No | No | Current file block overlay is approximate | Filesystem logical map |
| Apple II 140K `.woz`/`.po`/`.do`/`.nib`/`.dsk`/`.img`/`.scp` | Yes | ProDOS and DOS 3.3 | ProDOS directories; DOS 3.3 root only | Yes | Yes | Files and ProDOS directories | No | No | No | No | No | No | ProDOS block or DOS 3.3 T/S-list overlay | Physical/filesystem map |
| CP/M variants | Yes | Yes | Root only | Yes | Yes for modelled CP/M DPBs and Commodore GCR translations | Files for modelled DPBs and Commodore GCR | No | Modelled flat `.img` only | Modelled flat `.img` only | No | No | Osborne 1, Kaypro II, and Tandy Model 4 CP/M 2.2 `.img` | Allocation-block overlay for modelled DPBs and Commodore GCR | Filesystem logical map |
| Tandy/TRS-80 `.dsk`/`.dmk`/`.imd`/`.scp` | Yes | Model III TRSDOS 1.3, NEWDOS/80, LDOS/TRSDOS 6, and CP/M where probes pass | Root only | Yes | TRSDOS 1.3, NEWDOS/80, LDOS/TRSDOS 6, and modelled Tandy CP/M files | TRSDOS 1.3, NEWDOS/80, LDOS/TRSDOS 6, and modelled Tandy CP/M files | No | No | No | No | No | No | Allocation-block/extent overlay where supported | Physical map; filesystem logical map where supported |
| DisplayWriter | Yes | Label entries only | No | Yes | No | No | No | No | No | No | No | No | No | Physical map only |
| RT-11 normal volumes | Yes | Yes | Root only | Yes | Yes | Files | No | No | No | No | No | No | No | Physical map |
| RT-11 Interchange RX01/IBM 3740 | Yes | Active labels and labelled-empty raw recovery extents | No | Yes | Yes | Nonempty datasets; raw residual extent where labelled empty | No | No | No | No | No | No | No | Physical map only |
| Wang OIS 315K package `.img`/decoded `.scp` | Yes | Package catalog files and directories | Yes | Yes | Yes | Files and directories | No | No | No | No | No | No | Allocation extent overlay | Physical map |
| Wang OIS 315K system/software `.img` | Yes | No supported file view yet | No | No | No | No | No | No | No | No | No | No | Logical 16x256 sector map | Physical map |
| Seiko 8300 mixed-density `.img`/`.scp` | Yes | EBCDIC catalog or dataset headers | Root only | Yes | No | No | No | No | No | No | No | No | No | Physical map |

Notes:

- `.scp` and `.imd` write/manipulation actions are disabled even when a
  filesystem can be listed, because Fluxctl does not yet have a preservation-safe
  way to rewrite those containers after filesystem metadata changes.
- Tandy/TRS-80 JV3 and DMK containers can be opened and converted to IMD.
  Model III TRSDOS 1.3, NEWDOS/80, and LDOS/TRSDOS 6 can list and extract root
  files. Tandy Model 4 CP/M 2.2 and CP/M Plus allocation maps are modelled for
  extraction and selected-file overlays.
- CBM DOS `.d64`/`.d71` import support currently creates root-level entries;
  `.PRG`, `.SEQ`, and `.USR` suffixes select the CBM type and unknown suffixes
  default to PRG. REL import is pending because side-sector allocation is
  required. CBM DOS 1581 `.d81` supports directory creation and recursive
  directory import. Existing names are not overwritten. Replace and
  scratch/delete are enabled for existing root-level files.
- Amiga selected-file highlighting is useful as a locator but remains less exact
  than FAT12 and CBM DOS until full OFS/FFS file header, extension block, and
  hash-chain traversal is implemented.
- CP/M export is enabled only when Fluxctl has a modelled disk parameter block
  or a format-specific translation map.
  The CP/M 26-sector 256K FM, Osborne 1 SSDD 200K MFM, Kaypro II SSDD 200K MFM,
  Tandy Model 4 CP/M 2.2 180K MFM, and Tandy Model 4 CP/M Plus mixed-sector DPBs
  are supported. C64 CP/M 2.2 and C128 CP/M 3 GCR images use their documented
  allocation translations. Other generic CP/M layouts, including foreign 1571
  MFM media, still need their own allocation maps.

## Export Behavior

CLI and Studio file export require a filesystem plugin that can both list an
entry and extract its content. If a filesystem can list entries but extraction is
not implemented, Fluxctl should keep the listing available but reject file
export with a format-specific error.

Known list-without-extract cases:

- **CP/M**: exports files for modelled disk parameter blocks such as CP/M
  26-sector 256K FM, Osborne 1 SSDD 200K MFM, and Kaypro II SSDD 200K MFM
  media, plus C64 CP/M 2.2 and C128 CP/M 3 GCR media. Other CP/M variants still
  list root entries but reject extraction until their DPB and sector
  translation are implemented.
- **DisplayWriter**: lists standard-label `HDR1` records only; the document data
  format has not been decoded.
- **RT-11 normal volumes**: lists the flat RAD50 directory and extracts files
  from modelled logical 512-byte block extents. The reader is read-only and
  does not expose directory traversal or mutation.
- **RT-11 Interchange (RX01/IBM 3740)**: exports a nonempty `HDR1` dataset as
  its fixed-length EBCDIC record stream. When a label declares its extent empty,
  Studio exposes a clearly named `.RESIDUAL.RAW` sector-for-sector recovery
  export plus a JSON manifest; it does not claim this residual payload is a
  valid logical file.
- **Wang OIS package disks**: lists the package's actual hierarchical catalog
  and exports its file extents. The reader is intentionally limited to the
  catalog structure established on 315K OIS installation media; Wang archive
  document catalogs and system/software disks are not yet claimed as compatible.
- **Wang OIS system/software disks**: the standard 315K geometry is recognised,
  and flat IMG files use the logical 16-sector/256-byte view for sector mapping.
  File listing, selected-file overlays, and export remain disabled until the
  system disk file table or VTOC and its allocation extents are decoded.
- **Seiko 8300 catalog/dataset media**: lists decoded EBCDIC catalog records or
  dataset headers, but rejects HEX viewing and export until catalog offsets can
  be mapped to physical allocation safely.

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
- Wang OIS writes.
- Seiko 8300 catalog/dataset writes.
- `.scp` and `.imd` filesystem-level writes, until a safe container rewrite path
  is designed for each target.
