# SMD Fork Policy

This is **SMD Services'** downstream fork of [`NousResearch/hermes-agent`](https://github.com/NousResearch/hermes-agent) (MIT-licensed). The fork exists for **traceability and security-patch escape only**. SMD-specific code lives in the [`venturecrane/hermes-smd-overlay`](https://github.com/venturecrane/hermes-smd-overlay) plugin overlay, **not in this fork**.

## Pin policy

Per [ADR 0015 (rewritten)](https://github.com/venturecrane/ss-console/blob/main/docs/adr/0015-hermes-fork-vs-upstream.md):

- **No core-file modifications, ever**, under normal operation. The fork's tags promote upstream tags as-is.
- **Tag scheme:** `v{upstream}-smd.{n}` for normal pins (e.g., `v2026.5.16-smd.0` carries zero patches relative to upstream `v2026.5.16`).
- **Customer Machine builds reference the SMD-tagged ref**, and CI on the image build asserts the installed Hermes commit SHA matches the upstream commit SHA at the corresponding upstream tag. Any divergence fails the build.

## Security-patch escape valve

The "no patches, ever" rule applies to normal operation. If upstream has not shipped a fix for a CVE that materially affects customer Machines, Captain may authorize a vendored emergency patch under a tag pattern:

```
v{upstream}-smd.security.{n}
```

For example: `v2026.5.16-smd.security.0` for the first emergency patch on top of `v2026.5.16`.

The emergency patch is documented in `SMD_CHANGELOG.md` with:

1. The CVE reference (CVE-YYYY-NNNNN).
2. The upstream issue/PR (if filed).
3. **A forced removal date** — emergency patches must be either upstream-merged or have a clear retirement plan within 30 days.

Capabilities outside Captain do not bypass the no-patches CI rule; the security tag pattern is the only legitimate path.

## Rebase posture

- Upstream releases are tracked via `git remote add upstream https://github.com/NousResearch/hermes-agent.git`.
- New upstream tag → fetch + merge to fork `main` → tag `v{upstream}-smd.0`.
- No patches sit between upstream and the fork tag under normal operation.

## customer.yaml.hermes_ref validation

The per-customer `customer.yaml.hermes_ref` field pins which Hermes ref a customer Machine boots. The validator enforces the SMD tag shape:

```
^v\d{4}\.\d{1,2}\.\d{1,2}-smd\.(security\.)?\d+$
```

Valid examples:

- `v2026.5.16-smd.0` (normal pin, zero patches)
- `v2026.5.16-smd.1` (normal pin with a fork-side commit; reserved future use)
- `v2026.5.16-smd.security.0` (emergency security patch)

## Upstream contribution

The fork is also the contribution vehicle for genuinely-upstream PRs that originate from SMD work. When SMD has something worth contributing back to Hermes:

1. Branch off `upstream/main` (not fork `main`).
2. Cherry-pick or rewrite the contribution to be upstream-acceptable.
3. Open the PR against `NousResearch/hermes-agent`.
4. Track the PR in `SMD_CHANGELOG.md` once it lands upstream.

## Historical note (this fork's `main` branch)

Prior to the May 2026 architectural realignment, this fork's `main` branch carried experimental `smd/hooks/*` modules (PRs #4, #5, #6, etc.). Those modules implemented the fork-side overlay surface from the original [ADR 0015 v1](https://github.com/venturecrane/ss-console/blob/main/docs/adr/0015-hermes-fork-vs-upstream.md). The rewritten ADR 0015 supersedes that approach: SMD plugins now attach to Hermes' native plugin hook surface (verified at [`hermes_cli/plugins.py:128-168`](https://github.com/NousResearch/hermes-agent/blob/v2026.5.16/hermes_cli/plugins.py)) via the [`venturecrane/hermes-smd-overlay`](https://github.com/venturecrane/hermes-smd-overlay) plugin overlay.

The `v2026.5.16-smd.0` tag does NOT point at fork `main` (which carries the legacy `smd/hooks/*` commits); it points at the upstream commit `a91a57fa5a13d516c38b07a141a9ce8a3daabeb0`. That tag is the architectural ground truth for the realigned posture.

A follow-on Captain-approved cleanup will reset fork `main` to track upstream once Captain authorizes the destructive operation (force-push). Until then, `main` carries history; the tag carries truth.

## References

- ss-console: [`docs/adr/0015-hermes-fork-vs-upstream.md`](https://github.com/venturecrane/ss-console/blob/main/docs/adr/0015-hermes-fork-vs-upstream.md)
- ss-console: [`docs/adr/0007-per-customer-machine-isolation.md`](https://github.com/venturecrane/ss-console/blob/main/docs/adr/0007-per-customer-machine-isolation.md)
- Overlay repo: [`venturecrane/hermes-smd-overlay`](https://github.com/venturecrane/hermes-smd-overlay)
- Hermes plugin hook surface citation: [`hermes-smd-overlay/docs/hook-surface.md`](https://github.com/venturecrane/hermes-smd-overlay/blob/main/docs/hook-surface.md)
