"""Tests for ``smd.hooks.capability_adapter`` (ADR 0006).

Exercises the customer.yaml read, the connectors-map walk, the
build-adapter dynamic import, and the per-capability failure
isolation. Uses tmp_path for the customer.yaml fixture and injects a
fake ``ai-employee.connectors.<wrapper>`` module into ``sys.modules``
so the build-adapter path can be exercised without ss-console on
``PYTHONPATH``.
"""

from __future__ import annotations

import importlib
import sys
import textwrap
import types
from dataclasses import dataclass, field
from typing import Any

import pytest


pytest.importorskip("yaml")


@dataclass
class _FakeRegistry:
    capability_adapters: dict = field(default_factory=dict)


@pytest.fixture(autouse=True)
def _reset_module_state(monkeypatch):
    import smd.hooks.capability_adapter as mod
    importlib.reload(mod)
    monkeypatch.setattr(mod, "_HOOK_REGISTERED", False, raising=False)
    monkeypatch.setattr(mod, "_REGISTRY_REF", None, raising=False)
    monkeypatch.setattr(mod, "_CUSTOMER_ID", "", raising=False)
    monkeypatch.delenv("AIE_CUSTOMER_YAML", raising=False)
    yield
    for name in list(sys.modules):
        if name == "ai-employee.connectors" or name.startswith("ai-employee.connectors."):
            sys.modules.pop(name, None)
    sys.modules.pop("ai-employee", None)


def _write_yaml(tmp_path, body: str) -> str:
    p = tmp_path / "customer.yaml"
    p.write_text(textwrap.dedent(body))
    return str(p)


def _install_fake_vendor_package(
    wrapper: str,
    classes: dict[str, type],
) -> None:
    """Install ``ai-employee.connectors.<wrapper>`` with the given
    capability classes so the build-adapter dynamic-import resolves."""
    pkg_root = "ai-employee"
    pkg_connectors = "ai-employee.connectors"
    pkg_vendor = f"ai-employee.connectors.{wrapper}"

    sys.modules.setdefault(pkg_root, types.ModuleType(pkg_root))
    sys.modules.setdefault(pkg_connectors, types.ModuleType(pkg_connectors))
    vendor_mod = types.ModuleType(pkg_vendor)
    vendor_mod.__all__ = list(classes.keys())  # type: ignore[attr-defined]
    for name, cls in classes.items():
        setattr(vendor_mod, name, cls)
    sys.modules[pkg_vendor] = vendor_mod


def test_no_customer_yaml_leaves_empty_map(tmp_path):
    from smd.hooks import capability_adapter

    yaml_path = str(tmp_path / "nope.yaml")
    registry = _FakeRegistry()
    capability_adapter.register_smd_adapter(
        registry, customer_id="acme", customer_yaml_path=yaml_path
    )
    assert registry.capability_adapters == {}


def test_synthetic_backend_records_metadata_only(tmp_path):
    from smd.hooks import capability_adapter

    yaml_path = _write_yaml(
        tmp_path,
        """\
        connectors:
          Email:
            adapter: synthetic
            backend: synthetic:fixture
            enabled: true
        """,
    )
    registry = _FakeRegistry()
    capability_adapter.register_smd_adapter(
        registry, customer_id="acme", customer_yaml_path=yaml_path
    )

    assert "Email" in registry.capability_adapters
    entry = registry.capability_adapters["Email"]
    assert entry["adapter"] == "synthetic"
    assert entry["backend"] == "synthetic:fixture"
    assert entry["implementation"] is None


def test_composio_backend_records_metadata_only(tmp_path):
    from smd.hooks import capability_adapter

    yaml_path = _write_yaml(
        tmp_path,
        """\
        connectors:
          Calendar:
            adapter: gmail
            backend: composio:gmail
            enabled: true
        """,
    )
    registry = _FakeRegistry()
    capability_adapter.register_smd_adapter(
        registry, customer_id="acme", customer_yaml_path=yaml_path
    )

    assert registry.capability_adapters["Calendar"]["implementation"] is None
    assert registry.capability_adapters["Calendar"]["adapter"] == "gmail"


def test_build_backend_resolves_class_by_capability_suffix(tmp_path):
    from smd.hooks import capability_adapter

    class FilevinePracticeManagement:
        pass

    class FilevineDocumentStorage:
        pass

    _install_fake_vendor_package(
        "filevine",
        {
            "FilevinePracticeManagement": FilevinePracticeManagement,
            "FilevineDocumentStorage": FilevineDocumentStorage,
        },
    )

    yaml_path = _write_yaml(
        tmp_path,
        """\
        connectors:
          PracticeManagement:
            adapter: filevine
            backend: build:filevine
            enabled: true
          DocumentStorage:
            adapter: filevine
            backend: build:filevine
            enabled: true
        """,
    )
    registry = _FakeRegistry()
    capability_adapter.register_smd_adapter(
        registry, customer_id="acme", customer_yaml_path=yaml_path
    )

    assert registry.capability_adapters["PracticeManagement"]["implementation"] is FilevinePracticeManagement
    assert registry.capability_adapters["DocumentStorage"]["implementation"] is FilevineDocumentStorage


