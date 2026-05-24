"""Tests for ``smd.hooks.sticky_stop`` (ss-console#843, PR #948).

The fork-side bridge depends on the ss-console-owned ``HookRegistry``
*shape* (a ``.pinned_slots`` attribute exposing ``.get(key)``), not on
the ss-console package being on ``PYTHONPATH``. The tests use a fake
registry with that shape so the suite runs in the fork's CI in
isolation.
"""

from __future__ import annotations

import importlib
import sys
import types
from dataclasses import dataclass, field
from typing import Any, Optional

import pytest


@dataclass
class _FakePinnedSlots:
    slots: dict[str, str] = field(default_factory=dict)

    def get(self, key: str) -> Optional[str]:
        return self.slots.get(key)


@dataclass
class _FakeRegistry:
    pinned_slots: _FakePinnedSlots = field(default_factory=_FakePinnedSlots)


def _install_fake_plugin_manager() -> dict:
    """Install a minimal ``hermes_cli.plugins`` module that mirrors the
    real upstream surface so the test would catch the integration gap
    found in PRs #2/#3/#4. The real ``PluginManager`` exposes
    ``_hooks: dict[str, list[Callable]]`` and does NOT have a public
    ``register_hook`` method of its own (that lives on
    ``PluginContext``).
    """
    fake_pkg = types.ModuleType("hermes_cli")
    fake_mod = types.ModuleType("hermes_cli.plugins")

    class _FakeManager:
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
    state = _install_fake_plugin_manager()
    import smd.hooks.sticky_stop as mod
    importlib.reload(mod)
    monkeypatch.setattr(mod, "_HOOK_REGISTERED", False, raising=False)
    monkeypatch.setattr(mod, "_REGISTRY_REF", None, raising=False)
    monkeypatch.setattr(mod, "_CUSTOMER_ID", "", raising=False)
    yield state
    sys.modules.pop("hermes_cli.plugins", None)
    sys.modules.pop("hermes_cli", None)


def test_register_smd_adapter_installs_pre_tool_hook(_reset_module_state):
    from smd.hooks import sticky_stop

    registry = _FakeRegistry()
    sticky_stop.register_smd_adapter(registry, customer_id="acme")

    registered = _reset_module_state["registered"]
    assert len(registered) == 1
    name, _cb = registered[0]
    assert name == "pre_tool_call"


def test_pre_tool_allows_when_no_sticky_stop(_reset_module_state):
    from smd.hooks import sticky_stop

    registry = _FakeRegistry()
    sticky_stop.register_smd_adapter(registry, customer_id="acme")
    _name, callback = _reset_module_state["registered"][0]

    decision = callback(
        tool_name="terminal",
        args={"command": "ls"},
        task_id="t1",
        session_id="s1",
        tool_call_id="tc1",
    )
    assert decision is None


def test_pre_tool_allows_when_ok(_reset_module_state):
    from smd.hooks import sticky_stop

    registry = _FakeRegistry(pinned_slots=_FakePinnedSlots(slots={"sticky_stop.active": "OK"}))
    sticky_stop.register_smd_adapter(registry, customer_id="acme")
    _name, callback = _reset_module_state["registered"][0]

    assert callback(tool_name="x") is None


def test_pre_tool_allows_when_warn(_reset_module_state):
    """WARN is observational. Dispatch continues; trust-ceiling enforcer
    is the layer that reacts."""
    from smd.hooks import sticky_stop

    registry = _FakeRegistry(pinned_slots=_FakePinnedSlots(slots={"sticky_stop.active": "WARN"}))
    sticky_stop.register_smd_adapter(registry, customer_id="acme")
    _name, callback = _reset_module_state["registered"][0]

    assert callback(tool_name="x") is None


def test_pre_tool_allows_when_soft_stop(_reset_module_state):
    """SOFT_STOP is handled by trust-ceiling clamp to draft_for_review,
    not by blocking dispatch."""
    from smd.hooks import sticky_stop

    registry = _FakeRegistry(pinned_slots=_FakePinnedSlots(slots={"sticky_stop.active": "SOFT_STOP"}))
    sticky_stop.register_smd_adapter(registry, customer_id="acme")
    _name, callback = _reset_module_state["registered"][0]

    assert callback(tool_name="x") is None


