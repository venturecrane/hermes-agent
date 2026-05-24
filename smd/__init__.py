"""SMD overlay layer for the venturecrane/hermes-agent fork.

SMD-specific hooks per ADR 0015 (venturecrane/ss-console#844). Upstream
code stays under the upstream package layout (``agent/``, ``hermes_cli/``,
``gateway/``, etc.); SMD code lives here under ``smd/`` so that upstream
rebases stay clean and the SMD surface is locatable in one place.

See ``smd/README.md`` for layout and ``CHANGELOG.md`` for fork history.
"""

__all__: list[str] = []
