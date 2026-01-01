# Test fixtures

This directory mirrors the fixture naming convention used by the integration suite. Each fixture lives under a size- and
manufacturer-specific folder and follows the pattern:

```
<manufacturer>-<DriveStyle>-<SidesDensity>-<Encoding>-<OS>-<ApproxCapacity>.<ext>
```

An optional JSON/YAML sidecar matching the stem can include expectations such as sector counts or file totals. The
`discover_fixtures` helper in `fluxctl.fixtures` will load both the primary image and the metadata when building
parameterised pytest cases.
