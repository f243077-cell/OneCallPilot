"""The deterministic citation check and its consequences (AI-014, AI-016; §5.5)."""

from __future__ import annotations

from typing import get_args

import pytest
from oncallpilot_contracts.enums import RootCauseCategory

from oncallpilot.agent.citations import CitationCheck, check_citations, rules
from oncallpilot.agent.reflection import apply_reflection
from oncallpilot.agent.schemas import HypothesisOut, ReflectionItem, ReflectionOut

KINDS = {
    "E1": "detector_signal",
    "E2": "log_query",
    "E3": "deploy_list",
    "E4": "metric_query",
    "E5": "runbook_hit",
}


def hyp(category: str, refs: list[str], rank: int = 1, confidence: float = 0.9) -> HypothesisOut:
    return HypothesisOut(
        rank=rank,
        summary=f"a hypothesis of category {category}",
        root_cause_category=category,
        confidence=confidence,
        evidence_refs=refs,
    )


def test_every_category_has_a_rule() -> None:
    assert set(rules()) == set(get_args(RootCauseCategory))


@pytest.mark.parametrize(
    ("category", "refs", "valid", "supported"),
    [
        ("bad_deploy", ["E1", "E3"], ["E1", "E3"], True),
        ("bad_deploy", ["E1", "E4"], ["E1", "E4"], True),
        ("bad_deploy", ["E1", "E2"], ["E1", "E2"], False),  # nothing deploy- or metric-based
        ("traffic_surge", ["E3", "E4"], ["E4"], False),  # deploy list is not compatible
        ("application_bug", ["E2", "E5"], ["E2", "E5"], True),
        ("dependency_slow", ["E3"], [], False),
        ("unknown", ["E1"], ["E1"], True),
        ("memory_leak", ["E9"], [], False),  # unknown ref
    ],
)
def test_check(category: str, refs: list[str], valid: list[str], supported: bool) -> None:
    assert check_citations(hyp(category, refs), KINDS) == CitationCheck(valid, supported)


def test_consequences_in_reflection() -> None:
    hypotheses = [
        hyp("bad_deploy", ["E1", "E2"], 1, 0.9),
        hyp("dependency_slow", ["E3"], 2, 0.8),
        hyp("bad_deploy", ["E3", "E4"], 3, 0.7),
    ]
    verdict = ReflectionOut(
        items=[
            ReflectionItem(rank=r, citations_supported=True, adjusted_confidence=0.9, note="")
            for r in (1, 2, 3)
        ],
        top_verdict="survived",
    )
    checks = {h.rank: check_citations(h, KINDS) for h in hypotheses}
    ranked = apply_reflection(hypotheses, verdict, checks)
    kept = [r for r in ranked if not r.dropped]
    dropped = [r for r in ranked if r.dropped]
    assert [(r.original_rank, r.hypothesis.rank, r.confidence) for r in kept] == [
        (1, 1, 0.45),
        (3, 2, 0.7),
    ]
    assert [r.original_rank for r in dropped] == [2]  # no compatible citation left
