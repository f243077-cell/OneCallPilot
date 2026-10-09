"""Valid sample payloads for the contract tests.

Each function returns a fresh JSON-ready dict that validates against its
model. Tests change one field at a time to check that a rule rejects it.
The samples describe one incident: the api error spike after release 1.5.0
(scenario 2), waiting for approval of a rollback to 1.4.0.
"""

import hashlib
from typing import Any

INCIDENT_ID = "6f1c2a4e-0b7d-4c1e-9a53-2d8e4f6a7b01"
PROPOSAL_ID = "8a2d3b5f-1c8e-4d2f-8b64-3e9f5a7b8c02"


def uid(n: int) -> str:
    """A deterministic version-4 UUID for item number ``n``."""
    return f"00000000-0000-4000-8000-{n:012d}"


def sha(label: str) -> str:
    """A well-formed SHA-256 hex digest derived from ``label``."""
    return hashlib.sha256(label.encode()).hexdigest()


def seed_evidence() -> dict[str, Any]:
    return {
        "id": uid(1),
        "ref": "E1",
        "kind": "detector_signal",
        "purpose": "seed",
        "tool_name": None,
        "summary": "api error rate 31% (baseline 0.4%), z-score 9.2",
        "payload": {
            "service": "api",
            "signals": [
                {
                    "name": "error_rate",
                    "value": 0.31,
                    "baseline": 0.004,
                    "zscore": 9.2,
                    "first_anomalous_at": "2026-10-14T09:00:10Z",
                }
            ],
            "observed_at": "2026-10-14T09:00:25Z",
        },
        "suspicious_content": False,
        "created_at": "2026-10-14T09:00:25Z",
    }


def log_evidence() -> dict[str, Any]:
    return {
        "id": uid(2),
        "ref": "E2",
        "kind": "log_query",
        "purpose": "gather",
        "tool_name": "query_logs",
        "summary": "41 ERROR lines on /checkout, all KeyError: 'unit_price'",
        "payload": {
            "lines": [
                {
                    "ts": "2026-10-14T09:00:12Z",
                    "level": "ERROR",
                    "msg": "POST /checkout 500",
                    "exc_type": "KeyError",
                }
            ],
            "total_count": 41,
            "top_exception_types": [{"exc_type": "KeyError", "count": 41}],
        },
        "suspicious_content": False,
        "created_at": "2026-10-14T09:00:31Z",
    }


def metric_evidence() -> dict[str, Any]:
    return {
        "id": uid(3),
        "ref": "E3",
        "kind": "metric_query",
        "purpose": "gather",
        "tool_name": "query_metrics",
        "summary": "api error rate rose from 0.4% to 31% at 09:00:05",
        "payload": {
            "template": "error_rate",
            "service": "api",
            "step_seconds": 15,
            "points": [
                {"ts": "2026-10-14T08:59:45Z", "value": 0.004},
                {"ts": "2026-10-14T09:00:00Z", "value": 0.005},
                {"ts": "2026-10-14T09:00:15Z", "value": 0.29},
                {"ts": "2026-10-14T09:00:30Z", "value": 0.31},
            ],
            "baseline_mean": 0.004,
            "peak": 0.31,
            "pct_change": 7650.0,
            "change_point_at": "2026-10-14T09:00:05Z",
            "note": None,
        },
        "suspicious_content": False,
        "created_at": "2026-10-14T09:00:36Z",
    }


