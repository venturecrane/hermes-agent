"""Tests for ``smd.hooks.trust_ceiling`` (ss-console #864, #948, #953).

The bridge depends on the ss-console adapter *shape* (``HookRegistry``
with ``dispatch_pre_tool`` / ``dispatch_refusal``; ``BlockedToolCall``,
``ToolCallContext``, ``HookActionClass`` exported from
``adapter.hermes_hook``). Tests inject a fake adapter module so the
suite runs in the fork's CI without ss-console on ``PYTHONPATH``.
"""

from __future__ import annotations

import importlib
import sys
import types
from dataclasses import dataclass, field
from typing import Any, Optional

import pytest


@dataclass(frozen=True)
class _FakeContext:
    customer: str
    skill_name: str
    tool_name: str
    action_class: Any
    ceiling_level: str
    skill_version: Optional[str] = None
    matter_ref: Optional[str] = None
    trace_id: Optional[str] = None
    current_turn_approval: bool = False
    arguments: Optional[dict] = None


class _FakeBlockedToolCall(Exception):
    def __init__(self, *, reason: str, customer_message: Optional[str] = None,
                 context: Optional[_FakeContext] = None) -> None:
        super().__init__(f"blocked: {reason}")
        self.reason = reason
        self.customer_message = customer_message
        self.context = context


@dataclass
class _FakePinnedSlots:
    slots: dict[str, str] = field(default_factory=dict)

    def get(self, key: str) -> Optional[str]:
        return self.slots.get(key)


@dataclass
class _FakeRegistry:
    pre_tool_calls: list[_FakeContext] = field(default_factory=list)
    refusal_calls: list[tuple[_FakeContext, _FakeBlockedToolCall]] = field(default_factory=list)
    pre_tool_should_raise: Optional[BaseException] = None
    refusal_should_raise: Optional[BaseException] = None
    pinned_slots: _FakePinnedSlots = field(default_factory=_FakePinnedSlots)

    async def dispatch_pre_tool(self, ctx: _FakeContext) -> None:
        self.pre_tool_calls.append(ctx)
        if self.pre_tool_should_raise is not None:
            raise self.pre_tool_should_raise

    async def dispatch_refusal(self, ctx: _FakeContext, block: _FakeBlockedToolCall) -> None:
        self.refusal_calls.append((ctx, block))
        if self.refusal_should_raise is not None:
            raise self.refusal_should_raise


def _install_fake_adapter_module() -> None:
    fake_pkg = types.ModuleType("adapter")
    fake_mod = types.ModuleType("adapter.hermes_hook")

    class _ActionClassEnum:
        READ = "read"
        INTERNAL_WRITE = "internal_write"
        EXTERNAL_SEND = "external_send"
        COMMITMENT = "commitment"
        DESTRUCTIVE = "destructive"

    fake_mod.HookActionClass = _ActionClassEnum  # type: ignore[attr-defined]
    fake_mod.ToolCallContext = _FakeContext  # type: ignore[attr-defined]
    fake_mod.BlockedToolCall = _FakeBlockedToolCall  # type: ignore[attr-defined]

    sys.modules["adapter"] = fake_pkg
    sys.modules["adapter.hermes_hook"] = fake_mod


def _install_fake_plugin_manager() -> dict:
    fake_pkg = types.ModuleType("hermes_cli")
    fake_mod = types.ModuleType("hermes_cli.plugins")
    state: dict = {"registered": []}

    class _FakeManager:
        def register_hook(self, hook_name: str, callback: Any) -> None:
            state["registered"].append((hook_name, callback))

    fake_mod.get_plugin_manager = lambda: _FakeManager()  # type: ignore[attr-defined]
    sys.modules.setdefault("hermes_cli", fake_pkg)
    sys.modules["hermes_cli.plugins"] = fake_mod
    return state


@pytest.fixture(autouse=True)
def _reset_module_state(monkeypatch):
    _install_fake_adapter_module()
    state = _install_fake_plugin_manager()
    import smd.hooks.trust_ceiling as mod
    importlib.reload(mod)
    monkeypatch.setattr(mod, "_HOOK_REGISTERED", False, raising=False)
    monkeypatch.setattr(mod, "_REGISTRY_REF", None, raising=False)
    monkeypatch.setattr(mod, "_CUSTOMER_ID", "", raising=False)
    yield state
    sys.modules.pop("hermes_cli.plugins", None)
    sys.modules.pop("hermes_cli", None)
    sys.modules.pop("adapter.hermes_hook", None)
    sys.modules.pop("adapter", None)


def test_register_smd_adapter_installs_pre_tool_hook(_reset_module_state):
    from smd.hooks import trust_ceiling

    registry = _FakeRegistry()
    trust_ceiling.register_smd_adapter(registry, customer_id="acme")

    registered = _reset_module_state["registered"]
    assert len(registered) == 1
    name, _cb = registered[0]
    assert name == "pre_tool_call"


def test_allow_path_invokes_dispatch_pre_tool(_reset_module_state):
    from smd.hooks import trust_ceiling

    registry = _FakeRegistry()
    trust_ceiling.register_smd_adapter(registry, customer_id="acme")
    _name, callback = _reset_module_state["registered"][0]

    decision = callback(
        tool_name="terminal",
        args={"command": "ls"},
        task_id="t1",
        session_id="s1",
        tool_call_id="tc1",
    )
    assert decision is None
    assert len(registry.pre_tool_calls) == 1
    assert registry.pre_tool_calls[0].tool_name == "terminal"
    assert registry.pre_tool_calls[0].customer == "acme"
    assert registry.refusal_calls == []


