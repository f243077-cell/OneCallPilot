"""Copilot metrics (NFR-006): agent duration, tool calls, LLM latency and errors.

backend-worker serves them on ``WORKER_METRICS_PORT`` (default 9464) inside
the Compose network only; the port is never published (architecture §3).
Labels are bounded enums, except ``incident_id`` on the per-incident duration
gauge, which the "Copilot internals" panel needs (a few dozen incidents per
benchmark).
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, start_http_server

REGISTRY = CollectorRegistry()

AGENT_DURATION = Histogram(
    "ocp_agent_duration_seconds",
    "Investigation time from job pickup to the terminal state",
    ["terminal_state"],
    buckets=(10, 20, 30, 45, 60, 75, 90, 120),
    registry=REGISTRY,
)
INCIDENT_DURATION = Gauge(
    "ocp_agent_incident_duration_seconds",
    "Investigation time of one incident",
    ["incident_id", "terminal_state"],
    registry=REGISTRY,
)
TOOL_CALLS = Counter(
    "ocp_agent_tool_calls_total",
    "Tool calls by tool, purpose and outcome (stored or refused)",
    ["tool", "purpose", "outcome"],
    registry=REGISTRY,
)
LLM_LATENCY = Histogram(
    "ocp_llm_call_seconds",
    "LLM call latency by call kind",
    ["kind", "provider"],
    buckets=(1, 2, 5, 10, 15, 20, 30, 60),
    registry=REGISTRY,
)
LLM_ERRORS = Counter(
    "ocp_llm_errors_total",
    "LLM failures: invalid structured output, or the provider unavailable after a retry",
    ["kind", "provider", "error"],
    registry=REGISTRY,
)
ESCALATIONS = Counter(
    "ocp_agent_escalations_total", "Escalations by reason", ["reason"], registry=REGISTRY
)


def serve(port: int) -> None:
    start_http_server(port, registry=REGISTRY)
