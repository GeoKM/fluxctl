# Wang OIS System-Disk Research

This note records the current reverse-engineering boundary for the Wang OIS
315K hard-sectored images. It is deliberately separate from the supported
Wang package-catalog reader.

## Documented standard volume model

The Wang VS utilities documentation describes a standard labelled volume as
using 2 KiB physical blocks. Its VTOC starts at block 4, after the volume
label, bad-block list, and bitmap area. The VTOC block sequence is:

1. FDAV, the available-space chain
2. FDX1, the library index
3. FDX2, the file index
4. FDR, the file-descriptor records

FDX2 records point to FDR blocks. FDR1 stores permanent file attributes and
dynamic state such as record count, final data block, and final-record length;
FDR2 can describe additional extents. A valid implementation therefore needs
to decode both the VTOC chains and the extent representation before it can
list or extract files.

## ACMS80228 result

The 315,392-byte image has the expected Wang logical geometry: 77 tracks,
16 logical sectors per track, and 256 bytes per sector, which is 154 logical
2 KiB blocks. The documented VTOC location, logical block 4, contains
executable/system-utility material rather than an FDAV/FDX1/FDX2/FDR chain.

The opening blocks contain bootstrap tables, a date field, blank or erased
regions, and fixed-position Z80/8080 code modules. Later blocks contain Wang
word-processing utility menus and program text. These observations are
consistent with a non-labelled OIS system/software disk. They do not prove
file boundaries or allocation extents.

In particular, strings such as `PLM CNVT`, `PLM TC`, and `CREATE VTOC ENTRY`
are embedded program data, not verified catalog records. They must not be
presented as files or used to colour file-occupancy overlays.

## Implementation boundary

Fluxctl therefore continues to provide physical/logical sector inspection for
this image class, while leaving file listing, selected-file overlays, and
export disabled until a second source establishes the system-disk module table
or a recoverable VTOC. The next useful evidence would be a known-good writable
OIS system disk, a Wang utility dump of its VTOC map, or a matching disk image
with the system's module directory intact.

## Current inspector

Fluxctl now includes a conservative `wang_vtoc` inspector for development and
forensics. It checks the documented 2 KiB VTOC window (blocks 4 through 7) for
the four control-block identifiers `FDAV`, `FDX1`, `FDX2`, and `FDR1`, and
reports their block and byte offsets when present. It does not claim a
mountable filesystem, expose files, or colour allocation overlays. The latter
remain disabled until FDX/FDR pointer fields and the extent encoding have been
verified against a known-good labelled system volume.
