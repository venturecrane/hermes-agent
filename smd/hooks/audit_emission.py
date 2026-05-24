"""Per-tool audit emission hook surface.

Tracks: venturecrane/ss-console#842

Every tool dispatch must emit ``timestamp, customer, skill, tool,
action class, ceiling decision, outcome`` to the per-customer D1 audit
table at <5ms p99 overhead. This module exposes the dispatch-time
emission point the SMD adapter registers against.

Status: scaffold only. Implementation follows in the issue above.
"""

from __future__ import annotations

from typing import Any


def register_smd_adapter(*args: Any, **kwargs: Any) -> None:
    """Register the SMD audit-emission hook with the overlay.

    TODO(ss-console#842): wire the dispatch-time emission point. The
    adapter side calls this at boot with the audit writer it has
    constructed; this function attaches it to the Hermes dispatch loop
    so every tool call routes through it.
    """
    raise NotImplementedError(
        "smd.hooks.audit_emission.register_smd_adapter is a scaffold; "
        "implementation tracked in venturecrane/ss-console#842"
    )
