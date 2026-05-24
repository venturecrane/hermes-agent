# SMD Overlay

This directory is the SMD overlay layer for the
`venturecrane/hermes-agent` fork of
[`NousResearch/hermes-agent`](https://github.com/NousResearch/hermes-agent).

It exists per
[ADR 0015 in ss-console](https://github.com/venturecrane/ss-console/blob/main/docs/adr/0015-hermes-fork-vs-upstream.md):
**thin vendored fork with upstream contribution**. Upstream files stay
unmodified where the overlay can absorb the change; the overlay holds
SMD-specific safety-substrate hooks.

## Layout

```
smd/
├── __init__.py                 package marker
├── README.md                   this file
├── CHANGELOG.md                SMD-specific change history
├── hooks/                      hook surfaces the SMD adapter binds against
│   ├── __init__.py
│   ├── audit_emission.py       per-tool audit emission (ss-console#842)
│   ├── sticky_stop.py          sticky-stop dispatch interception (ss-console#843)
│   ├── trust_ceiling.py        trust-ceiling enforce/refuse (PRD §7.5)
│   └── capability_adapter.py   capability-adapter registration (ADR 0006)
└── tests/                      overlay-only tests; runs in smd-overlay-tests CI
```

## Adapter contract

The SMD adapter in
[`ss-console/ai-employee/adapter/aie_adapter.py`](https://github.com/venturecrane/ss-console/blob/main/ai-employee/adapter/aie_adapter.py)
calls `smd.hooks.<surface>.register_smd_adapter(...)` for each of the
four hook surfaces at Hermes boot. The adapter side does not depend on
this overlay's internal layout — only on the `register_smd_adapter`
contract exposed by each hook module.

## Fork-tag scheme

Tags on this fork follow the pattern `v{upstream}-smd.{n}`:

- `{upstream}` is the upstream Hermes tag this fork rebased onto
  (date-based, e.g. `v2026.5.16`).
- `{n}` is the SMD revision counter, starting at `0` for "fork exists,
  no SMD runtime code yet" and incrementing on every SMD-side change.

Examples:

- `v2026.5.16-smd.0` — initial fork at upstream `v2026.5.16`, overlay
  scaffolding only.
- `v2026.5.16-smd.1` — first SMD runtime change (e.g. audit-emission
  hook implementation) on top of the same upstream ref.
- `v2026.6.4-smd.0` — after the quarterly upstream rebase, before any
  SMD-side change.

`customer.yaml`'s `hermes_ref` field must pin a fork tag (this pattern),
not a bare upstream tag. The ss-console validator enforces this; see
[ADR 0015 §Decision](https://github.com/venturecrane/ss-console/blob/main/docs/adr/0015-hermes-fork-vs-upstream.md).

## Upstream contribution

Per ADR 0015 §Decision: SMD-specific code that is genuinely
generalizable (a tool-dispatch hook API, an adapter-side audit emission
point) goes upstream as PRs within one week of landing in the SMD fork.
SMD-specific code that is genuinely SMD-specific
(capability-adapter registration, customer.yaml wiring, per-customer
Machine boot invariants) stays here in the overlay.

When the overlay layer is insufficient and SMD must modify
upstream-owned files directly, the modification is kept minimal,
documented per-change in `CHANGELOG.md`, and filed as an upstream PR
against `NousResearch/hermes-agent` within one week.
