# Filesystem Capabilities

Fluxctl separates filesystem detection, directory listing, file extraction, and
copy-only mutation. A filesystem may be detectable before it has enough
format-specific code to safely extract or modify file data.

## Current Capability Matrix

| Filesystem | Detect | List files | Extract/export files | Directory traversal | Copy-only mutation | Main limitations |
| --- | --- | --- | --- | --- | --- | --- |
| FAT12 | Yes | Yes | Yes | Yes | Yes for flat `.img` | Uses 8.3 ASCII names for import/create. Delete supports files and empty directories. Replacement/import/write actions currently require flat `.img` containers. |
| CBM DOS 1541/1571 | Yes | Yes | Yes | Root only | Root file import for `.d64`/`.d71` | No delete, replace, directory import, or directory creation yet. Import writes PRG-style files into a new image copy and does not overwrite entries. |
| CBM DOS 1581 | Yes | Yes | Yes | Yes | Root file import for `.d81` | Real 1581 BAM allocation exists for root-file import. Delete, replace, directory import, and directory creation are pending. |
| Amiga OFS/FFS | Yes | Yes | Yes | Yes | No | Reader supports file/directory export. `.adf` mutation is pending because allocation bitmap, block checksums, file headers, and directory hash chains must be updated correctly. |
| CP/M variants | Yes | Yes | No | Root only | No | Directory entries and allocation references are decoded for listing and map overlays, but file extraction is not implemented yet. |
| DisplayWriter | Yes | Label directory only | No | No | No | The reader lists IBM standard-label `HDR1` records from track 0. Actual DisplayWriter document extraction is not implemented. |
| RT-11 | Yes | No | No | No | No | Probe and volume label metadata exist. Directory listing and extraction are not implemented. |
| Raw sectors | Not a filesystem | N/A | Sector dump/export | N/A | Sector patch helpers only | Raw sector operations do not understand filesystem allocation or directory structures. |

## Export Behavior

CLI and Studio file export require a filesystem plugin that can both list an
entry and extract its content. If a filesystem can list entries but extraction is
not implemented, Fluxctl should keep the listing available but reject file
export with a format-specific error.

Known list-without-extract cases:

- **CP/M**: lists root entries and estimates sizes from CP/M records, but
  `extract_file()` is not implemented.
- **DisplayWriter**: lists standard-label `HDR1` records only; the document data
  format has not been decoded.
- **RT-11**: can identify likely RT-11 volumes, but directory listing and
  extraction are both pending.

## Mutation Safety

All GUI file manipulation writes a new image copy and never modifies the
original image in place. Support is intentionally enabled per filesystem and per
container only after allocation and metadata update rules are implemented.

Currently enabled copy-only mutation:

- FAT12 flat `.img`: replace, delete, import file, import directory, create
  directory.
- CBM DOS `.d64`/`.d71`: root-level file import.
- CBM DOS 1581 `.d81`: root-level file import.

Currently disabled mutation:

- CBM DOS delete/replace/directories.
- CBM DOS 1581 delete/replace/directories.
- Amiga OFS/FFS `.adf` writes.
- CP/M writes.
- DisplayWriter writes.
- RT-11 writes.
- `.scp` and `.imd` filesystem-level writes, until a safe container rewrite path
  is designed for each target.
