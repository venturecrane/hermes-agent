"""Sticky-stop dispatch interception hook surface.

Tracks: venturecrane/ss-console#843 (the sticky-stop state machine
shipped in ss-console PR #948; this module is the Hermes-side hook
surface that turns the machine's HARD_STOP signal into a real dispatch
intercept).

Bridges the ss-console-owned ``HookRegistry`` into Hermes' upstream
``pre_tool_call`` plugin hook. On every tool dispatch the bridge reads
``registry.pinned_slots.get("sticky_stop.active")`` and, when the
value indicates ``HARD_STOP``, returns a block directive that halts
the dispatch path.

Why the pinned-slot table
-------------------------

The sticky-stop state machine persists per ``(customer, persona)``
to a D1 table (ss-console PR #948). The runtime view of "is this
agent currently stopped?" lives in the registry's
``PinnedSlots.sticky_stop.active`` slot per safety invariant #4
("don't act" survives compaction). The ss-console adapter side is
responsible for keeping that slot synchronized with the machine; the
fork-side bridge is responsible for honoring it on every dispatch.

State vocabulary
----------------

``sticky_stop.active`` carries one of the ``StickyStopLevel`` string
values: ``OK``, ``WARN``, ``SOFT_STOP``, ``HARD_STOP``. The bridge
blocks only on ``HARD_STOP`` — SOFT_STOP is handled by the
trust-ceiling enforcer (it clamps every skill to ``draft_for_review``
for the duration), and WARN is observational. Missing or empty slot
means "not stopped, allow."

HARD_STOP semantics
-------------------

Per PR #948: "the caller receives a StickyStopError it must
propagate, NOT swallow. Same invariant as the audit log writer: a
state the substrate cannot enforce is a state the agent does not
run." Returning a ``{"action": "block"}`` from the upstream pre-tool
hook is how that propagation lands in Hermes.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger("smd.hooks.sticky_stop")


_HOOK_REGISTERED = False
_REGISTRY_REF: Any = None
_CUSTOMER_ID: str = ""

# The closed-set sticky-stop level that triggers a dispatch block.
# WARN and SOFT_STOP are intentionally allowed through (WARN is
# observational; SOFT_STOP is clamped by trust-ceiling, not blocked).
_BLOCK_LEVELS = frozenset({"HARD_STOP"})

_BLOCK_MESSAGE = (
    "Sticky-stop HARD_STOP active for this AI Employee. "
    "No further tool calls until Captain investigation clears the state."
)


def _pinned_slot(registry: Any, key: str) -> Optional[str]:
    """Best-effort read of one pinned-slot value.

    Returns ``None`` if the registry shape is unfamiliar or the slot
    is unset. Never raises — the slot read runs on the hot dispatch
    path and a malformed pinned-slot table must not abort tool
    execution.
    """
    try:
        slots = getattr(registry, "pinned_slots", None)
        if slots is None:
            return None
        value = slots.get(key)
        if value is None:
            return None
        return str(value)
    except Exception as exc:
        log.warning("sticky_stop: pinned-slot read failed for key=%s: %s", key, exc)
        return None


def _on_pre_tool_call(
    *,
    tool_name: str = "",
    args: Any = None,
    task_id: str = "",
    session_id: str = "",
    tool_call_id: str = "",
    **_: Any,
) -> Optional[dict]:
    """Upstream ``pre_tool_call`` callback.

    Returns ``{"action": "block", "message": ...}`` when the
    registry's ``sticky_stop.active`` pinned slot indicates
    ``HARD_STOP``; returns ``None`` otherwise so dispatch continues.
    """
    if _REGISTRY_REF is None:
        return None

    level = _pinned_slot(_REGISTRY_REF, "sticky_stop.active")
    if level is None:
        return None

    normalized = level.upper().strip()
    if normalized not in _BLOCK_LEVELS:
        return None

    log.warning(
        "sticky_stop: HARD_STOP intercept for tool=%s customer=%s",
        tool_name,
        _CUSTOMER_ID,
    )
    return {"action": "block", "message": _BLOCK_MESSAGE}


def _install_upstream_hook(manager: Any, hook_name: str, callback: Any) -> bool:
    """Install a callback against the Hermes upstream ``PluginManager``.

    Same shape as the helper in ``smd.hooks.audit_emission``: the
    public ``register_hook`` method on ``hermes_cli.plugins`` is
    defined on ``PluginContext`` (the facade passed to plugin
    ``register(ctx)`` functions), not on ``PluginManager`` itself.
    Overlay code does not go through plugin discovery and has no
    ``PluginContext``, so it cannot use the public method. The fallback
    touches ``manager._hooks`` directly — exactly what
    ``PluginContext.register_hook`` does internally
    (upstream ``hermes_cli/plugins.py``).
    """
    public_register = getattr(manager, "register_hook", None)
    if callable(public_register):
        try:
            public_register(hook_name, callback)
            return True
        except Exception as exc:
            log.warning(
                "register_hook(%r) raised on %s; falling back to "
                "_hooks dict: %s",
                hook_name,
                type(manager).__name__,
                exc,
            )

    hooks = getattr(manager, "_hooks", None)
    if isinstance(hooks, dict):
        hooks.setdefault(hook_name, []).append(callback)
        return True

    return False


def register_smd_adapter(
    registry: Any,
    *,
    customer_id: str = "",
    **_: Any,
) -> None:
    """Wire the ss-console ``HookRegistry`` to upstream ``pre_tool_call``.

    Called exactly once at Machine boot by ``aie_adapter.register()``.
    The registration is idempotent on this module — a second call
    swaps the active registry but does not double-register the
    callback against the upstream plugin manager.

    Args:
        registry: ss-console ``HookRegistry`` whose ``pinned_slots``
            carry the live sticky-stop state under the
            ``sticky_stop.active`` key.
        customer_id: customer slug from ``customer.yaml``; carried
            into log lines for cross-row correlation.
    """
    global _HOOK_REGISTERED, _REGISTRY_REF, _CUSTOMER_ID
    _REGISTRY_REF = registry
    _CUSTOMER_ID = customer_id or ""

    if _HOOK_REGISTERED:
        log.info(
            "sticky_stop: registry rebound (customer_id=%s); "
            "upstream callback already installed",
            _CUSTOMER_ID,
        )
        return

    try:
        from hermes_cli.plugins import get_plugin_manager
    except ImportError:
        log.warning(
            "sticky_stop: hermes_cli.plugins unavailable; "
            "pre_tool_call bridge not installed"
        )
        return

    if not _install_upstream_hook(
        get_plugin_manager(), "pre_tool_call", _on_pre_tool_call
    ):
        log.warning(
            "sticky_stop: could not install pre_tool_call bridge "
            "(no compatible PluginManager surface)"
        )
        return

    _HOOK_REGISTERED = True
    log.info(
        "sticky_stop: registered pre_tool_call bridge for customer_id=%s",
        _CUSTOMER_ID,
    )
