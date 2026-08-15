# Seiko 8300 CP/M Media

Fluxctl recognises the Seiko 8300 fixture as a mixed-density, double-sided
format with 77 cylinders and 26 sectors per side:

- track 0/head 0: FM, 26 x 128-byte sectors;
- track 0/head 1: MFM, 26 x 256-byte EBCDIC label sectors;
- tracks 1-76: MFM, 26 x 256-byte sectors on both heads.

The image contains strong system evidence, including `SEIKO 8300 60K CP/M
V2.2`, Digital Research text, CP/M utilities, and Seiko-specific EBCDIC
`DDR1` labels. The current filesystem reader exposes the EBCDIC catalog on
track 1/head 0 as a read-only directory.

## Why generic CP/M extraction is disabled

The catalog records are 32-byte Seiko records. Their start, end, and length
fields are internally consistent and mostly monotonic, with gaps that require
further interpretation. They have not been proven to be physical byte offsets,
logical CP/M block numbers, or sector addresses. Strings such as `PIP.COM` and `ED.COM` elsewhere
on the image occur inside executable/system data and are not sufficient proof
of a standard CP/M directory.

Applying a generic CP/M DPB would therefore risk extracting unrelated bytes.
The plugin reports `allocation_mapping_status=unproven` and deliberately keeps
file sizes and extraction disabled until a mapping is validated.

## External evidence and next validation step

22DISK includes a named `Seiko DSDD 96 tpi 5.25"` definition, confirming that
Seiko CP/M media was treated as a vendor-specific CP/M format. Its definition
file uses geometry, sector order, DPB values, and an offset together; the
definition name alone does not prove that it applies to this 8-inch
mixed-density image.

The next reliable step is to obtain the numeric Seiko definition or a Seiko
8300 system disk with a known-good directory, then compare:

1. physical sector order and side ordering;
2. reserved-track/sector offset;
3. CP/M block size and allocation width;
4. directory entry location and record format;
5. every catalog allocation reference against the bytes recovered from the
   corresponding physical sectors.

Until all five checks agree, the Seiko catalog should remain read-only.

## EBCDIC indexed-dataset variation

`ACMS80034` uses the same physical mixed-density layout but does not carry the
Seiko CP/M catalog. It has 26 `DDR1` labels on track 0/head 1 and EBCDIC record
headers at sectors 1, 4, 7, and so on. Fluxctl identifies this separately as
`seiko_8300_ebcdic_dataset`, lists those record headers, and keeps extraction
disabled. This is a dataset organization, not evidence of a mountable CP/M
filesystem.

References:

- [Seiko 8300 history](https://www2u.biglobe.ne.jp/~n-fuji/seiko.pdf)
- [22DISK and the Seiko format entry](https://www.crimson-systems.com/apl/cpm86.htm)
- [22DISK custom definition guidance](https://groups.google.com/g/comp.os.cpm/c/GxQ2ad3aHdI/m/nt8em8edXYQJ)
- [22DISK manual](https://bitsavers.org/pdf/sydex/22Disk_1.34_Sep90.pdf)
