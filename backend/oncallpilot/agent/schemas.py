"""Structured LLM outputs (architecture §6.6; AI-020). Agent-internal, not contracts.

Every model forbids unknown fields and is frozen (``CLAUDE.md`` §4.4). The
model never sets a risk tier, approval requirement or rollback step: those
fields do not exist here and are copied from the catalogue (§5.6).
"""

from __future__ import annotations

from typing import Any, Literal

from oncallpilot_contracts.common import EvidenceRef
from oncallpilot_contracts.enums import RootCauseCategory, ToolName
from pydantic import BaseModel, ConfigDict, Field


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class HypothesisOut(_Out):
    rank: int = Field(ge=1, le=3)
    summary: str = Field(min_length=10, max_length=400)
    root_cause_category: RootCauseCategory
    confidence: float = Field(ge=0, le=1)
    evidence_refs: list[EvidenceRef] = Field(min_length=1, max_length=8)


class HypothesisSetOut(_Out):
    hypotheses: list[HypothesisOut] = Field(min_length=1, max_length=3)


class DisproofPlanOut(_Out):
    target_rank: Literal[1]
    tool: ToolName
    params: dict[str, Any]  # re-validated against the tool's own schema
    expectation_if_true: str = Field(max_length=300)


class ReflectionItem(_Out):
    rank: int = Field(ge=1, le=3)
    citations_supported: bool
    adjusted_confidence: float = Field(ge=0, le=1)
    note: str = Field(max_length=300)


class ReflectionOut(_Out):
    items: list[ReflectionItem]
    top_verdict: Literal["survived", "disproved"]


ProposedAction = Literal[
    "restart_service", "scale_service", "rollback_deploy", "clear_cache", "none"
]


class ProposalOut(_Out):
    action: ProposedAction
    params: dict[str, Any]  # re-validated against contracts/actions.yaml + live targets
    expected_effect: str = Field(max_length=300)
    rationale: str = Field(max_length=600)
    evidence_refs: list[EvidenceRef] = Field(min_length=1, max_length=8)
