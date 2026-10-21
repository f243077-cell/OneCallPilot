"""Parameter models of the agent's tools (architecture §6.3). Strict: unknown
fields, out-of-range values and free-form enums are refused (AI-005…AI-009)."""

from __future__ import annotations

from oncallpilot_contracts.enums import ServiceName
from oncallpilot_contracts.ledger import LedgerService
from pydantic import BaseModel, ConfigDict, Field

from oncallpilot.agent.tools.query_logs import QueryLogsParams
from oncallpilot.agent.tools.query_metrics import QueryMetricsParams


class GetRecentDeploysParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    service: LedgerService | None = None
    limit: int = Field(default=10, ge=1, le=10)


class GetServiceHealthParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    service: ServiceName


class SearchRunbooksParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str = Field(min_length=1, max_length=200)
    k: int = Field(default=3, ge=1, le=3)


__all__ = [
    "GetRecentDeploysParams",
    "GetServiceHealthParams",
    "QueryLogsParams",
    "QueryMetricsParams",
    "SearchRunbooksParams",
]
