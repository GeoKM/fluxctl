# Fluxctl Studio Manual Functionality Test Plan

Use this plan when manually exercising Fluxctl Studio against known fixtures or
new real-world disk images. The aim is to catch problems that automated tests
miss: confusing workflow, wrong visual assumptions, stale status panels,
surprising disabled buttons, filesystem misidentification, and format-specific
edge cases.

## Setup

Start Studio from the repository checkout:

```sh
.venv/bin/fluxctl-studio
```

Before starting a focused test pass:

- Run `fluxctl doctor` and note warnings about native acceleration,
  Greaseweazle, or HxC.
- Keep a scratch output directory for conversions, exports, and copy-only
  mutation outputs.
- Never choose an original fixture path as the output for mutation workflows.
  Studio should prevent this, but it is worth verifying.
- Record the exact image path, mode, map view, action, observed result, and any
  traceback or status text for each issue.

## General UI Smoke Tests

Run these once at the start of a session.

| Area | Steps | Expected result | Flag as problem if |
| --- | --- | --- | --- |
| Startup | Launch Studio with no image loaded. | Simple mode starts without a crash. Image-specific panels are blank or neutral. | Old image data is visible, controls imply an image is loaded, or Python exits unexpectedly. |
| Open image | Open any known-good `.img`, `.d64`, `.d71`, `.d81`, `.adf`, `.scp`, and `.imd`. | Path updates, old file list clears, summary fields refresh, status becomes ready/good/suspect as appropriate. | Previous image files remain listed, stale map remains, or summary fields disagree with the opened image. |
| Mode switch | Switch between Simple Mode and Advanced Mode with and without an image loaded. | No data loss; Advanced fields match current image when loaded and stay blank when not loaded. | Buttons do nothing silently, fields show stale values, or lower panel does not match selected mode/action. |
| Top-level tabs | Visit Disk & Imaging, Files & Directories, HEX & ASCII, Advanced, and Jobs & Logs. | Each workflow has a full-size page; Advanced is disabled in Simple Mode and enabled in Advanced Mode. | Content remains cramped, a tab is blank, or Advanced is selectable in Simple Mode. |
| Action routing | List files, view file/sector HEX, run Advanced Info/Sectors, and run a Greaseweazle read. | Each action selects its relevant Files, HEX, Advanced, or Jobs tab without losing the loaded image. | Results appear only on a hidden tab, the wrong tab opens, or prior results are cleared unexpectedly. |
| Disabled actions | Open unsupported media for write actions, such as `.scp`, `.imd`, `.adf`, CP/M, DisplayWriter, or RT-11. | Unsupported buttons are greyed out and tooltips explain why. | Disabled buttons look active, enabled buttons fail immediately, or tooltip text is stale. |

## Probe, QC, and Map Views

For each fixture group below, run `Probe`, `QC Report`, and `Render Map`.

Check:

- Layout, encoding, filesystem, confidence, size, and status are plausible.
- QC good/weak/missing/bad totals match the map colors.
- Physical disk maps show the right number of heads and the correct sector
  geometry.
- Filesystem Logical Map and Whole Physical Disk Health have clear differences
  where logical relevance differs from raw sector health.
- Map legend colors match visible sector/block colors.
- Track and sector delimiter lines remain visible at typical window sizes.
- Hovering a map sector/block shows useful track/head/sector and quality
  information.
- Clicking a sector/block loads the correct Sector HEX view.

Flag:

- Single-sided media drawn as double-sided, or double-sided media drawn as one
  head.
- Commodore variable-sector GCR tracks drawn with nonexistent red sectors.
- CP/M logical maps marking deliberately unused disk areas as filesystem
  damage.
- Status says suspect/error when the detail report shows no corresponding weak,
  missing, or bad items.
- Head/track/sector labels use confusing Commodore numbering in CBM-specific
  views.

## Fixture Coverage Matrix

Use these as the baseline known fixtures.

