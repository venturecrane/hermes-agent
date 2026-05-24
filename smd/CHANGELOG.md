# SMD Overlay Changelog

This changelog tracks SMD-specific changes to the `venturecrane/hermes-agent`
fork. Upstream Hermes release notes live in the repo root (`RELEASE_v*.md`).

## 2026-05-23 — Initial fork at upstream `v2026.5.16`

Overlay scaffolding only. No SMD-specific runtime code yet.

Added:

- `smd/` overlay package with `smd/hooks/` submodules for the four named
  hook surfaces called out in ADR 0015 (audit emission, sticky-stop
  dispatch interception, trust-ceiling enforce/refuse, capability-adapter
  registration). Each module exposes a `register_smd_adapter` stub
  raising `NotImplementedError`; live implementations follow per-issue.
- `smd/CHANGELOG.md` (this file).
- `smd/README.md` describing the overlay's purpose, layout, and the
  fork-tag scheme.
- `.github/workflows/smd-overlay-tests.yml` running `smd/tests/`.
- `.github/workflows/upstream-sync-check.yml` flagging the fork when it
  is more than 90 days behind upstream `main`.
- Top-of-`README.md` banner identifying this checkout as a vendored
  SMD fork per ADR 0015.

Tagged as `v2026.5.16-smd.0` per the fork-tag scheme
(`v{upstream}-smd.{n}`) defined in ADR 0015 and elaborated in
`smd/README.md`. The `-smd.0` suffix means "fork exists but no SMD
runtime code yet."
