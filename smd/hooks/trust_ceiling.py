"""Trust-ceiling enforce/refuse hook surface.

Tracks: Platform PRD §7.5 invariants #1, #2, #5
(live work on ss-console main per #864, #948, #953).

Tool calls must be intercepted, classified, and either allowed,
draft-routed, or refused before execution. This module exposes the wrap
point inside the tool dispatch loop where the SMD adapter's enforcer
runs.

Status: scaffold only. Implementation follows in the issues above.
"""

from __future__ import annotations

from typing import Any


def register_smd_adapter(*args: Any, **kwargs: Any) -> None:
    """Register the SMD trust-ceiling enforcer with the overlay.

    TODO(ss-console#864, #948, #953): wire the pre-dispatch enforcer
    such that every tool call routes through ``trust_ceiling.enforce()``
    and BlockedToolCall surfaces propagate to the refusal hook.
    """
    raise NotImplementedError(
        "smd.hooks.trust_ceiling.register_smd_adapter is a scaffold; "
        "implementation tracked in venturecrane/ss-console#864, #948, #953"
    )
