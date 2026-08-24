# Test fixtures

This directory mirrors the fixture naming convention used by the integration suite. Each fixture lives under a size- and
manufacturer-specific folder and follows the pattern:

```
<manufacturer>-<DriveStyle>-<SidesDensity>-<Encoding>-<OS>-<ApproxCapacity>.<ext>
```

An optional JSON/YAML sidecar matching the stem can include regression
expectations. The preferred JSON schema is `fluxctl.fixture-expectation/v1` and
can record independently verified geometry, filesystem, QC bounds, directory
entries, selected SHA-256 file hashes, and supported conversion outcomes. This
metadata is test evidence only; it is never consulted by runtime detection.
The `discover_fixtures` helper in `fluxctl.fixtures` loads both the primary
image and metadata when building parameterised pytest cases.

GCR SCP fixtures can take significantly longer to decode than MFM. When running CLI checks, prefer the
`scripts/fixture_cli_smoke.py` helper, which uses longer default timeouts for GCR media.