| Family | Fixtures to test | Expected focus |
| --- | --- | --- |
| Commodore 1541 CBM DOS | `Commodore-1541-SSDD-GCR-C64-170K.d64`, `.scp`; `0100-LADS001-CBM_LADS_Assembler-orig.d64`, `.scp`; `0008-DISC001-Disk_Disector-v5.d64`, `.scp` | GCR layout, varied sector zones, CBM DOS detection, BAM map, root file list, bad-sector diagnostics on weak captures. |
| Commodore 1541 CP/M 2.2 | `Commodore-1541-SSDD-GCR-C64CPM-170K.d64`, `.scp` | C64 CP/M detection, logical map relevance, file list, CP/M export disabled, SCP reconstruction quality. |
| Commodore 1571 CBM DOS | `Commodore-1571-DSDD-GCR-C128-341K.d71`, `.scp` | Two heads, side 0/side 1 physical map, BAM for both sides, root file import on `.d71`. |
| Commodore 1571 CP/M | `Commodore-1571-DSDD-MFM-C128CPM-340K.d71`, related `.scp`; `Commodore-1571-SSDD-MFM-C128CPM-170K.d64`, related `.scp` | CP/M detection, GCR/MFM expectations, root listing, export disabled, selected-file overlay limitations. |
| Commodore 1581 CBM DOS | `Commodore-1581-DSDD-MFM-C64-800K.d81`, `.img`, `.scp` | 80-track MFM geometry, directory drill-down, 1581 BAM map, root-file import on `.d81`, copy-only gating on `.scp/.img` where applicable. |
| Amiga OFS/FFS | `Commodore-1010-DSDD-MFM-Amiga-880K.adf`, `.img`, `.scp` | Amiga filesystem detection, directory drill-down, file/directory export, approximate selected-file overlay, mutation disabled. |
| Apple II ProDOS 140K | `Apple-AppleII-SSDD-Apple6A2-ProDOSvb1a-140K.woz`, `.po`, `.scp` | All three identify 35 populated tracks, 16x256-byte sectors, ProDOS volume `/PRODOS`, the same three root entries, exact file extraction, and matching selected-file overlays. WOZ and SCP conversion to PO must be byte-identical. |
| IBM DOS FAT12 | 180K, 360K, 720K, 1.2M, 1.44M `.img`, `.imd`, `.scp` fixtures | FAT12 detection, directory support where available, file export, flat `.img` mutation actions, decoded container write gating. |
| IBM XDF OS/2 | `IBM-XDF-DSHD-MFM-OS2-1890K.scp` | 80-track DSHD MFM physical XDF layout; track 0 has 19x512 sectors, tracks 1-79 use 512/1024/2048/8192-byte sectors. Probe/QC should identify `ibm_xdf_1890k`; filesystem listing remains unsupported until logical XDF unpacking is implemented. |
| IBM 8-inch FAT12 | `IBM-Generic-DSDD-MFM-IBMPC-1200K-B.scp`, `IBM-Generic-DSDD-MFM-IBMPC-1200K-C.scp`, known-bad 1.2M variants | Geometry mismatch tolerance, empty disk behavior, bad/unknown filesystem handling, need for known-good 1.15 MB fixtures. |
| IBM DisplayWriter | `IBM-6580-SSDD-FM-DisplayWriter-284K.scp`, `.imd`, `.img` | Mixed FM geometry, standard-label entries, document extraction disabled with a clear message. |
| DEC RT-11 RX02 | `DEC-RX02-DSDD-MFM-RT11-500K.scp`, `.imd`, `.img` | Probe metadata, list the flat RAD50 directory, view HEX, and export readable files. Mutation and directory traversal remain unsupported. |
| DEC RT-11 Interchange RX01 | `DEC-RX01-SSSD-FM-RT11_IDF-250K.scp`, `.imd`, `.img` | Probe as 77-track, one-sided, 26 x 128-byte FM RX01 media. Export nonempty `HDR1` datasets as fixed-length EBCDIC record streams. This fixture's `DATA` label is empty, so verify the separately named `DATA.RESIDUAL.RAW` forensic export and its JSON missing-sector manifest. |
| Wang OIS package disk | `Wang-OIS100-HS32-FM-PeripheralsII-315K.scp`, `.img` | Probe as 77 x 16 x 256-byte `wang_ois` media. Navigate `/PRINT`, view and export files such as `/INSTALL` and `/PRINT/T300/OBJ`, recursively export `/PRINT/T407`, and verify selected-file allocation highlighting. Mutation remains disabled. |
| Seiko 8300 mixed-density media | Seiko 8300 `.img` and `.scp` fixtures | Probe as Seiko-family mixed-density media, list EBCDIC catalog/dataset records, and verify that HEX/export actions explain that physical allocation mapping is not yet proven. |

