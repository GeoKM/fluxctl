# Test Tiers

Fluxctl keeps the normal development loop fast while retaining a larger
fixture-based regression suite. The tiers are selected with pytest markers.

## Fast PR Tests

These are deterministic unit, parser, CLI, and packaging checks that do not
need the full fixture collection:

```sh
./.venv/bin/python -m pytest -q -m fast
```

GitHub pull requests run the curated fast command in `.github/workflows/ci.yml`
on Linux, macOS, and Windows.

## Full Fixture Regression

This exercises filesystem detection, decoding, QC, Studio services, and the
representative fixture set. It requires Git LFS content and is intended for
pushes to `main`, manual runs, and the scheduled CI run:

```sh
git lfs pull
./.venv/bin/python -m pytest -q
```

## Extended Conversion Tests

These cover large or slower conversion, round-trip, Apple, Amiga fallback,
and sector-reconstruction cases:

```sh
./.venv/bin/python -m pytest -q -m extended
```

The extended GitHub job runs on the nightly schedule and can be started with
`workflow_dispatch`.

## Hardware Validation

Hardware tests are deliberately not part of hosted CI. A self-hosted machine
with a Greaseweazle and suitable drive can run them with:

```sh
./.venv/bin/python -m pytest -q -m hardware
```

The hardware tier must use disposable output paths and real media only with
explicit operator approval.

## Fixture Storage

Large SCP captures are stored with Git LFS using `.gitattributes`. New large
captures should be added to LFS deliberately; small deterministic fixtures can
remain ordinary Git files. The full fixture job checks out LFS content, while
fast PR jobs avoid downloading the full collection.
