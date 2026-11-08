"""Runs one read-only tool by name with validated parameters (architecture §5.3 steps 1–2).

Parameters are validated against the tool's own model first; a refusal is
returned as a message for the model and still counts as a call. Every call
is time-boxed to 10 s. Returns the raw (not yet redacted) payload; the effects
redact before anything is stored or shown.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import asyncpg
import httpx
from oncallpilot_contracts.domain import TOOL_EVIDENCE_KIND
from oncallpilot_contracts.enums import EvidenceKind
from pydantic import BaseModel, ValidationError

from oncallpilot.agent.providers.embeddings import EmbeddingProvider
from oncallpilot.agent.tools.clients import LokiClient, PrometheusClient, QueryError
from oncallpilot.agent.tools.get_recent_deploys import get_recent_deploys
from oncallpilot.agent.tools.get_service_health import get_service_health
from oncallpilot.agent.tools.params import (
    GetRecentDeploysParams,
    GetServiceHealthParams,
    QueryLogsParams,
    QueryMetricsParams,
    SearchRunbooksParams,
)
from oncallpilot.agent.tools.promql import UnsupportedMetricError
from oncallpilot.agent.tools.query_logs import query_logs
from oncallpilot.agent.tools.query_metrics import query_metrics
from oncallpilot.agent.tools.registry import READ_TOOLS
from oncallpilot.agent.tools.search_runbooks import search_runbooks
from oncallpilot.agent.tools.window import RESET_SETTLE, WindowError
from oncallpilot.ledger import read_records

TOOL_TIMEOUT_SECONDS = 10.0


class ToolRefusedError(Exception):
    """Unknown tool, invalid parameters, or the backend failed; the message is for the model."""


@dataclass(frozen=True)
class ToolResult:
    kind: EvidenceKind
    params: dict[str, Any]
    payload: dict[str, Any]


@dataclass
class ToolBox:
    loki: LokiClient
    prometheus: PrometheusClient
    docker: httpx.AsyncClient  # base URL = socket-proxy-ro
    http: httpx.AsyncClient  # chaos_net probes
    ledger: Path
    pool: asyncpg.Pool
    embedder: EmbeddingProvider

    async def run(
        self,
        name: str,
        arguments: dict[str, Any],
        window_start: datetime,
        window_end: datetime | None,
        now: datetime,
    ) -> ToolResult:
        if name not in READ_TOOLS:
            raise ToolRefusedError(f"unknown tool {name!r}; use one of {', '.join(READ_TOOLS)}")
        model, _ = READ_TOOLS[name]
        try:
            params = model.model_validate(arguments)
        except ValidationError as exc:
            errors = "; ".join(
                f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
            )
            raise ToolRefusedError(f"invalid parameters for {name}: {errors}") from exc
        try:
            payload = await asyncio.wait_for(
                self._call(name, params, window_start, window_end, now),
                timeout=TOOL_TIMEOUT_SECONDS,
            )
        except TimeoutError as exc:
            raise ToolRefusedError(f"{name} timed out after {TOOL_TIMEOUT_SECONDS:.0f} s") from exc
        except (QueryError, WindowError, UnsupportedMetricError, httpx.HTTPError, OSError) as exc:
            raise ToolRefusedError(f"{name} failed: {type(exc).__name__}: {exc}"[:300]) from exc
        return ToolResult(
            kind=TOOL_EVIDENCE_KIND[name],  # type: ignore[index]
            params=params.model_dump(mode="json", exclude_none=True),
            payload=payload.model_dump(mode="json"),
        )

    async def _call(
        self,
        name: str,
        params: BaseModel,
        window_start: datetime,
        window_end: datetime | None,
        now: datetime,
    ) -> BaseModel:
        if isinstance(params, QueryLogsParams):
            floor = self._settled_reset(now)
            return await query_logs(self.loki, params, window_start, window_end, now, floor)
        if isinstance(params, QueryMetricsParams):
            floor = self._settled_reset(now)
            return await query_metrics(
                self.prometheus, params, window_start, window_end, now, floor
            )
        if isinstance(params, GetRecentDeploysParams):
            return get_recent_deploys(self.ledger, params)
        if isinstance(params, GetServiceHealthParams):
            floor = self._settled_reset(now)
            return await get_service_health(self.docker, self.http, params, floor)
        if isinstance(params, SearchRunbooksParams):
            async with self.pool.acquire() as conn:
                return await search_runbooks(conn, self.embedder, params)
        raise ToolRefusedError(f"no implementation for {name}")  # pragma: no cover

    def _settled_reset(self, now: datetime) -> datetime | None:
        """ADR-27: the end of the last testbed reset's settle period, from the ledger.

        Logs and metrics from before it belong to another run (``chaos reset``
        restores the baseline). A ledger without resets (production) changes nothing.
        """
        resets = [
            r.deployed_at
            for r in read_records(self.ledger)
            if r.kind == "reset" and r.deployed_at <= now
        ]
        return max(resets) + RESET_SETTLE if resets else None
