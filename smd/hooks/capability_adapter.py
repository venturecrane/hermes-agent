"""Capability-adapter registration hook surface.

Tracks: ADR 0006 (Capability-Adapter Pattern), ss-console
docs/adr/0006-capability-adapter-pattern.md.

Reads ``customer.yaml``'s ``connectors:`` map at Machine boot and
builds a per-capability adapter registry on the ss-console
``HookRegistry`` so skill code can resolve a capability name
(``Email``, ``PracticeManagement``, etc.) to the concrete vendor
adapter for THIS customer.

Wiring shape (per ADR 0006 + ss-console connectors layout)
----------------------------------------------------------

``customer.yaml`` declares the binding per capability::

    connectors:
      PracticeManagement:
        adapter: filevine
        backend: build:filevine
        enabled: true
      Email:
        adapter: gmail
        backend: composio:gmail
        enabled: true

Three backend prefixes are accepted (per
``ai-employee/adapter/validate_customer_yaml.py``):

  * ``build:<wrapper>``    -- python adapter at
                              ``ai-employee/connectors/<wrapper>/``
  * ``composio:<id>``      -- Composio-managed adapter; nothing to
                              import on this side
  * ``synthetic:<fixture>`` -- in-process fixture for demos and tests;
                              nothing to import on this side

For ``build:`` backends the hook dynamic-imports
``ai-employee.connectors.<wrapper>`` and pulls the capability class
out of the package's ``__all__`` by name match -- this is the
convention each vendor package follows (e.g. ``filevine`` exports
``FilevinePracticeManagement`` and ``FilevineDocumentStorage``). For
``composio:`` and ``synthetic:`` backends the entry is recorded as
metadata only; the runtime knows what to do at call time without a
python import.

Output
------

After this hook runs, ``registry.capability_adapters`` is a dict::

    {
      "PracticeManagement": {
        "adapter": "filevine",
        "backend": "build:filevine",
        "implementation": <class 'FilevinePracticeManagement'>,
      },
      "Email": {
        "adapter": "gmail",
        "backend": "composio:gmail",
        "implementation": None,
      },
      ...
    }

Skill-side capability resolution (out of scope for this PR -- shipped
separately in the ADR 0006 build-out work stream) reads this map to
hand a skill the right adapter for its customer.

Failure mode
------------

Per-capability errors are isolated: a missing or broken ``build:``
adapter for one capability does not prevent the others from being
registered. The customer-facing impact is a skill calling that
capability getting a "not configured" runtime error; the alternative
(failing Machine boot if any single connector cannot be loaded) would
be worse for incremental rollouts and per-customer adapter swaps.
"""

from __future__ import annotations

import importlib
import logging
import os
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("smd.hooks.capability_adapter")


_HOOK_REGISTERED = False
_REGISTRY_REF: Any = None
_CUSTOMER_ID: str = ""

# Closed set per ADR 0006 §Decision / PRD §7.2. Mirrored from
# ai-employee/connectors/filevine/errors.py::CAPABILITY_NAMES so the
# overlay refuses unknown capability names rather than silently
# accepting typos in customer.yaml.
_CAPABILITY_NAMES = frozenset({
    "PracticeManagement",
    "Email",
    "Calendar",
    "DocumentStorage",
    "ESign",
    "CourtAccess",
    "Payments",
    "Accounting",
    "IntakeCRM",
    "CallTracking",
    "InternalComms",
})


def _load_customer_config(path_override: Optional[str] = None) -> dict:
    """Read ``customer.yaml`` at the adapter-conventional path.

    Mirrors ``aie_adapter._load_customer_config``: honors the
    ``AIE_CUSTOMER_YAML`` env var, defaults to ``/app/customer.yaml``.
    Returns ``{}`` on any read failure -- per-capability registration
    is skipped rather than aborting boot.
    """
    if path_override is not None:
        yaml_path = path_override
    else:
        yaml_path = os.environ.get("AIE_CUSTOMER_YAML", "/app/customer.yaml")

    try:
        import yaml
    except ImportError:
        log.warning(
            "capability_adapter: pyyaml unavailable; "
            "skipping connectors registration"
        )
        return {}

    p = Path(yaml_path)
    if not p.exists():
        log.info(
            "capability_adapter: customer.yaml not at %s; "
            "no connectors registered",
            yaml_path,
        )
        return {}

    try:
        with open(p) as f:
            data = yaml.safe_load(f) or {}
    except Exception as exc:
        log.warning(
            "capability_adapter: customer.yaml unreadable at %s: %s",
            yaml_path,
            exc,
        )
        return {}

    return data if isinstance(data, dict) else {}


def _find_capability_class(module: Any, capability: str) -> Optional[type]:
    """Locate the capability implementation inside a vendor package.

    Convention (ss-console PR #828 + ADR 0006): a vendor adapter
    package exports its capability implementations under names of the
    form ``<Vendor><Capability>`` (``FilevinePracticeManagement``,
    ``FilevineDocumentStorage``). The matcher is suffix-based so
    capitalisation drift in the vendor prefix does not break the
    lookup.
    """
    candidates = getattr(module, "__all__", None)
    if candidates is None:
        candidates = [name for name in dir(module) if not name.startswith("_")]

    for name in candidates:
        if name.endswith(capability):
            obj = getattr(module, name, None)
            if isinstance(obj, type):
                return obj
    return None


