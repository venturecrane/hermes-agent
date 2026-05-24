"""Tests for ``smd.hooks.audit_emission`` (ss-console#842).

The ss-console adapter types (``HookRegistry``, ``ToolCallContext``,
``ToolCallResult``, ``HookActionClass``) are not on ``PYTHONPATH`` in
this fork's CI. The tests inject a fake ``adapter.hermes_hook`` module
into ``sys.modules`` so the hook's lazy imports resolve against the
fake. This mirrors the dual-surface contract: the fork-side bridge
depends on the ss-console *shape*, not on the ss-console package being
installed.
"""

from __future__ import annotations

import asyncio
import importlib
import sys
import types
from dataclasses import dataclass, field
from typing import Any, Optional

import pytest


@dataclass
class _FakeHookActionClass:
    value: str


READ = _FakeHookActionClass("read")


@dataclass(frozen=True)
class _FakeContext:
    customer: str
    skill_name: str
    tool_name: str
    action_class: _FakeHookActionClass
    ceiling_level: str
    skill_version: Optional[str] = None
    matter_ref: Optional[str] = None
    trace_id: Optional[str] = None
    current_turn_approval: bool = False
    arguments: Optional[dict] = None


@dataclass(frozen=True)
class _FakeResult:
    outcome: str
    output_summary: Optional[str] = None
    error_type: Optional[str] = None
    duration_ms: Optional[float] = None


@dataclass
class _FakeRegistry:
    """In-test stand-in for ss-console's ``HookRegistry``."""

    calls: list[tuple[_FakeContext, _FakeResult]] = field(default_factory=list)

    async def dispatch_post_tool(self, ctx: _FakeContext, result: _FakeResult) -> None:
        self.calls.append((ctx, result))


def _install_fake_adapter_module() -> None:
    """Install a minimal ``adapter.hermes_hook`` module so the lazy
    imports inside the hook resolve. The module mirrors the ss-console
    shape used by ``audit_emission``."""
    fake_pkg = types.ModuleType("adapter")
    fake_mod = types.ModuleType("adapter.hermes_hook")

    class _ActionClassEnum:
        READ = READ

    fake_mod.HookActionClass = _ActionClassEnum  # type: ignore[attr-defined]
    fake_mod.ToolCallContext = _FakeContext  # type: ignore[attr-defined]
    fake_mod.ToolCallResult = _FakeResult  # type: ignore[attr-defined]

    sys.modules["adapter"] = fake_pkg
    sys.modules["adapter.hermes_hook"] = fake_mod


def _install_fake_plugin_manager() -> dict:
    """Install a minimal ``hermes_cli.plugins`` module that mirrors the
    real upstream surface so the test would catch the integration gap
    found in PRs #2/#3/#4. The real ``PluginManager`` exposes
    ``_hooks: dict[str, list[Callable]]`` (populated by
    ``PluginContext.register_hook`` internally); it does NOT have a
    public ``register_hook`` method of its own.

    ``state["manager"]`` is the fake manager (with ``_hooks`` dict).
    ``state["registered"]`` is a property-like view that flattens the
    current ``_hooks`` into a list of ``(hook_name, callback)`` tuples
    on each access, so test code stays the same shape it had before.
    """
    fake_pkg = types.ModuleType("hermes_cli")
    fake_mod = types.ModuleType("hermes_cli.plugins")

    class _FakeManager:
        # Mirrors the real PluginManager: a dict only; populated via
        # direct ``_hooks.setdefault(name, []).append(cb)`` (exactly
        # what PluginContext.register_hook does internally upstream).
        def __init__(self) -> None:
            self._hooks: dict[str, list[Any]] = {}

    manager = _FakeManager()

    class _RegisteredView:
        def __iter__(self):
            for name, cbs in manager._hooks.items():
                for cb in cbs:
                    yield (name, cb)

        def __len__(self):
            return sum(len(v) for v in manager._hooks.values())

        def __getitem__(self, idx):
            return list(self)[idx]

    state: dict = {"manager": manager, "registered": _RegisteredView()}

    fake_mod.get_plugin_manager = lambda: manager  # type: ignore[attr-defined]
    sys.modules.setdefault("hermes_cli", fake_pkg)
    sys.modules["hermes_cli.plugins"] = fake_mod
    return state


@pytest.fixture(autouse=True)
def _reset_module_state(monkeypatch):
    """Reload the hook between tests so its module-level state resets
    and the fakes above are picked up fresh."""
    _install_fake_adapter_module()
    state = _install_fake_plugin_manager()
    import smd.hooks.audit_emission as mod
    importlib.reload(mod)
    monkeypatch.setattr(mod, "_HOOK_REGISTERED", False, raising=False)
    monkeypatch.setattr(mod, "_REGISTRY_REF", None, raising=False)
    monkeypatch.setattr(mod, "_CUSTOMER_ID", "", raising=False)
    yield state
    sys.modules.pop("hermes_cli.plugins", None)
    sys.modules.pop("hermes_cli", None)
    sys.modules.pop("adapter.hermes_hook", None)
    sys.modules.pop("adapter", None)


