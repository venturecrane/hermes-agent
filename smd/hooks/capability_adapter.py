"""Capability-adapter registration hook surface.

Tracks: ADR 0006 (Capability-Adapter Pattern), ss-console
docs/adr/0006-capability-adapter-pattern.md.

Skills bind to abstract capability interfaces; vendor adapters implement
them; ``customer.yaml`` wires them. The wiring happens at Machine boot
through Hermes' adapter loader. This module exposes the registration
point the SMD adapter calls during boot to install the vendor-specific
adapter implementations declared in customer.yaml.

Status: scaffold only. Implementation follows in the ADR 0006 build-out
work stream.
"""

from __future__ import annotations

from typing import Any


def register_smd_adapter(*args: Any, **kwargs: Any) -> None:
    """Register SMD capability adapters with the overlay.

    TODO(ADR 0006 follow-on): wire the per-capability adapter
    registration so customer.yaml's capability bindings produce a live
    adapter map at boot.
    """
    raise NotImplementedError(
        "smd.hooks.capability_adapter.register_smd_adapter is a scaffold; "
        "implementation tracked in ADR 0006 follow-on work"
    )
