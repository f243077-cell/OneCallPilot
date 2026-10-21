"""What the stack recorded, read from Postgres (BENCH-004 step 6, BENCH-007).

Read-only queries with bound parameters, as the ``ocp_backend`` role through
``DATABASE_URL`` on the integration host. The API does not expose
``benchmark_run_id`` or ``detector_config_version``, which the benchmark needs.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

import psycopg
from psycopg.rows import dict_row

from bench.judge import ExecutionFact, Facts, Hypothesis, ProposalFact


class Db(Protocol):
    def run_incident(self, run_id: str) -> tuple[str, datetime] | None: ...

    def facts(self, incident_id: str, marks: dict[str, Any]) -> Facts: ...


class PostgresDb:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def _rows(self, sql: str, *params: Any) -> list[dict[str, Any]]:
        with psycopg.connect(self._dsn, autocommit=True, connect_timeout=10) as conn:
            conn.execute("set default_transaction_read_only = on")
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, params)
                return list(cur.fetchall())

    def run_incident(self, run_id: str) -> tuple[str, datetime] | None:
        rows = self._rows(
            "select id::text, opened_at from ocp.incidents where benchmark_run_id = %s"
            " order by opened_at limit 1",
            run_id,
        )
        return (rows[0]["id"], rows[0]["opened_at"]) if rows else None

    def facts(self, incident_id: str, marks: dict[str, Any]) -> Facts:
        [incident] = self._rows(
            "select status::text, status_reason, opened_at, resolved_at,"
            " detector_config_version, agent_meta from ocp.incidents where id = %s",
            incident_id,
        )
        evidence = self._rows(
            "select id, ref from ocp.evidence where incident_id = %s", incident_id
        )
        ref_of = {e["id"]: e["ref"] for e in evidence}
        hypotheses = self._rows(
            "select rank, root_cause_category, status, evidence_ids from ocp.hypotheses"
            " where incident_id = %s",
            incident_id,
        )
        proposals = self._rows(
            "select action, params, status::text, created_at, kind::text from ocp.proposals"
            " where incident_id = %s order by created_at",
            incident_id,
        )
        executions = self._rows(
            "select p.action, p.params, e.status::text, p.kind::text from ocp.executions e"
            " join ocp.proposals p on p.id = e.proposal_id where p.incident_id = %s"
            " order by e.created_at",
            incident_id,
        )
        decided = self._rows(
            "select min(a.decided_at) as at from ocp.approvals a"
            " join ocp.proposals p on p.id = a.proposal_id where p.incident_id = %s",
            incident_id,
        )
        return Facts(
            status=incident["status"],
            status_reason=incident["status_reason"],
            opened_at=incident["opened_at"],
            resolved_at=incident["resolved_at"],
            detector_config_version=incident["detector_config_version"],
            agent_meta=dict(incident["agent_meta"] or {}),
            evidence_refs=frozenset(ref_of.values()),
            hypotheses=tuple(
                Hypothesis(
                    h["rank"],
                    h["root_cause_category"],
                    h["status"],
                    tuple(ref_of.get(i, f"?{i}") for i in h["evidence_ids"]),
                )  # fmt: skip
                for h in hypotheses
            ),
            proposals=tuple(
                ProposalFact(p["action"], p["params"], p["status"], p["created_at"], p["kind"])
                for p in proposals
            ),
            executions=tuple(
                ExecutionFact(e["action"], e["params"], e["status"], e["kind"]) for e in executions
            ),
            decided_at=decided[0]["at"] if decided else None,
            marks=marks,
        )