## Files Panel and Directory Workflows

Run on FAT12, 1581, and Amiga images with directories.

| Action | Expected result | Flag as problem if |
| --- | --- | --- |
| List Files | Root directory appears, sorted and readable. Sizes are nonzero for normal files. | Old image entries remain, CBM DOS sizes are zero unexpectedly, directories are shown as files. |
| Double-click directory | Files panel enters that directory. | Directory name becomes editable, nothing happens silently, or path display is wrong. |
| Up | Moves to parent directory. | Stays in same directory or jumps to root unexpectedly. |
| Root | Returns to `/`. | Previous nested path remains in later actions. |
| Select multiple files | Multiple selection is visible and stable. | Only last selected item remains selected unexpectedly. |
| View File HEX | Hex tab becomes active and shows selected file content. | Hex panel stays hidden, wrong file appears, or directory selection dumps misleading data. |
| Select file on map | File allocation overlay appears for supported formats. | No highlight, wrong sectors highlighted, or stale highlight remains after changing file/image. |

## File Export Tests

Run on FAT12, CBM DOS, 1581, and Amiga. Also try CP/M, DisplayWriter, and RT-11
to confirm unsupported export is explicit.

| Action | Expected result | Flag as problem if |
| --- | --- | --- |
| Export one file | Destination file is created with correct name and byte size. | Empty export, wrong file content, overwritten existing file without warning. |
| Export multiple files | All selected files are exported. | Only the last selected file appears. |
| Export directory | Directory tree is created for filesystems with extraction support. | Directory export silently skips files or flattens subdirectories unexpectedly. |
| Export unsupported listed entry | Clear filesystem-specific unsupported message. | Button is enabled but fails with a Python traceback or generic error. |
| Reopen exported files | Content makes sense in a host hex editor or viewer. | Exported bytes appear shifted, truncated, padded incorrectly, or include directory metadata. |

## HEX Workflows

Test both Simple and Advanced Mode.

| Action | Expected result | Flag as problem if |
| --- | --- | --- |
| Sector HEX by fields | The selected head/track/sector dumps with stable address and ASCII columns. | Off-by-one addressing, wrong head, or unavailable sector silently dumps another sector. |
| Sector HEX by map click | Clicking a sector updates the Sector HEX view automatically. | Click does nothing, loads wrong address, or fails after map view changes. |
| File HEX | Dumps selected file bytes and activates the Hex tab. | Wrong file, stale file, or no tab switch. |
| Advanced Sector Dump mode | Dump button applies to selected sector fields. | Dump uses file selection or stale sector fields. |
| Advanced File Dump mode | Dump button applies to selected file path. | Directory dumps as a file without explanation, or stale file path is used. |
| Directory selection in file dump | Either disabled or clearly explained as not implemented except future raw directory mode. | Misleading output or crash. |

## Copy-Only Mutation Tests

These should be tested only on disposable copies or outputs from the blank image
creator. Confirm every action writes a new image and leaves the source unchanged.

### FAT12 `.img`

| Action | Expected result | Extra checks |
| --- | --- | --- |
| Replace file with same size | Output copy contains new content. | Source hash unchanged. |
| Replace file with smaller content | Output file size updates and content is not padded into visible file data. | FAT chain remains valid. |
| Replace file with larger content within existing allocation | Output file grows correctly. | Existing directories still list. |
| Replace file requiring new clusters | Free clusters decrease and file content is complete. | No cross-linked files. |
| Delete file | File disappears from output copy. | Other files still readable. |
| Delete empty directory | Directory disappears. | Non-empty directory delete should be rejected. |
| Import file | New 8.3 ASCII file appears and exports correctly. | Long names are rejected clearly. |
| Import directory | Tree imports recursively. | Nested files and directories are readable. |
| Create directory | Empty directory appears and can receive imported files. | Duplicate names are rejected clearly. |

### CBM DOS `.d64/.d71`

