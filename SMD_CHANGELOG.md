# SMD Changelog

Tracks SMD-specific events on this fork: pin promotions, emergency security patches (under `v{upstream}-smd.security.{n}` tags), and any genuinely-upstream contributions originating from SMD work.

For the pin policy and the rationale behind the tag scheme, see [`SMD_FORK_POLICY.md`](SMD_FORK_POLICY.md).

## Tag history

### `v2026.5.16-smd.0` — 2026-05-24

Initial SMD pin under the realigned ADR 0015 posture. Points at upstream commit `a91a57fa5a13d516c38b07a141a9ce8a3daabeb0` (upstream `v2026.5.16`). Zero patches relative to upstream.

The `.smd.0` suffix establishes the tag-promotion scheme for future pins.

## Upstream contributions

_None yet._ When SMD contributes work to `NousResearch/hermes-agent`, the contribution is tracked here once it lands upstream.

## Emergency security patches

_None._ Per `SMD_FORK_POLICY.md`, emergency patches under `v{upstream}-smd.security.{n}` tags must be documented here with the CVE reference, upstream issue/PR, and a forced removal date.
