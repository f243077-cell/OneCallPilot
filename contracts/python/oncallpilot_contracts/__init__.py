"""OnCallPilot shared contract models.

Modules, by contract:

- C1 domain models: ``domain``, ``enums``, ``common``
- C2 REST bodies: ``api`` (endpoints in ``contracts/openapi.yaml``)
- C3 WebSocket messages: ``ws``, and ``timeline`` for the fixture timelines
- C5 runner messaging: ``runner``, ``signing``
- C6 deploy ledger: ``ledger``
- C8 approval protocol: ``approval``
- C9 push payload: ``push``

C4 (``contracts/actions.yaml``) and C7 (``contracts/telemetry.md``) have no
Python module. Every model here is a cross-component contract: change it only
through a ``contract/*`` PR (CLAUDE.md §8.4). ``schema_export`` writes the
JSON Schemas in ``contracts/schemas/``.
"""