def test_disabled_connector_skipped(tmp_path):
    from smd.hooks import capability_adapter

    yaml_path = _write_yaml(
        tmp_path,
        """\
        connectors:
          Email:
            adapter: gmail
            backend: composio:gmail
            enabled: false
        """,
    )
    registry = _FakeRegistry()
    capability_adapter.register_smd_adapter(
        registry, customer_id="acme", customer_yaml_path=yaml_path
    )
    assert registry.capability_adapters == {}


def test_unknown_capability_skipped(tmp_path):
    """Typos in customer.yaml capability names are rejected, not
    silently registered."""
    from smd.hooks import capability_adapter

    yaml_path = _write_yaml(
        tmp_path,
        """\
        connectors:
          NotARealCapability:
            adapter: gmail
            backend: composio:gmail
            enabled: true
          Email:
            adapter: gmail
            backend: composio:gmail
            enabled: true
        """,
    )
    registry = _FakeRegistry()
    capability_adapter.register_smd_adapter(
        registry, customer_id="acme", customer_yaml_path=yaml_path
    )
    assert "NotARealCapability" not in registry.capability_adapters
    assert "Email" in registry.capability_adapters


def test_missing_adapter_or_backend_skipped(tmp_path):
    from smd.hooks import capability_adapter

    yaml_path = _write_yaml(
        tmp_path,
        """\
        connectors:
          Email:
            enabled: true
        """,
    )
    registry = _FakeRegistry()
    capability_adapter.register_smd_adapter(
        registry, customer_id="acme", customer_yaml_path=yaml_path
    )
    assert "Email" not in registry.capability_adapters


def test_per_capability_failure_isolation(tmp_path):
    """A broken ``build:`` adapter for one capability does not block
    the others. The customer-facing impact is a skill calling that
    capability getting a 'not configured' runtime error."""
    from smd.hooks import capability_adapter

    yaml_path = _write_yaml(
        tmp_path,
        """\
        connectors:
          PracticeManagement:
            adapter: nonexistent
            backend: build:nonexistent_vendor_pkg
            enabled: true
          Email:
            adapter: gmail
            backend: composio:gmail
            enabled: true
        """,
    )
    registry = _FakeRegistry()
    capability_adapter.register_smd_adapter(
        registry, customer_id="acme", customer_yaml_path=yaml_path
    )

    assert registry.capability_adapters["PracticeManagement"]["implementation"] is None
    assert registry.capability_adapters["PracticeManagement"]["adapter"] == "nonexistent"
    assert "Email" in registry.capability_adapters


def test_unrecognized_backend_records_metadata_only(tmp_path):
    """A backend that does not match the validator's accepted prefixes
    is logged and recorded as metadata-only; it does not crash boot."""
    from smd.hooks import capability_adapter

    yaml_path = _write_yaml(
        tmp_path,
        """\
        connectors:
          Email:
            adapter: weird
            backend: zzz:unknown
            enabled: true
        """,
    )
    registry = _FakeRegistry()
    capability_adapter.register_smd_adapter(
        registry, customer_id="acme", customer_yaml_path=yaml_path
    )
    assert registry.capability_adapters["Email"]["implementation"] is None
    assert registry.capability_adapters["Email"]["backend"] == "zzz:unknown"


def test_re_registration_rebuilds_map(tmp_path):
    from smd.hooks import capability_adapter

    yaml_path_1 = _write_yaml(
        tmp_path,
        """\
        connectors:
          Email:
            adapter: gmail
            backend: composio:gmail
            enabled: true
        """,
    )
    registry = _FakeRegistry()
    capability_adapter.register_smd_adapter(
        registry, customer_id="acme", customer_yaml_path=yaml_path_1
    )
    assert set(registry.capability_adapters.keys()) == {"Email"}

    # Re-register with a different customer.yaml. The map rebuilds.
    yaml_path_2 = str(tmp_path / "v2.yaml")
    with open(yaml_path_2, "w", encoding="utf-8") as f:
        f.write(textwrap.dedent("""\
        connectors:
          Calendar:
            adapter: gmail
            backend: composio:gmail
            enabled: true
        """))
    capability_adapter.register_smd_adapter(
        registry, customer_id="acme", customer_yaml_path=yaml_path_2
    )
    assert set(registry.capability_adapters.keys()) == {"Calendar"}


def test_env_var_path_honored(monkeypatch, tmp_path):
    from smd.hooks import capability_adapter

    yaml_path = _write_yaml(
        tmp_path,
        """\
        connectors:
          Email:
            adapter: gmail
            backend: composio:gmail
            enabled: true
        """,
    )
    monkeypatch.setenv("AIE_CUSTOMER_YAML", yaml_path)
    registry = _FakeRegistry()
    capability_adapter.register_smd_adapter(registry, customer_id="acme")
    assert "Email" in registry.capability_adapters
