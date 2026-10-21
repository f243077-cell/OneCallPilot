"""One-line evidence summaries per kind (architecture §5.3 step 5; ≤ 300 chars).

Built by code from the (already redacted) payload, never by the model.
"""

from __future__ import annotations

from typing import Any

from oncallpilot_contracts.enums import EvidenceKind


def _num(value: Any) -> str:
    if value is None:
        return "n/a"
    number = float(value)
    return f"{number:.3g}"


def summarise(kind: EvidenceKind, params: dict[str, Any], payload: dict[str, Any]) -> str:
    if kind == "log_query":
        level = params.get("level", "ANY")
        what = "" if level == "ANY" else f" {level}"
        text = f"{payload.get('total_count', 0)}{what} log lines on {params.get('service')}"
        if params.get("contains"):
            text += f" containing {params['contains']!r}"
        top = payload.get("top_exception_types") or []
        if top:
            text += "; top: " + ", ".join(f"{t['exc_type']} x{t['count']}" for t in top[:3])
    elif kind == "metric_query":
        if payload.get("note"):
            text = f"{payload.get('template')} on {payload.get('service')}: {payload['note']}"
        else:
            text = (
                f"{payload.get('template')} on {payload.get('service')}: baseline "
                f"{_num(payload.get('baseline_mean'))}, peak {_num(payload.get('peak'))}"
            )
            if payload.get("pct_change") is not None:
                text += f" ({payload['pct_change']:+.0f}%)"
    elif kind == "deploy_list":
        records = payload.get("records") or []
        if records:
            latest = records[0]
            text = (
                f"{len(records)} ledger records; latest: {latest['service']} "
                f"{latest['release']} ({latest['kind']}) at {latest['deployed_at']}"
            )
        else:
            text = "no deploy-ledger records"
    elif kind == "service_health":
        containers = payload.get("containers") or []
        running = sum(c.get("state") == "running" for c in containers)
        restarts = sum(int(c.get("restart_count") or 0) for c in containers)
        text = (
            f"{payload.get('service')}: {running}/{len(containers)} containers running, "
            f"{restarts} restarts"
        )
        probe = payload.get("probe")
        if probe:
            text += "; /healthz ok" if probe.get("ok") else "; /healthz failing"
    elif kind == "runbook_hit":
        chunks = payload.get("chunks") or []
        if chunks:
            first = chunks[0]
            text = f"{len(chunks)} runbook passages; top: {first['source']}"
            if first.get("heading"):
                text += f" ({first['heading']})"
        else:
            text = "no matching runbook passages"
    else:
        text = f"{kind} evidence"
    return text[:300]
