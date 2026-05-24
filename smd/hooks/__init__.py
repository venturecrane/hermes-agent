"""SMD hook surfaces consumed by the SMD adapter (ai-employee/adapter).

This package holds the four named hook surfaces the SMD adapter binds
against per ADR 0015. Each file is a stable contract; the adapter side
(``aie_adapter.register()`` in ss-console) does not depend on this
package's internal layout, only on the ``register_smd_adapter`` callable
exposed by each submodule.

Submodules:
  - ``audit_emission``    per-tool audit emission (ss-console#842)
  - ``sticky_stop``       sticky-stop dispatch interception (ss-console#843)
  - ``trust_ceiling``     trust-ceiling enforce/refuse (PRD §7.5)
  - ``capability_adapter`` capability-adapter registration (ADR 0006)
"""

__all__: list[str] = []