| Action | Expected result | Extra checks |
| --- | --- | --- |
| Import root PRG file | New PRG-style entry appears in root. | Source hash unchanged. |
| Import duplicate name | Clear rejection. | No partial output file left behind if rejected. |
| Import subdirectory | Disabled or rejected clearly. | Button tooltip matches capability docs. |
| Delete/replace/create directory | Disabled. | Disabled styling and tooltip are clear. |
| Validate `.d71` in external tools | Free block count and BAM side 1 are plausible. | DirMaster/emulator should not require validation for blank `.d71` outputs. |

### CBM DOS 1581 `.d81`

| Action | Expected result | Extra checks |
| --- | --- | --- |
| Import root PRG file | New file appears and can be exported. | 1581 BAM map shows file blocks. |
| Import into subdirectory | Disabled or rejected clearly for now. | Tooltip/status says root-only. |
| Delete/replace/import directory/new directory | Disabled. | No misleading enabled controls. |

### Unsupported Containers

For `.scp` and `.imd`, write/manipulation actions should stay disabled even if
the filesystem can list and export files.

## Blank Image Creation Tests

Create one image for each preset, then immediately open it in Studio.

| Preset | Expected result |
| --- | --- |
| FAT12 180K `.img` | Probes as FAT12, empty root, mutation actions enabled. |
| FAT12 360K `.img` | Probes as FAT12, empty root, mutation actions enabled. |
| FAT12 720K `.img` | Probes as FAT12, empty root, mutation actions enabled. |
| FAT12 1.2M `.img` | Probes as FAT12, empty root, mutation actions enabled. |
| FAT12 1.44M `.img` | Probes as FAT12, empty root, mutation actions enabled. |
| CBM DOS 1541 `.d64` | Probes as CBM DOS, BAM and directory blocks allocated, root import enabled. |
| CBM DOS 1571 `.d71` | Probes as CBM DOS 1571, both BAM sides plausible, root import enabled. |
| CBM DOS 1581 `.d81` | Probes as CBM DOS 1581, 80-track BAM map plausible, root import enabled. |
| AmigaDOS OFS `.adf` | Probes as Amiga OFS, empty root, mutation disabled. |

After creating each blank image, try opening it in an external emulator/tool when
possible and note any validation or free-space disagreement.

## Convert Workflow Tests

For every conversion, confirm the default output format matches the source
layout before pressing Convert. The Convert dialog should also let the tester
choose another suitable target format, such as raw `.img` for an Amiga `.scp`,
before the save-location dialog appears.

| Source | Expected default | Check |
| --- | --- | --- |
| 1541 GCR `.scp` | `.d64` where layout supports sector image export; `.g64` when preserving GCR track data is the better target. | Output probes as Commodore, not FAT12. |
| 1571 GCR `.scp` | `.d71` when layout is `commodore_gcr_1571_341k`. | Does not collapse side/head geometry. |
| 1581 MFM `.scp` | `.d81` when layout is `commodore_mfm_1581_800k`. | Output probes as CBM DOS 1581 and lists files. |
| 1581 `.img` | `.d81` when the tester chooses Commodore 1581 image. | Output matches the 819,200-byte D81 flat sector image. |
| 1581 `.d81` | Raw `.img` by default. | Output matches the original 819,200-byte sector image. |
| IBM MFM `.scp/.imd` | Raw `.img` or `.imd` as selected. | Output probes as FAT12 where filesystem exists. |
| Amiga MFM `.scp` | `.adf` when reconstruction is good enough. | Output probes as Amiga OFS/FFS. |
| Amiga MFM `.scp` to raw | `.img` when the tester chooses raw sector image. | Output probes as Amiga OFS/FFS with `--layout amiga_mfm_880k`. |
| Amiga MFM `.scp` to IMD | `.imd` remains available as a decoded-sector interchange target. | Studio warns that IMD does not preserve Amiga physical track encoding; regenerated IMD should QC good and list files, but ADF remains the recommended Amiga target. |
| DisplayWriter FM `.scp/.imd` | `.imd` or raw as appropriate. | Mixed sector sizes are represented or rejected clearly. |
| Apple II WOZ/NIB/SCP | `.po` or `.do` as selected. | PO is the default for ProDOS media; converting the supplied WOZ and SCP to PO produces identical 143,360-byte output. IMD is not offered. |

Flag any conversion that silently chooses FAT12 for Commodore/Amiga media,
does not offer an appropriate alternate target, loses head geometry, or reports
success while output cannot be reopened.