def test_register_smd_adapter_installs_post_tool_hook(_reset_module_state):
    from smd.hooks import audit_emission

    registry = _FakeRegistry()
    audit_emission.register_smd_adapter(registry, customer_id="acme")

    registered = _reset_module_state["registered"]
    assert len(registered) == 1
    name, _cb = registered[0]
    assert name == "post_tool_call"


def test_post_tool_call_bridges_to_registry_dispatch(_reset_module_state):
    from smd.hooks import audit_emission

    registry = _FakeRegistry()
    audit_emission.register_smd_adapter(registry, customer_id="acme")
    name, callback = _reset_module_state["registered"][0]
    assert name == "post_tool_call"

    callback(
        tool_name="terminal",
        args={"command": "ls"},
        result='{"stdout": "hi"}',
        task_id="t1",
        session_id="s1",
        tool_call_id="tc1",
        duration_ms=12,
    )

    assert len(registry.calls) == 1
    ctx, res = registry.calls[0]
    assert ctx.customer == "acme"
    assert ctx.tool_name == "terminal"
    assert ctx.arguments == {"command": "ls"}
    assert res.outcome == "ok"
    assert res.duration_ms == 12.0


def test_post_tool_call_marks_error_outcome(_reset_module_state):
    from smd.hooks import audit_emission

    registry = _FakeRegistry()
    audit_emission.register_smd_adapter(registry, customer_id="acme")
    _name, callback = _reset_module_state["registered"][0]

    callback(
        tool_name="some_tool",
        args={},
        result='{"error": "boom"}',
        task_id="",
        session_id="",
        tool_call_id="",
        duration_ms=None,
    )

    assert len(registry.calls) == 1
    _ctx, res = registry.calls[0]
    assert res.outcome == "error"


def test_post_tool_call_swallows_registry_errors(_reset_module_state):
    """An audit-writer raising must not propagate; tool dispatch must
    not abort because audit emission failed."""
    from smd.hooks import audit_emission

    class _BoomRegistry:
        async def dispatch_post_tool(self, ctx, result):
            raise RuntimeError("audit writer down")

    registry = _BoomRegistry()
    audit_emission.register_smd_adapter(registry, customer_id="acme")
    _name, callback = _reset_module_state["registered"][0]

    callback(
        tool_name="x",
        args={},
        result="",
        task_id="",
        session_id="",
        tool_call_id="",
        duration_ms=0,
    )


def test_bridge_installs_on_real_plugin_manager_surface(_reset_module_state):
    """Regression guard for the integration gap fixed by this PR.

    The real upstream ``PluginManager`` exposes ``_hooks`` only; the
    public ``register_hook`` method lives on ``PluginContext``. Earlier
    versions of this bridge called ``manager.register_hook(...)``,
    which raised ``AttributeError`` at production boot and was masked
    in tests by fakes that exposed the wrong surface.

    This test asserts the bridge installs against a manager that ONLY
    has ``_hooks`` (no ``register_hook``), so a regression to the
    previous shape would fail here."""
    from smd.hooks import audit_emission

    manager = _reset_module_state["manager"]
    assert not hasattr(manager, "register_hook"), (
        "fake manager must mirror the real upstream surface "
        "(no register_hook method) for this regression guard to hold"
    )

    registry = _FakeRegistry()
    audit_emission.register_smd_adapter(registry, customer_id="acme")
    assert "post_tool_call" in manager._hooks
    assert len(manager._hooks["post_tool_call"]) == 1


def test_register_succeeds_without_adapter_module(monkeypatch):
    """Without ``adapter.hermes_hook`` importable, registration still
    succeeds (the bridge is installed; per-call payload building is a
    no-op until the ss-console types are present)."""
    sys.modules.pop("adapter.hermes_hook", None)
    sys.modules.pop("adapter", None)
    state = _install_fake_plugin_manager()
    import smd.hooks.audit_emission as mod
    importlib.reload(mod)

    registry = _FakeRegistry()
    mod.register_smd_adapter(registry, customer_id="acme")
    _name, callback = state["registered"][-1]
    callback(
        tool_name="x",
        args={},
        result="",
        task_id="",
        session_id="",
        tool_call_id="",
        duration_ms=0,
    )
    assert registry.calls == []
    sys.modules.pop("hermes_cli.plugins", None)
    sys.modules.pop("hermes_cli", None)