def deploy_evidence() -> dict[str, Any]:
    return {
        "id": uid(4),
        "ref": "E4",
        "kind": "deploy_list",
        "purpose": "gather",
        "tool_name": "get_recent_deploys",
        "summary": "api 1.5.0 deployed at 08:58:40, 90 s before the error spike",
        "payload": {
            "records": [
                {
                    "service": "api",
                    "release": "1.5.0",
                    "commit_sha": sha("api 1.5.0")[:40],
                    "commit_message": "checkout: compute totals with new pricing rounding",
                    "config_hash": sha("api config 1.5.0"),
                    "deployed_at": "2026-10-14T08:58:40Z",
                    "deployed_by": "ci",
                    "kind": "deploy",
                },
                {
                    "service": "api",
                    "release": "1.4.0",
                    "commit_sha": sha("api 1.4.0")[:40],
                    "commit_message": "orders: paginate order history",
                    "config_hash": sha("api config 1.4.0"),
                    "deployed_at": "2026-10-13T16:20:00Z",
                    "deployed_by": "setup",
                    "kind": "deploy",
                },
            ]
        },
        "suspicious_content": False,
        "created_at": "2026-10-14T09:00:40Z",
    }


def health_evidence() -> dict[str, Any]:
    return {
        "id": uid(5),
        "ref": "E5",
        "kind": "service_health",
        "purpose": "gather",
        "tool_name": "get_service_health",
        "summary": "cs-api-150-1 running and healthy; /healthz 200",
        "payload": {
            "service": "api",
            "containers": [
                {
                    "name": "cs-api-150-1",
                    "release": "1.5.0",
                    "state": "running",
                    "health": "healthy",
                    "restart_count": 0,
                    "oom_killed": False,
                    "exit_code": None,
                    "started_at": "2026-10-14T08:58:31Z",
                }
            ],
            "probe": {
                "url": "http://cs-lb/healthz",
                "ok": True,
                "status_code": 200,
                "latency_ms": 4.1,
                "error": None,
            },
        },
        "suspicious_content": False,
        "created_at": "2026-10-14T09:00:44Z",
    }


def runbook_evidence() -> dict[str, Any]:
    return {
        "id": uid(6),
        "ref": "E6",
        "kind": "runbook_hit",
        "purpose": "gather",
        "tool_name": "search_runbooks",
        "summary": "Runbook: roll back a release",
        "payload": {
            "chunks": [
                {
                    "source": "runbooks/rollback.md",
                    "heading": "When to roll back",
                    "content": (
                        "If errors start right after a deploy, roll back to the last good release."
                    ),
                    "similarity": 0.83,
                }
            ]
        },
        "suspicious_content": False,
        "created_at": "2026-10-14T09:00:49Z",
    }


def all_evidence() -> list[dict[str, Any]]:
    return [
        seed_evidence(),
        log_evidence(),
        metric_evidence(),
        deploy_evidence(),
        health_evidence(),
        runbook_evidence(),
    ]


def hypothesis(rank: int = 1) -> dict[str, Any]:
    return {
        "id": uid(20 + rank),
        "rank": rank,
        "summary": "Release 1.5.0 of the api fails at checkout, starting right after its deploy",
        "root_cause_category": "bad_deploy",
        "confidence": 0.86,
        "confidence_initial": 0.9,
        "evidence_refs": ["E2", "E3", "E4"],
        "status": "active",
        "reflection_notes": (
            "Citations support the claim; the disproof query found no error before the deploy."
        ),
    }


def dry_run() -> dict[str, Any]:
    return {
        "would_change": [
            {"target": "cs-api-140-1", "change": "start"},
            {"target": "cs-api-150-1", "change": "stop"},
        ],
        "current": {
            "active_release": "1.5.0",
            "running_replicas": 1,
            "containers": [
                {"name": "cs-api-150-1", "state": "running", "health": "healthy"},
                {"name": "cs-api-140-1", "state": "exited", "health": "none"},
            ],
            "cache_keys": None,
        },
        "preconditions": {"rate_limit_ok": True, "cooldown_ok": True, "slots_available": True},
        "predicted_effect": "1 replica of api 1.4.0 serves traffic; 1.5.0 is stopped",
        "warnings": [],
        "state_fingerprint": sha("api 1.5.0 x1 cs-api-140-1 cs-api-150-1"),
    }


