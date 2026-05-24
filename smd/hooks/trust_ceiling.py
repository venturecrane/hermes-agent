"""Trust-ceiling enforce/refuse hook surface.

Tracks: Platform PRD §7.5 invariants #1, #2, #5
(ss-console #864, #948, #953 shipped the substrate side; this module
is the Hermes-side bridge that turns the substrate's enforce/refuse
decisions into real dispatch outcomes).

Bridges the ss-console-owned ``HookRegistry`` into Hermes' upstream
``pre_tool_call`` plugin hook. On every tool dispatch the bridge calls
``registry.dispatch_pre_tool(ctx)``; when ss-console's enforcer raises
``BlockedToolCall``, the bridge fires the refusal hook to emit the
customer notification + Captain cascade row, then returns a block
directive to halt the upstream dispatch path.

Coexistence with sticky_stop
----------------------------

Both hooks bridge through ``pre_tool_call``. Upstream Hermes'
``get_pre_tool_call_block_message`` takes the FIRST block directive
that wins, so the two hooks are independent — either can block on its
own. Sticky-stop checks a pinned-slot flag; trust-ceiling runs the
substrate enforcer. There is no ordering coupling.

Failure mode
------------

The substrate-side enforcer (``DefaultTrustCeilingEnforcer``) is the
authoritative answer for whether a tool call is permitted. If the
bridge cannot reach the enforcer (registry missing, adapter types not
on PYTHONPATH, async-run failure), the bridge fails closed only for
substrate errors that look like enforcement decisions; everything
else falls through to allow. Per PR #948's invariant: "a state the
substrate cannot enforce is a state the agent does not run" — but
that invariant applies to the substrate side, not the bridge. A
bridge that fails open is a bug, but a bridge that fails closed on
infrastructure noise would brick every dispatch the moment the
adapter package goes missing in dev. The chosen middle: substrate
``BlockedToolCall`` → block; everything else → allow + log.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Optional

log = logging.getLogger("smd.hooks.trust_ceiling")


_HOOK_REGISTERED = False
_REGISTRY_REF: Any = None
_CUSTOMER_ID: str = ""

_FALLBACK_BLOCK_MESSAGE = (
    "This action requires reviewer approval before it can proceed."
)


def _build_context(tool_name: str, args: Any, customer_id: str) -> Any:
    """Construct a ``ToolCallContext`` from upstream-call kwargs.

    Lazy-imports the ss-console types so this module remains importable
    when the ``adapter`` package is not on ``PYTHONPATH``. Returns
    ``None`` if the types cannot be imported — the caller treats that
    as "skip the bridge, allow the call."

    The bridge sets ``action_class=DESTRUCTIVE`` as a conservative
    default: the upstream Hermes runtime does not pre-classify tool
    calls, and the ss-console enforcer expects a closed-set action
    class. ``DESTRUCTIVE`` is the strictest class; any per-tool
    refinement (e.g., classifying ``terminal`` as DESTRUCTIVE vs
    classifying ``read_file`` as READ) happens in the substrate-side
    enforcer or in a per-customer policy override — not here. Erring
    on the strict side means the enforcer is given the maximum
    opportunity to refuse; it can downgrade if the per-tool policy
    permits.

    NOTE: the ceiling_level is taken from the registry's pinned-slot
    table when present; otherwise it defaults to ``draft_for_review``
    (the safety-floor ceiling per PRD §7.5).
    """
    try:
        from adapter.hermes_hook import HookActionClass, ToolCallContext
    except ImportError:
        return None

    ceiling = "draft_for_review"
    try:
        slots = getattr(_REGISTRY_REF, "pinned_slots", None)
        if slots is not None:
            pinned = slots.get("trust_ceiling.locked_skills")
            if pinned:
                ceiling = str(pinned)
    except Exception:
        pass

    return ToolCallContext(
        customer=customer_id,
        skill_name="",
        tool_name=tool_name,
        action_class=HookActionClass.DESTRUCTIVE,
        ceiling_level=ceiling,
        arguments=args if isinstance(args, dict) else None,
    )


def _blocked_tool_call_class() -> Optional[type]:
    """Lazy-import ``BlockedToolCall`` so the bridge can ``except`` it."""
    try:
        from adapter.hermes_hook import BlockedToolCall
        return BlockedToolCall
    except ImportError:
        return None


def _run_async(coro: Any) -> Any:
    """Run an async coroutine to completion from a sync caller.

    Same async-from-sync pattern as ``audit_emission`` (upstream
    ``invoke_hook`` callbacks are sync; ss-console hooks are async).
    Re-raises any exception the coroutine raised so the caller can
    distinguish substrate refusals (``BlockedToolCall``) from
    infrastructure noise.
    """
    try:
        return asyncio.run(coro)
    except RuntimeError:
        pass

    result_box: list[Any] = []
    exc_box: list[BaseException] = []

    def _runner() -> None:
        try:
            result_box.append(asyncio.run(coro))
        except BaseException as exc:
            exc_box.append(exc)

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    t.join()
    if exc_box:
        raise exc_box[0]
    return result_box[0] if result_box else None


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

    Runs the ss-console pre-tool enforcer via
    ``registry.dispatch_pre_tool(ctx)``. On ``BlockedToolCall`` from
    the substrate, fires the refusal hook (customer notification +
    Captain cascade) and returns a block directive. On any other
    failure, allows the call and logs — the substrate is the
    authoritative answer and the bridge does not invent refusals.
    """
    if _REGISTRY_REF is None:
        return None

    ctx = _build_context(tool_name, args, _CUSTOMER_ID)
    if ctx is None:
        return None

    blocked_cls = _blocked_tool_call_class()
    if blocked_cls is None:
        return None

    try:
        _run_async(_REGISTRY_REF.dispatch_pre_tool(ctx))
    except blocked_cls as block:
        try:
            _run_async(_REGISTRY_REF.dispatch_refusal(ctx, block))
        except Exception as refusal_exc:
            log.warning(
                "trust_ceiling: refusal hook raised after block for tool=%s: %s",
                tool_name,
                refusal_exc,
            )
        message = getattr(block, "customer_message", None) or _FALLBACK_BLOCK_MESSAGE
        log.warning(
            "trust_ceiling: block for tool=%s customer=%s reason=%s",
            tool_name,
            _CUSTOMER_ID,
            getattr(block, "reason", "(unknown)"),
        )
        return {"action": "block", "message": message}
    except Exception as exc:
        log.warning(
            "trust_ceiling: dispatch_pre_tool raised non-Block exception "
            "for tool=%s (allowing call): %s",
            tool_name,
            exc,
        )
        return None

    return None


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
    upstream callback.

    Args:
        registry: ss-console ``HookRegistry`` whose ``dispatch_pre_tool``
            runs the trust-ceiling enforcer and whose
            ``dispatch_refusal`` fires the customer notification +
            Captain cascade.
        customer_id: customer slug from ``customer.yaml``; carried into
            every ``ToolCallContext`` and into log lines.
    """
    global _HOOK_REGISTERED, _REGISTRY_REF, _CUSTOMER_ID
    _REGISTRY_REF = registry
    _CUSTOMER_ID = customer_id or ""

    if _HOOK_REGISTERED:
        log.info(
            "trust_ceiling: registry rebound (customer_id=%s); "
            "upstream callback already installed",
            _CUSTOMER_ID,
        )
        return

    try:
        from hermes_cli.plugins import get_plugin_manager
    except ImportError:
        log.warning(
            "trust_ceiling: hermes_cli.plugins unavailable; "
            "pre_tool_call bridge not installed"
        )
        return

    get_plugin_manager().register_hook("pre_tool_call", _on_pre_tool_call)
    _HOOK_REGISTERED = True
    log.info(
        "trust_ceiling: registered pre_tool_call bridge for customer_id=%s",
        _CUSTOMER_ID,
    )
