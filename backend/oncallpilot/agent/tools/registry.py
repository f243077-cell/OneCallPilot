"""The complete tool list (architecture §6.3; AI-004; ``CLAUDE.md`` §5.1).

Exactly five read-only tools for gathering and disproof, plus the terminal
``propose_action``. There is no tool that writes, executes text, shells out,
calls the runner or edits files. A test pins this list.
"""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel

from oncallpilot.agent.providers.base import ToolSpec
from oncallpilot.agent.tools.params import (
    GetRecentDeploysParams,
    GetServiceHealthParams,
    QueryLogsParams,
    QueryMetricsParams,
    SearchRunbooksParams,
)

READ_TOOLS: Final[dict[str, tuple[type[BaseModel], str]]] = {
    "query_logs": (
        QueryLogsParams,
        "Search one service's logs in the incident window with a fixed query. "
        "level: ERROR, WARNING, INFO or ANY; contains: an optional literal substring.",
    ),
    "query_metrics": (
        QueryMetricsParams,
        "One fixed metric series of api or worker in the incident window, with baseline, "
        "peak, % change and change point.",
    ),
    "get_recent_deploys": (
        GetRecentDeploysParams,
        "The latest deploy-ledger records (deploys, rollbacks, scaling, resets), newest first.",
    ),
    "get_service_health": (
        GetServiceHealthParams,
        "Container state, health, restarts and exit code of one service, plus a /healthz probe.",
    ),
    "search_runbooks": (
        SearchRunbooksParams,
        "Up to 3 runbook passages most similar to the query.",
    ),
}

TERMINAL_TOOL: Final = "propose_action"
ALL_TOOLS: Final[frozenset[str]] = frozenset({*READ_TOOLS, TERMINAL_TOOL})


def read_tool_specs() -> list[ToolSpec]:
    """Tool specs offered to the model during gathering (never ``propose_action``)."""
    return [
        ToolSpec(name=name, description=description, parameters=model.model_json_schema())
        for name, (model, description) in READ_TOOLS.items()
    ]