def _resolve_build_adapter(wrapper: str, capability: str) -> Optional[type]:
    """Dynamic-import ``ai-employee.connectors.<wrapper>`` and return
    the class implementing ``capability``. Returns ``None`` on any
    import or lookup failure (per-capability failure isolation)."""
    module_path = f"ai-employee.connectors.{wrapper}"
    # The package on disk uses snake_case (e.g. "ms_graph"), and Python
    # dotted-module paths cannot contain hyphens, so the wrapper string
    # is used as-is from customer.yaml. ms-graph aliasing is the
    # validator's responsibility (per validate_customer_yaml.py), not
    # the overlay's.
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        log.warning(
            "capability_adapter: cannot import %s for %s: %s",
            module_path,
            capability,
            exc,
        )
        return None
    except Exception as exc:
        log.warning(
            "capability_adapter: %s import raised for %s: %s",
            module_path,
            capability,
            exc,
        )
        return None

    impl = _find_capability_class(module, capability)
    if impl is None:
        log.warning(
            "capability_adapter: %s declares no class for %s",
            module_path,
            capability,
        )
    return impl


def _build_registry_entries(connectors: dict) -> dict:
    """Walk customer.yaml's ``connectors:`` map and resolve adapters.

    Skips entries with ``enabled: false``, with unknown capability
    names, with missing ``adapter`` or ``backend`` fields, or with
    backends that do not match the validator's accepted prefixes.

    Returns a dict keyed by capability name. The value is a small
    metadata dict so skill-side resolution code can pick the right
    field at call time: ``adapter`` (vendor slug), ``backend`` (raw
    string from customer.yaml), ``implementation`` (the python class,
    or ``None`` for composio / synthetic / failed-import entries).
    """
    entries: dict = {}
    for capability, conf in (connectors or {}).items():
        if capability not in _CAPABILITY_NAMES:
            log.warning(
                "capability_adapter: unknown capability %r in customer.yaml; "
                "skipping",
                capability,
            )
            continue
        if not isinstance(conf, dict):
            continue
        if conf.get("enabled") is False:
            log.info(
                "capability_adapter: %s disabled in customer.yaml; skipping",
                capability,
            )
            continue

        adapter = conf.get("adapter")
        backend = conf.get("backend")
        if not adapter or not backend:
            log.warning(
                "capability_adapter: %s missing adapter or backend; skipping",
                capability,
            )
            continue

        implementation: Optional[type] = None
        if isinstance(backend, str) and backend.startswith("build:"):
            wrapper = backend.split(":", 1)[1]
            implementation = _resolve_build_adapter(wrapper, capability)
        elif isinstance(backend, str) and (
            backend.startswith("composio:") or backend.startswith("synthetic:")
        ):
            implementation = None
        else:
            log.warning(
                "capability_adapter: %s has unrecognized backend %r; "
                "registering metadata only",
                capability,
                backend,
            )

        entries[capability] = {
            "adapter": adapter,
            "backend": backend,
            "implementation": implementation,
        }
        log.info(
            "capability_adapter: registered %s -> adapter=%s backend=%s "
            "implementation=%s",
            capability,
            adapter,
            backend,
            "<class>" if implementation is not None else "None",
        )
    return entries


def register_smd_adapter(
    registry: Any,
    *,
    customer_id: str = "",
    customer_yaml_path: Optional[str] = None,
    **_: Any,
) -> None:
    """Wire capability adapters from ``customer.yaml`` onto the registry.

    Called exactly once at Machine boot by ``aie_adapter.register()``.
    Re-registration is supported: a second call rebuilds the
    ``capability_adapters`` map on the supplied registry.

    Args:
        registry: ss-console ``HookRegistry``. The hook attaches a
            ``capability_adapters`` dict to this object.
        customer_id: customer slug from ``customer.yaml``; carried
            into log lines for cross-row correlation.
        customer_yaml_path: optional override for the customer.yaml
            location. When omitted, the path is read from
            ``AIE_CUSTOMER_YAML`` (or defaults to
            ``/app/customer.yaml``), matching
            ``aie_adapter._load_customer_config``'s behavior.
    """
    global _HOOK_REGISTERED, _REGISTRY_REF, _CUSTOMER_ID
    _REGISTRY_REF = registry
    _CUSTOMER_ID = customer_id or ""

    cfg = _load_customer_config(customer_yaml_path)
    connectors = cfg.get("connectors") or {}
    entries = _build_registry_entries(connectors)

    try:
        registry.capability_adapters = entries
    except Exception as exc:
        log.warning(
            "capability_adapter: cannot attach capability_adapters to "
            "registry (type=%s): %s",
            type(registry).__name__,
            exc,
        )
        return

    _HOOK_REGISTERED = True
    log.info(
        "capability_adapter: registered %d capability adapter(s) for "
        "customer_id=%s (%s)",
        len(entries),
        _CUSTOMER_ID,
        ", ".join(sorted(entries.keys())) if entries else "none",
    )
