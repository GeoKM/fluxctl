# Fixtures

Test fixtures mirror media types in `tests/fixtures/`. Synthetic fixtures are small to keep CI fast. New fixtures should follow the existing directory naming (e.g., `3.5inch/IBM`).

Fixture filenames are descriptive only. Detection tests must assert behavior from
decoded layout, sector geometry, and filesystem structures rather than from words
embedded in the path.

## Tandy Model II CP/M 625K formats

Fluxctl models two related 77-track, single-sided Tandy Model II CP/M layouts:

- `tandy_trs80_model2_cpm_625k`: track 0 FM 26x128; tracks 1-76 MFM 8x1024.
- `tandy_trs80_model2_cpm_16x512_625k`: track 0 FM 26x128; tracks 1-76 MFM
  16x512.

SCP and IMD fixtures retain per-track physical geometry and should probe to the
corresponding layout. A flat 625,920-byte IMG preserves the logical byte stream
but not the physical sector boundaries; use the originating SCP/IMD or pass an
explicit layout when the 8x1024 versus 16x512 distinction matters.

Fixture test tiers and their local commands are documented in
`docs/testing.md`. Keep large SCP captures in Git LFS and add only a small,
representative deterministic fixture to the fast PR tier when a new behavior
needs a short feedback cycle.
