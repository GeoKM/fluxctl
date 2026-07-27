# Fixtures

Test fixtures mirror media types in `tests/fixtures/`. Synthetic fixtures are small to keep CI fast. New fixtures should follow the existing directory naming (e.g., `3.5inch/IBM`).

Fixture filenames are descriptive only. Detection tests must assert behavior from
decoded layout, sector geometry, and filesystem structures rather than from words
embedded in the path.