Also use the Studio `Round Trip...` action after important conversions. Choose
the same intermediate format as the conversion target, keep intermediates when
you want to inspect the generated images, and optionally save the JSON report.
The Jobs panel should show the same `Forward check` and `Round-trip check`
results as the CLI.

## CLI Round-Trip Tests

Use `fluxctl roundtrip` when checking whether a conversion preserves decoded
sector content. These tests compare the sector image reconstructed from each
step, not the raw container bytes.

| Workflow | Command shape | Expected result |
| --- | --- | --- |
| SCP to emulator image | `fluxctl roundtrip source.scp --layout amiga_mfm_880k --to adf --json-out roundtrip.json` | Forward and round-trip checks both report `MATCH`. |
| Flat image through raw | `fluxctl roundtrip source.adf --to raw --back-to adf --work-dir scratch` | The final decoded hash matches the original. |
| Sector image to synthesized SCP | `fluxctl synthesize-scp source.img --format ibm.720 --out generated.scp`, then `fluxctl compare source.img generated.scp --layout-a ibm_mfm_720k --layout-b ibm_mfm_720k` | The generated SCP decodes to matching sector data. It is calibrated logical flux, not original preservation flux. |
| Verified physical write | `fluxctl write source.img --format ibm.720 --layout ibm_mfm_720k --readback-out readback.scp --confirm-write` | Greaseweazle write verification succeeds, the retained raw SCP read-back compares as a match, and the JSON manifest includes hashes, commands, and both tool outputs. |

Round-trip JSON reports also contain separate `data_equivalence`,
`logical_geometry_equivalence`, and `preservation_equivalence` results. Check
sector IDs/order/sizes, deleted marks, CRC and missing/synthesized status, and
`filesystem_file_equivalence` when the filesystem plugin can extract files.

Flag any `DIFFER` result where the source image has no weak/missing/bad sector
warnings, or any workflow that claims success but produces an output image that
cannot be probed/listed.

## Advanced Mode Tests

| Area | Steps | Expected result |
| --- | --- | --- |
| Initial state | Enter Advanced Mode with no image. | Top panel blank; lower panel shows doctor summary only. |
| Loaded image | Enter Advanced Mode after opening an image. | Top panel fields match current image and defaults are sensible. |
| Info | Press Info. | The Advanced tab opens and shows the text/detail view. |
| Sectors | Change sector fields and press Sectors. | The Advanced tab opens and shows the selected track/head sector list. |
| File path chooser | Use the dropdown to traverse directories and select files. | File path is valid and action mode follows selected file. |
| Dump mode switch | Toggle Sector Dump and Selected File Dump. | Dump applies to the chosen target only and opens the editable HEX & ASCII tab. |

## Visual and Usability Review

While testing, deliberately resize the window, switch tabs, and use long paths.

Look for:

- Text clipping in buttons, tables, tooltips, and status bars.
- Controls that appear clickable but are disabled or do nothing.
- Buttons whose text does not match the action that actually runs.
- Status messages that overwrite useful error detail too quickly.
- Table columns that make file names, sizes, or paths hard to read.
- Scrollbars that hide important map or table content.
- Mismatched color meanings between physical map, filesystem logical map, and
  BAM map.
- Any Python crash dialog. Record the image, action, and last visible status.

## Issue Report Template

Use this short template when logging a finding:

```text
Image:
Source format:
Mode: Simple / Advanced
Map view:
Action:
Expected:
Observed:
Status text:
Can reproduce: yes/no
External tool comparison:
Notes/screenshots:
```

## Known Gaps to Keep in Mind

These are expected limitations today, not necessarily bugs:

- CP/M export and selected-file map overlays are available for modelled DPBs.
  Other CP/M variants may list entries but still need per-format DPB support.
- DisplayWriter lists standard-label entries only; document extraction is not
  implemented.
- Normal RT-11 volumes can list their flat RAD50 directory and extract files;
  mutation and directory traversal are not implemented. RT-11 Interchange
  media has separate HDR1 dataset and residual-recovery behavior.
- `.scp` and `.imd` filesystem-level writes are disabled.
- CBM DOS and 1581 mutation is limited to the explicitly enabled copy-only
  actions in the capability matrix.
- Amiga `.adf` mutation is disabled.
- Amiga selected-file map overlay is approximate until full OFS/FFS block-chain
  traversal is implemented.
