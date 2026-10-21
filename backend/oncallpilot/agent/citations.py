"""The deterministic citation check of reflection (architecture §5.5 step 1; AI-014).

Rules live in ``reflection_rules.yaml``. Every cited ref must exist and have a
kind compatible with the hypothesis's category; incompatible citations are
removed. The hypothesis is *supported* when nothing was removed and at least
one citation has a required kind. Code applies the consequences (×0.5, or
dropped when no citation is left) in ``reflection.apply_reflection``.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from pathlib import Path

import yaml

from oncallpilot.agent.schemas import HypothesisOut

RULES_FILE = Path(__file__).resolve().parent / "reflection_rules.yaml"


@dataclass(frozen=True)
class CitationCheck:
    valid_refs: list[str]
    supported: bool


@dataclass(frozen=True)
class _Rule:
    compatible: frozenset[str]
    requires: frozenset[str]


@cache
def rules() -> dict[str, _Rule]:
    data = yaml.safe_load(RULES_FILE.read_text(encoding="utf-8"))
    always = frozenset(data["always_compatible"])
    return {
        category: _Rule(frozenset(rule["compatible"]) | always, frozenset(rule["requires"]))
        for category, rule in data["categories"].items()
    }


def check_citations(hypothesis: HypothesisOut, kinds: dict[str, str]) -> CitationCheck:
    """``kinds``: evidence kind by ref for this incident."""
    rule = rules()[hypothesis.root_cause_category]
    valid = [r for r in hypothesis.evidence_refs if kinds.get(r) in rule.compatible]
    required = not rule.requires or any(kinds.get(r) in rule.requires for r in valid)
    return CitationCheck(
        valid_refs=valid,
        supported=len(valid) == len(hypothesis.evidence_refs) and required,
    )