def test_pre_tool_blocks_on_hard_stop(_reset_module_state):
    from smd.hooks import sticky_stop

    registry = _FakeRegistry(pinned_slots=_FakePinnedSlots(slots={"sticky_stop.active": "HARD_STOP"}))
    sticky_stop.register_smd_adapter(registry, customer_id="acme")
    _name, callback = _reset_module_state["registered"][0]

    decision = callback(tool_name="terminal", args={"command": "rm -rf /"})
    assert isinstance(decision, dict)
    assert decision["action"] == "block"
    assert "HARD_STOP" in decision["message"]


def test_pre_tool_blocks_on_hard_stop_case_insensitive(_reset_module_state):
    """The slot is set by the ss-console state machine, but the bridge
    accepts equivalent casings for robustness."""
    from smd.hooks import sticky_stop

    registry = _FakeRegistry(pinned_slots=_FakePinnedSlots(slots={"sticky_stop.active": "hard_stop"}))
    sticky_stop.register_smd_adapter(registry, customer_id="acme")
    _name, callback = _reset_module_state["registered"][0]

    decision = callback(tool_name="x")
    assert isinstance(decision, dict)
    assert decision["action"] == "block"


def test_pre_tool_handles_malformed_registry(_reset_module_state):
    """A registry without ``pinned_slots`` must not abort dispatch."""
    from smd.hooks import sticky_stop

    class _Bad:
        pass

    sticky_stop.register_smd_adapter(_Bad(), customer_id="acme")
    _name, callback = _reset_module_state["registered"][0]

    assert callback(tool_name="x") is None


def test_pre_tool_handles_pinned_slot_get_raising(_reset_module_state):
    """A pinned-slot read raising must not abort dispatch."""
    from smd.hooks import sticky_stop

    class _Boom:
        def get(self, _key):
            raise RuntimeError("slot table corrupt")

    class _Registry:
        pinned_slots = _Boom()

    sticky_stop.register_smd_adapter(_Registry(), customer_id="acme")
    _name, callback = _reset_module_state["registered"][0]

    assert callback(tool_name="x") is None


def test_bridge_installs_on_real_plugin_manager_surface(_reset_module_state):
    """Regression guard for the integration gap fixed in
    fix/smd-hooks-plugin-manager-integration.

    The real upstream ``PluginManager`` exposes ``_hooks`` only; the
    public ``register_hook`` method lives on ``PluginContext``. Earlier
    versions of this bridge called ``manager.register_hook(...)``,
    which raised ``AttributeError`` at production boot and was masked
    in tests by fakes that exposed the wrong surface.

    This test asserts the bridge installs against a manager that ONLY
    has ``_hooks`` (no ``register_hook``)."""
    from smd.hooks import sticky_stop

    manager = _reset_module_state["manager"]
    assert not hasattr(manager, "register_hook"), (
        "fake manager must mirror the real upstream surface "
        "(no register_hook method) for this regression guard to hold"
    )

    sticky_stop.register_smd_adapter(_FakeRegistry(), customer_id="acme")
    assert "pre_tool_call" in manager._hooks
    assert len(manager._hooks["pre_tool_call"]) == 1


def test_second_register_swaps_registry_without_double_bridge(_reset_module_state):
    """Re-registration is idempotent on the upstream callback side."""
    from smd.hooks import sticky_stop

    first = _FakeRegistry(pinned_slots=_FakePinnedSlots(slots={"sticky_stop.active": "OK"}))
    second = _FakeRegistry(pinned_slots=_FakePinnedSlots(slots={"sticky_stop.active": "HARD_STOP"}))

    sticky_stop.register_smd_adapter(first, customer_id="acme-v1")
    sticky_stop.register_smd_adapter(second, customer_id="acme-v2")

    registered = _reset_module_state["registered"]
    assert len(registered) == 1
    _name, callback = registered[0]

    decision = callback(tool_name="x")
    assert isinstance(decision, dict)
    assert decision["action"] == "block"
