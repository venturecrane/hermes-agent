# SMD overlay tests

Tests for the `smd/` overlay layer only. Upstream tests live under the
repo root `tests/` directory and are run by upstream's `tests.yml` CI.

The `smd-overlay-tests.yml` workflow runs `pytest smd/tests/`. When no
tests exist yet (the scaffolding-only initial state), pytest exits 5
("no tests collected") and the workflow treats that as success.

Add tests here alongside each `smd/hooks/<surface>.py` implementation as
those land per the issues called out in `smd/README.md`.