def proposal() -> dict[str, Any]:
    return {
        "id": PROPOSAL_ID,
        "incident_id": INCIDENT_ID,
        "kind": "primary",
        "parent_proposal_id": None,
        "action": "rollback_deploy",
        "params": {"service": "api", "target_release": "1.4.0"},
        "risk_tier": "medium",
        "approval_requirement": "biometric",
        "expected_effect": "Checkout errors stop once 1.4.0 serves traffic",
        "rationale": (
            "Errors began 90 s after 1.5.0 was deployed (E4) and all come from checkout (E2)."
        ),
        "evidence_refs": ["E2", "E3", "E4"],
        "rollback_step": {
            "action": "rollback_deploy",
            "params": {"service": "api", "target_release": "1.5.0"},
        },
        "dry_run_result": dry_run(),
        "dry_run_at": "2026-10-14T09:01:02Z",
        "fingerprint": sha("proposal fingerprint"),
        "status": "pending",
        "status_reason": None,
        "expires_at": "2026-10-14T09:16:05Z",
        "created_at": "2026-10-14T09:01:05Z",
    }


def execution() -> dict[str, Any]:
    return {
        "id": uid(40),
        "proposal_id": PROPOSAL_ID,
        "status": "succeeded",
        "steps": [
            {
                "at": "2026-10-14T09:02:01Z",
                "step": "validated",
                "status": "succeeded",
                "message": "signature, catalogue, targets and fingerprint checked",
            }
        ],
        "error_code": None,
        "health_after": {
            "structural": {
                "passed": True,
                "checked_at": "2026-10-14T09:02:30Z",
                "containers": [
                    {"name": "cs-api-140-1", "state": "running", "health": "healthy"},
                ],
                "probes": [
                    {
                        "url": "http://cs-lb/healthz",
                        "ok": True,
                        "status_code": 200,
                        "latency_ms": 3.0,
                        "error": None,
                    }
                ],
            },
            "signals": {
                "passed": True,
                "checked_at": "2026-10-14T09:03:31Z",
                "window_seconds": 60,
                "signals": ["error_rate"],
            },
            "verdict": "pass",
        },
        "started_at": "2026-10-14T09:02:00Z",
        "finished_at": "2026-10-14T09:02:30Z",
    }


def incident_summary() -> dict[str, Any]:
    return {
        "id": INCIDENT_ID,
        "service": "api",
        "severity": "sev1",
        "status": "awaiting_approval",
        "status_reason": None,
        "title": "api error rate 31%",
        "opened_at": "2026-10-14T09:00:25Z",
        "resolved_at": None,
        "resolution": None,
        "state_version": 9,
        "top_hypothesis": {
            "summary": hypothesis()["summary"],
            "confidence": 0.86,
            "root_cause_category": "bad_deploy",
        },
        "pending_proposal_id": PROPOSAL_ID,
    }


def incident_detail() -> dict[str, Any]:
    return {
        **incident_summary(),
        "window_start": "2026-10-14T08:50:10Z",
        "window_end": None,
        "trigger": {
            "source": "detector",
            "signals": seed_evidence()["payload"]["signals"],
            "observed_at": "2026-10-14T09:00:25Z",
            "updates": [],
        },
        "evidence": all_evidence(),
        "hypotheses": [hypothesis(1)],
        "proposals": [proposal()],
        "executions": [],
        "agent_meta": {
            "prompt_version": "v1",
            "provider": "fake",
            "model": "fake-scripted",
            "catalogue_version": "1.0.0",
            "agent_config_hash": sha("agent config"),
            "tool_calls": 7,
            "llm_calls": 11,
            "input_tokens": 18240,
            "output_tokens": 1630,
            "duration_ms": 38400,
            "terminal_state": "PROPOSED",
            "run_started_at": "2026-10-14T09:00:26Z",
        },
    }


def approval() -> dict[str, Any]:
    return {
        "id": uid(50),
        "proposal_id": PROPOSAL_ID,
        "user_id": uid(51),
        "decision": "approved",
        "reason": None,
        "auth_method": "biometric",
        "challenge_id": uid(52),
        "device_id": uid(53),
        "decided_at": "2026-10-14T09:01:58Z",
    }
