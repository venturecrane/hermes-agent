"""Per-tool audit emission hook surface.

Tracks: venturecrane/ss-console#842

Bridges the ss-console-owned ``HookRegistry`` into Hermes' upstream
``post_tool_call`` plugin hook so every tool dispatch produces one
audit row via the registry's ``dispatch_post_tool`` slot.

Loading
-------

``aie_adapter.register()`` constructs a ``HookRegistry`` (defined in
``ai-employee/adapter/hermes_hook.py``), installs the safety-substrate
hooks against it, and then calls ``register_smd_adapter(registry,
customer_id=...)`` on this module. The registration here is what makes
the in-tree post-tool hook actually fire on real tool dispatches inside
the Hermes runtime.

Hook bridging
-------------

Upstream Hermes invokes ``post_tool_call`` from ``model_tools.py`` with
kwargs ``(tool_name, args, result, task_id, session_id, tool_call_id,
duration_ms)``. The ss-console ``PostToolHook`` signature wants
``(ToolCallContext, ToolCallResult)`` and is ``async``. The bridge
adapts the upstream call shape into the ss-console shape and runs the
async hook synchronously via a private event loop so the upstream
``invoke_hook`` callback contract stays sync.

Failure mode is best-effort: any exception in the bridge is caught and
logged. The upstream ``invoke_hook`` already wraps each callback in a
try/except for the same reason — the audit writer raising must not
break tool dispatch. The safety-substrate's runtime-enforcement
invariant lives on the pre-tool side (trust-ceiling + sticky-stop);
post-tool emission is observational.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Optional

log = logging.getLogger("smd.hooks.audit_emission")


_HOOK_REGISTERED = False
_REGISTRY_REF: Any = None
_CUSTOMER_ID: str = ""


def _classify_outcome(result: Any) -> str:
    """Map an upstream tool result into the ss-console ``outcome`` vocabulary.

    The ``ToolCallResult.outcome`` field is closed-set: ``"ok" | "error"
    | "blocked"``. Upstream Hermes returns a JSON-encoded string from
    ``registry.dispatch`` for ok and most error paths, and the
    ``pre_tool_call`` block path short-circuits before this hook runs.
    A JSON payload with an ``error`` key signals a tool-level failure.
    """
    if not isinstance(result, str):
        return "ok"
    if '"error"' in result and result.startswith("{"):
        return "error"
    return "ok"


def _build_context(
    tool_name: str,
    args: Any,
    customer_id: str,
) -> Any:
    """Construct a ``ToolCallContext`` from upstream-call kwargs.

    Lazy-imports the ss-console types so this module is importable in
    environments where the ``adapter`` package is not on ``PYTHONPATH``
    (CI for the fork itself, for example). Returns ``None`` if the
    types cannot be imported — the caller treats that as a no-op.
    """
    try:
        from adapter.hermes_hook import HookActionClass, ToolCallContext
    except ImportError:
        return None

    return ToolCallContext(
        customer=customer_id,
        skill_name="",
        tool_name=tool_name,
        action_class=HookActionClass.READ,
        ceiling_level="draft_for_review",
        arguments=args if isinstance(args, dict) else None,
    )


def _build_result(result: Any, duration_ms: Optional[int]) -> Any:
    """Construct a ``ToolCallResult`` from upstream-call kwargs."""
    try:
        from adapter.hermes_hook import ToolCallResult
    except ImportError:
        return None

    outcome = _classify_outcome(result)
    summary: Optional[str] = None
    if isinstance(result, str) and len(result) > 0:
        summary = result[:200]

    return ToolCallResult(
        outcome=outcome,
        output_summary=summary,
        duration_ms=float(duration_ms) if duration_ms is not None else None,
    )


def _run_async(coro: Any) -> None:
    """Run an async coroutine to completion from a sync caller.

    Upstream ``invoke_hook`` calls each registered callback synchronously
    in the dispatch thread. The ss-console ``PostToolHook`` is async.
    Running ``asyncio.run`` on a thread that already has a running loop
    (gateway / ACP paths sometimes do) raises ``RuntimeError``; the
    fallback runs the coroutine on a fresh background thread with its
    own loop.
    """
    try:
        asyncio.run(coro)
        return
    except RuntimeError:
        pass

    exc_box: list[BaseException] = []

    def _runner() -> None:
        try:
            asyncio.run(coro)
        except BaseException as exc:
            exc_box.append(exc)

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    t.join()
    if exc_box:
        raise exc_box[0]


def _on_post_tool_call(
    *,
    tool_name: str = "",
    args: Any = None,
    result: Any = None,
    task_id: str = "",
    session_id: str = "",
    tool_call_id: str = "",
    duration_ms: Optional[int] = None,
    **_: Any,
) -> None:
    """Upstream ``post_tool_call`` callback.

    Builds the ss-console hook payload and invokes
    ``registry.dispatch_post_tool``. Errors here never propagate — the
    audit emission is observational and must not abort dispatch.
    """
    if _REGISTRY_REF is None:
        return

    ctx = _build_context(tool_name, args, _CUSTOMER_ID)
    res = _build_result(result, duration_ms)
    if ctx is None or res is None:
        return

    try:
        _run_async(_REGISTRY_REF.dispatch_post_tool(ctx, res))
    except Exception as exc:
        log.warning(
            "audit_emission: dispatch_post_tool raised for tool=%s: %s",
            tool_name,
            exc,
        )


def register_smd_adapter(
    registry: Any,
    *,
    customer_id: str = "",
    **_: Any,
) -> None:
    """Wire the ss-console ``HookRegistry`` to upstream ``post_tool_call``.

    Called exactly once at Machine boot by ``aie_adapter.register()``.
    The registration is idempotent on this module — a second call swaps
    the active registry but does not double-register the callback.

    Args:
        registry: ss-console ``HookRegistry`` instance (from
            ``ai-employee/adapter/hermes_hook.py``).
        customer_id: customer slug from ``customer.yaml``; carried into
            every ``ToolCallContext``.
    """
    global _HOOK_REGISTERED, _REGISTRY_REF, _CUSTOMER_ID
    _REGISTRY_REF = registry
    _CUSTOMER_ID = customer_id or ""

    if _HOOK_REGISTERED:
        log.info(
            "audit_emission: registry rebound (customer_id=%s); "
            "upstream callback already installed",
            _CUSTOMER_ID,
        )
        return

    try:
        from hermes_cli.plugins import get_plugin_manager
    except ImportError:
        log.warning(
            "audit_emission: hermes_cli.plugins unavailable; "
            "post_tool_call bridge not installed"
        )
        return

    get_plugin_manager().register_hook("post_tool_call", _on_post_tool_call)
    _HOOK_REGISTERED = True
    log.info(
        "audit_emission: registered post_tool_call bridge for customer_id=%s",
        _CUSTOMER_ID,
    )
