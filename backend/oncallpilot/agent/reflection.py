"""Code rules of reflection, which always override the model (architecture §5.5; AI-016).

- a hypothesis whose citations are not supported → confidence capped at × 0.5;
- the top hypothesis disproved → dropped, and the next one moves to the top;
- final confidence = min(model-adjusted value, rule cap).
"""

from __future__ import annotations

from oncallpilot.agent.effects import RankedHypothesis
from oncallpilot.agent.schemas import HypothesisOut, ReflectionOut

UNSUPPORTED_FACTOR = 0.5


def apply_reflection(
    hypotheses: list[HypothesisOut],
    reflection: ReflectionOut,
    citations_ok: dict[int, bool],
) -> list[RankedHypothesis]:
    """Ranked hypotheses after the rules; dropped ones come last, kept ones re-ranked 1…n.

    ``citations_ok`` is the deterministic citation check per rank (code); the
    model's own ``citations_supported`` can only lower confidence further.
    """
    items = {item.rank: item for item in reflection.items}
    top_rank = min(h.rank for h in hypotheses)
    ranked: list[RankedHypothesis] = []
    for hypothesis in sorted(hypotheses, key=lambda h: h.rank):
        item = items.get(hypothesis.rank)
        adjusted = item.adjusted_confidence if item else hypothesis.confidence
        supported = citations_ok.get(hypothesis.rank, True) and (
            item.citations_supported if item else True
        )
        cap = hypothesis.confidence * (1.0 if supported else UNSUPPORTED_FACTOR)
        dropped = hypothesis.rank == top_rank and reflection.top_verdict == "disproved"
        ranked.append(
            RankedHypothesis(
                original_rank=hypothesis.rank,
                hypothesis=hypothesis,
                confidence_initial=hypothesis.confidence,
                confidence=round(min(adjusted, cap), 2),
                dropped=dropped,
                reflection_notes=item.note if item else None,
            )
        )
    kept = [r for r in ranked if not r.dropped]  # rank order: the next one moves up
    kept = [
        RankedHypothesis(
            original_rank=r.original_rank,
            hypothesis=r.hypothesis.model_copy(update={"rank": i}),
            confidence_initial=r.confidence_initial,
            confidence=r.confidence,
            dropped=False,
            reflection_notes=r.reflection_notes,
        )
        for i, r in enumerate(kept, start=1)
    ]
    return kept + [r for r in ranked if r.dropped]


def top_confidence(ranked: list[RankedHypothesis]) -> float | None:
    kept = [r for r in ranked if not r.dropped]
    return kept[0].confidence if kept else None
