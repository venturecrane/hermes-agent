"""Sticky-stop dispatch interception hook surface.

Tracks: venturecrane/ss-console#843 (closed; state machine integrated
SMD-side, Hermes-side hook surface still pending).

The sticky-stop state machine has already landed on ss-console main.
This module exposes the Hermes-side hook surface so the soft-stop and
hard-stop transitions actually intercept dispatch inside the runtime.

Status: scaffold only. Implementation follows in the issue above.
"""

from __future__ import annotations

from typing import Any


def register_smd_adapter(*args: Any, **kwargs: Any) -> None:
    """Register the SMD sticky-stop hook with the overlay.

    TODO(ss-console#843 follow-on): wire the dispatch interception so
    transitions in the SMD-side state machine actually halt tool
    execution in the runtime.
    """
    raise NotImplementedError(
        "smd.hooks.sticky_stop.register_smd_adapter is a scaffold; "
        "implementation tracked in venturecrane/ss-console#843 follow-on"
    )
