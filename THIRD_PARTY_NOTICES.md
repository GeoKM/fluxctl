# Third-party projects and acknowledgements

Fluxctl can integrate with the following separately maintained projects. These
credits identify the upstream projects and the contributions Fluxctl builds on;
they do not imply endorsement or a change to the projects' own licenses.

## Greaseweazle

[Greaseweazle](https://github.com/keirf/greaseweazle) provides the host tools
and Python APIs used by Fluxctl for optional floppy-drive operations and Amiga
track decoding. The project and its host tools are authored by **Keir Fraser**.
The upstream repository includes its `COPYING` file, which dedicates the work
to the public domain under the Unlicense.

## Adrian Black's live drive diagnostics

The optional **Drive Diagnostics** page uses the diagnostic additions from
[Adrian Black's Greaseweazle fork](https://github.com/misterblack1/greaseweazle/tree/diag).
The fork extends Greaseweazle with interactive drive readings and diagnostic
controls. Its diagnostic source credits Keir Fraser's underlying work; Adrian
Black's fork and commit history identify his diagnostic changes and subsequent
maintenance. The fork carries Greaseweazle's `COPYING` notice; consult the
fork itself for the exact source revision and any later attribution updates.

## HxC Floppy Emulator

[HxC Floppy Emulator](https://github.com/jfdelnero/HxCFloppyEmulator) provides
the optional `hxcfe` command-line helper used by Fluxctl for format hints and
image conversions. The project is authored by **Jean-François DEL NERO / HxC2001**.
Its source tree contains component-level `COPYING` and `COPYING_FULL` notices,
including notices for embedded libraries; refer to those files in the HxC
repository for applicable terms and required attribution.