def test_block_path_fires_refusal_and_returns_block_directive(_reset_module_state):
    from smd.hooks import trust_ceiling

    registry = _FakeRegistry(
        pre_tool_should_raise=_FakeBlockedToolCall(
            reason="commitment_no_approval",
            customer_message="That action needs reviewer sign-off.",
        ),
    )
    trust_ceiling.register_smd_adapter(registry, customer_id="acme")
    _name, callback = _reset_module_state["registered"][0]

    decision = callback(tool_name="email_send", args={})
    assert isinstance(decision, dict)
    assert decision["action"] == "block"
    assert decision["message"] == "That action needs reviewer sign-off."
    assert len(registry.refusal_calls) == 1
    _ctx, block = registry.refusal_calls[0]
    assert block.reason == "commitment_no_approval"


def test_block_path_falls_back_message_when_block_has_none(_reset_module_state):
    from smd.hooks import trust_ceiling

    registry = _FakeRegistry(
        pre_tool_should_raise=_FakeBlockedToolCall(reason="r"),
    )
    trust_ceiling.register_smd_adapter(registry, customer_id="acme")
    _name, callback = _reset_module_state["registered"][0]

    decision = callback(tool_name="x")
    assert decision is not None
    assert decision["action"] == "block"
    assert "approval" in decision["message"].lower()


def test_refusal_hook_exception_does_not_break_block_decision(_reset_module_state):
    """If the refusal hook raises (audit writer down), the bridge still
    must return the block so the tool does not run."""
    from smd.hooks import trust_ceiling

    registry = _FakeRegistry(
        pre_tool_should_raise=_FakeBlockedToolCall(
            reason="r",
            customer_message="blocked.",
        ),
        refusal_should_raise=RuntimeError("notification system down"),
    )
    trust_ceiling.register_smd_adapter(registry, customer_id="acme")
    _name, callback = _reset_module_state["registered"][0]

    decision = callback(tool_name="x")
    assert decision is not None
    assert decision["action"] == "block"


def test_non_block_exception_allows_call(_reset_module_state):
    """A non-Block exception from the substrate is infra noise, not an
    enforcement decision. Allow + log so a transient substrate fault
    does not brick every dispatch."""
    from smd.hooks import trust_ceiling

    registry = _FakeRegistry(pre_tool_should_raise=RuntimeError("transient"))
    trust_ceiling.register_smd_adapter(registry, customer_id="acme")
    _name, callback = _reset_module_state["registered"][0]

    decision = callback(tool_name="x")
    assert decision is None


def test_context_ceiling_from_pinned_slot(_reset_module_state):
    from smd.hooks import trust_ceiling

    registry = _FakeRegistry(
        pinned_slots=_FakePinnedSlots(slots={"trust_ceiling.locked_skills": "autonomous"}),
    )
    trust_ceiling.register_smd_adapter(registry, customer_id="acme")
    _name, callback = _reset_module_state["registered"][0]

    callback(tool_name="x")
    assert len(registry.pre_tool_calls) == 1
    assert registry.pre_tool_calls[0].ceiling_level == "autonomous"


def test_context_ceiling_defaults_to_draft_for_review(_reset_module_state):
    from smd.hooks import trust_ceiling

    registry = _FakeRegistry()
    trust_ceiling.register_smd_adapter(registry, customer_id="acme")
    _name, callback = _reset_module_state["registered"][0]

    callback(tool_name="x")
    assert registry.pre_tool_calls[0].ceiling_level == "draft_for_review"


def test_register_without_adapter_module_succeeds(monkeypatch):
    """Without adapter.hermes_hook importable, registration installs
    the upstream callback but each call no-ops to allow."""
    sys.modules.pop("adapter.hermes_hook", None)
    sys.modules.pop("adapter", None)
    state = _install_fake_plugin_manager()
    import smd.hooks.trust_ceiling as mod
    importlib.reload(mod)

    registry = _FakeRegistry()
    mod.register_smd_adapter(registry, customer_id="acme")
    _name, callback = state["registered"][-1]

    decision = callback(tool_name="x")
    assert decision is None
    assert registry.pre_tool_calls == []
    sys.modules.pop("hermes_cli.plugins", None)
    sys.modules.pop("hermes_cli", None)


def test_second_register_swaps_registry_without_double_bridge(_reset_module_state):
    from smd.hooks import trust_ceiling

    first = _FakeRegistry()
    second = _FakeRegistry(
        pre_tool_should_raise=_FakeBlockedToolCall(
            reason="r",
            customer_message="second registry blocked.",
        ),
    )

    trust_ceiling.register_smd_adapter(first, customer_id="acme-v1")
    trust_ceiling.register_smd_adapter(second, customer_id="acme-v2")

    registered = _reset_module_state["registered"]
    assert len(registered) == 1
    _name, callback = registered[0]

    decision = callback(tool_name="x")
    assert decision is not None
    assert decision["action"] == "block"
    assert "second registry" in decision["message"]
