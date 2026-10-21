"""Checks, unique indexes and triggers of the initial schema (TEST-016, DB-001…DB-010).

Every constraint gets a negative test: a row that breaks it is refused.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import asyncpg
import pytest
from conftest import Rows

pytestmark = pytest.mark.db

TABLES = {
    "incidents",
    "evidence",
    "hypotheses",
    "devices",
    "proposals",
    "approvals",
    "executions",
    "audit_log",
    "runbook_chunks",
    "monitor_settings",
}
RESOLVED_AT = datetime(2026, 10, 21, 11, 0, tzinfo=UTC)


async def refused(conn: asyncpg.Connection, sql: str, *args: object) -> asyncpg.PostgresError:
    """Run ``sql`` in a savepoint and return the Postgres error it must raise."""
    with pytest.raises(asyncpg.PostgresError) as caught:
        async with conn.transaction():
            await conn.execute(sql, *args)
    return caught.value


async def refused_call(conn: asyncpg.Connection, call: object) -> asyncpg.PostgresError:
    """Await ``call`` (an insert from ``Rows``) in a savepoint; it must raise."""
    with pytest.raises(asyncpg.PostgresError) as caught:
        async with conn.transaction():
            await call  # type: ignore[misc]
    return caught.value


# --- DB-001 -------------------------------------------------------------------


async def test_schema_extensions_and_tables(conn: asyncpg.Connection) -> None:
    extensions = {r["extname"] for r in await conn.fetch("select extname from pg_extension")}
    assert {"vector", "pgcrypto"} <= extensions
    tables = {
        r["tablename"]
        for r in await conn.fetch("select tablename from pg_tables where schemaname = 'ocp'")
    }
    assert tables == TABLES
    public = await conn.fetchval(
        "select count(*) from pg_tables where schemaname = 'public' and tablename = any($1)",
        list(TABLES),
    )
    assert public == 0


# --- DB-002 incidents ---------------------------------------------------------


async def test_resolved_needs_resolved_at_and_resolution(
    conn: asyncpg.Connection, rows: Rows
) -> None:
    err = await refused_call(conn, rows.incident(status="resolved", resolution="manual"))
    assert isinstance(err, asyncpg.CheckViolationError)
    err = await refused_call(conn, rows.incident(status="resolved", resolved_at=RESOLVED_AT))
    assert isinstance(err, asyncpg.CheckViolationError)


async def test_resolved_at_only_when_resolved(conn: asyncpg.Connection, rows: Rows) -> None:
    err = await refused_call(conn, rows.incident(resolved_at=RESOLVED_AT, resolution="manual"))
    assert isinstance(err, asyncpg.CheckViolationError)


async def test_one_active_incident_per_service(conn: asyncpg.Connection, rows: Rows) -> None:
    await rows.incident(
        service="api", status="resolved", resolved_at=RESOLVED_AT, resolution="manual"
    )
    await rows.incident(service="api")
    await rows.incident(service="worker")
    err = await refused_call(conn, rows.incident(service="api", status="escalated"))
    assert isinstance(err, asyncpg.UniqueViolationError)


async def test_incident_enums_refuse_unknown_values(conn: asyncpg.Connection, rows: Rows) -> None:
    err = await refused_call(conn, rows.incident(severity="sev4"))
    assert isinstance(err, asyncpg.InvalidTextRepresentationError)
    err = await refused_call(conn, rows.incident(status="closed"))
    assert isinstance(err, asyncpg.InvalidTextRepresentationError)


# --- DB-003 evidence ----------------------------------------------------------


async def test_duplicate_evidence_ref_is_refused(conn: asyncpg.Connection, rows: Rows) -> None:
    incident = await rows.incident()
    await rows.evidence(incident, "E1")
    err = await refused_call(conn, rows.evidence(incident, "E1"))
    assert isinstance(err, asyncpg.UniqueViolationError)
    other = await rows.incident(service="worker")
    await rows.evidence(other, "E1")


@pytest.mark.parametrize("ref", ["E", "e1", "X1", "E1000", "E1a", " E1"])
async def test_evidence_ref_format(conn: asyncpg.Connection, rows: Rows, ref: str) -> None:
    incident = await rows.incident()
    err = await refused_call(conn, rows.evidence(incident, ref))
    assert isinstance(err, asyncpg.CheckViolationError)


async def test_evidence_needs_an_existing_incident(conn: asyncpg.Connection, rows: Rows) -> None:
    err = await refused_call(conn, rows.evidence(uuid.uuid4()))
    assert isinstance(err, asyncpg.ForeignKeyViolationError)


async def test_incident_with_evidence_cannot_be_deleted(
    conn: asyncpg.Connection, rows: Rows
) -> None:
    incident = await rows.incident()
    await rows.evidence(incident)
    err = await refused(conn, "delete from ocp.incidents where id = $1", incident)
    assert isinstance(err, asyncpg.ForeignKeyViolationError)


# --- DB-004 hypotheses --------------------------------------------------------


async def test_two_active_rank_one_hypotheses_fail(conn: asyncpg.Connection, rows: Rows) -> None:
    incident = await rows.incident()
    e1 = await rows.evidence(incident)
    await rows.hypothesis(incident, [e1], rank=1)
    err = await refused_call(conn, rows.hypothesis(incident, [e1], rank=1))
    assert isinstance(err, asyncpg.UniqueViolationError)
    # A dropped hypothesis frees its rank.
    await rows.hypothesis(incident, [e1], rank=2, status="dropped")
    await rows.hypothesis(incident, [e1], rank=2)


async def test_hypothesis_needs_evidence(conn: asyncpg.Connection, rows: Rows) -> None:
    incident = await rows.incident()
    err = await refused_call(conn, rows.hypothesis(incident, []))
    assert isinstance(err, asyncpg.CheckViolationError)


@pytest.mark.parametrize(
    "cols",
    [
        {"rank": 0},
        {"rank": 4},
        {"confidence": 1.5},
        {"confidence": -0.1},
        {"confidence_initial": 1.01},
        {"status": "archived"},
    ],
)
async def test_hypothesis_checks(
    conn: asyncpg.Connection, rows: Rows, cols: dict[str, Any]
) -> None:
    incident = await rows.incident()
    e1 = await rows.evidence(incident)
    err = await refused_call(conn, rows.hypothesis(incident, [e1], **cols))
    # numeric(3,2) overflows before the check for values >= 10; these do not.
    assert isinstance(err, asyncpg.CheckViolationError)


# --- DB-005 proposals ---------------------------------------------------------


async def test_second_pending_proposal_fails(conn: asyncpg.Connection, rows: Rows) -> None:
    incident = await rows.incident()
    await rows.proposal(incident)
    err = await refused_call(conn, rows.proposal(incident))
    assert isinstance(err, asyncpg.UniqueViolationError)
    await rows.proposal(incident, status="rejected")


async def test_rollback_needs_a_parent_and_primary_has_none(
    conn: asyncpg.Connection, rows: Rows
) -> None:
    incident = await rows.incident()
    parent = await rows.proposal(incident, status="failed")
    err = await refused_call(conn, rows.proposal(incident, kind="rollback"))
    assert isinstance(err, asyncpg.CheckViolationError)
    err = await refused_call(conn, rows.proposal(incident, parent_proposal_id=parent))
    assert isinstance(err, asyncpg.CheckViolationError)
    await rows.proposal(incident, kind="rollback", parent_proposal_id=parent)


@pytest.mark.parametrize(
    ("cols", "error"),
    [
        ({"approval_requirement": "none"}, asyncpg.CheckViolationError),
        ({"risk_tier": "critical"}, asyncpg.InvalidTextRepresentationError),
        ({"status": "done"}, asyncpg.InvalidTextRepresentationError),
        ({"hypothesis_id": uuid.uuid4()}, asyncpg.ForeignKeyViolationError),
        ({"superseded_by": uuid.uuid4()}, asyncpg.ForeignKeyViolationError),
    ],
)
async def test_proposal_checks(
    conn: asyncpg.Connection,
    rows: Rows,
    cols: dict[str, Any],
    error: type[asyncpg.PostgresError],
) -> None:
    incident = await rows.incident()
    err = await refused_call(conn, rows.proposal(incident, **cols))
    assert isinstance(err, error)


# --- DB-006 approvals ---------------------------------------------------------


async def test_second_decision_on_a_proposal_fails(conn: asyncpg.Connection, rows: Rows) -> None:
    proposal = await rows.proposal(await rows.incident())
    await rows.approval(proposal, "approved")
    err = await refused_call(conn, rows.approval(proposal, "rejected"))
    assert isinstance(err, asyncpg.UniqueViolationError)


async def test_idempotency_key_is_unique(conn: asyncpg.Connection, rows: Rows) -> None:
    incident = await rows.incident()
    first = await rows.proposal(incident, status="rejected")
    second = await rows.proposal(incident)
    await rows.approval(first, "rejected", idempotency_key="key-1")
    err = await refused_call(conn, rows.approval(second, idempotency_key="key-1"))
    assert isinstance(err, asyncpg.UniqueViolationError)


@pytest.mark.parametrize("reason", [None, "", "no"])
async def test_rejection_needs_a_reason(
    conn: asyncpg.Connection, rows: Rows, reason: str | None
) -> None:
    proposal = await rows.proposal(await rows.incident())
    err = await refused_call(conn, rows.approval(proposal, "rejected", reason=reason))
    assert isinstance(err, asyncpg.CheckViolationError)


async def test_approval_needs_a_challenge(conn: asyncpg.Connection, rows: Rows) -> None:
    proposal = await rows.proposal(await rows.incident())
    err = await refused_call(conn, rows.approval(proposal, "approved", challenge_id=None))
    assert isinstance(err, asyncpg.CheckViolationError)


@pytest.mark.parametrize(
    ("cols", "error"),
    [
        ({"auth_method": "pin"}, asyncpg.CheckViolationError),
        ({"device_id": uuid.uuid4()}, asyncpg.ForeignKeyViolationError),
    ],
)
async def test_approval_checks(
    conn: asyncpg.Connection,
    rows: Rows,
    cols: dict[str, Any],
    error: type[asyncpg.PostgresError],
) -> None:
    proposal = await rows.proposal(await rows.incident())
    err = await refused_call(conn, rows.approval(proposal, **cols))
    assert isinstance(err, error)


async def test_approval_needs_an_existing_proposal(conn: asyncpg.Connection, rows: Rows) -> None:
    err = await refused_call(conn, rows.approval(uuid.uuid4()))
    assert isinstance(err, asyncpg.ForeignKeyViolationError)


# --- DB-007 executions --------------------------------------------------------


async def test_execution_for_a_rejected_approval_raises(
    conn: asyncpg.Connection, rows: Rows
) -> None:
    proposal = await rows.proposal(await rows.incident())
    approval = await rows.approval(proposal, "rejected")
    err = await refused_call(conn, rows.execution(proposal, approval))
    assert isinstance(err, asyncpg.CheckViolationError)
    assert "approved approval" in str(err)


async def test_execution_for_another_proposals_approval_raises(
    conn: asyncpg.Connection, rows: Rows
) -> None:
    incident = await rows.incident()
    approved = await rows.proposal(incident, status="approved")
    other = await rows.proposal(incident)
    approval = await rows.approval(approved, "approved")
    err = await refused_call(conn, rows.execution(other, approval))
    assert isinstance(err, asyncpg.CheckViolationError)


async def test_execution_without_an_approval_raises(conn: asyncpg.Connection, rows: Rows) -> None:
    proposal = await rows.proposal(await rows.incident())
    err = await refused_call(conn, rows.execution(proposal, uuid.uuid4()))
    assert isinstance(err, asyncpg.CheckViolationError)


async def test_one_execution_per_approved_proposal(conn: asyncpg.Connection, rows: Rows) -> None:
    proposal = await rows.proposal(await rows.incident(), status="approved")
    approval = await rows.approval(proposal, "approved")
    await rows.execution(proposal, approval)
    err = await refused_call(conn, rows.execution(proposal, approval))
    assert isinstance(err, asyncpg.UniqueViolationError)


async def test_execution_status_enum(conn: asyncpg.Connection, rows: Rows) -> None:
    proposal = await rows.proposal(await rows.incident(), status="approved")
    approval = await rows.approval(proposal, "approved")
    execution = await rows.execution(proposal, approval)
    err = await refused(
        conn, "update ocp.executions set status = 'cancelled' where id = $1", execution
    )
    assert isinstance(err, asyncpg.InvalidTextRepresentationError)


# --- DB-008 audit_log (owner side; the grant side is in test_privileges.py) ---


async def test_audit_log_refuses_update_delete_truncate_as_owner(
    conn: asyncpg.Connection, rows: Rows
) -> None:
    entry = await rows.audit()
    err = await refused(conn, "update ocp.audit_log set actor = 'x' where id = $1", entry)
    assert isinstance(err, asyncpg.InsufficientPrivilegeError)
    assert "append-only" in str(err)
    err = await refused(conn, "delete from ocp.audit_log where id = $1", entry)
    assert isinstance(err, asyncpg.InsufficientPrivilegeError)
    err = await refused(conn, "truncate ocp.audit_log")
    assert isinstance(err, asyncpg.InsufficientPrivilegeError)
    assert await conn.fetchval("select count(*) from ocp.audit_log where id = $1", entry) == 1


async def test_audit_log_id_is_generated_always(conn: asyncpg.Connection) -> None:
    err = await refused(conn, "insert into ocp.audit_log (id, actor, event) values (1, 'a', 'b')")
    assert isinstance(err, asyncpg.GeneratedAlwaysError)


# --- DB-009 runbook_chunks ----------------------------------------------------


def _vector(value: float, dims: int = 768) -> str:
    return "[" + ",".join([str(value)] * dims) + "]"


async def _chunk(conn: asyncpg.Connection, index: int, vector: str) -> None:
    await conn.execute(
        "insert into ocp.runbook_chunks"
        " (source, chunk_index, content, content_hash, embedding, embedding_model)"
        " values ('runbooks/rollback.md', $1, 'text', 'h', $2::vector, 'gemini-embedding-001')",
        index,
        vector,
    )


async def test_runbook_chunk_dimension_and_uniqueness(conn: asyncpg.Connection) -> None:
    await _chunk(conn, 0, _vector(0.1))
    with pytest.raises(asyncpg.PostgresError) as caught:
        async with conn.transaction():
            await _chunk(conn, 0, _vector(0.2))
    assert isinstance(caught.value, asyncpg.UniqueViolationError)
    with pytest.raises(asyncpg.PostgresError) as caught:
        async with conn.transaction():
            await _chunk(conn, 1, _vector(0.1, dims=3072))
    assert "768" in str(caught.value)


async def test_runbook_search_uses_the_hnsw_index(conn: asyncpg.Connection) -> None:
    for i in range(5):
        await _chunk(conn, i, _vector(0.1 + i / 100))
    await conn.execute("set local enable_seqscan = off")
    plan = await conn.fetch(
        "explain select id from ocp.runbook_chunks order by embedding <=> $1::vector limit 3",
        _vector(0.12),
    )
    text = "\n".join(r[0] for r in plan)
    assert "runbook_chunks_embedding" in text


# --- DB-010 devices and monitor_settings --------------------------------------


async def test_monitor_settings_is_a_single_row(conn: asyncpg.Connection) -> None:
    await conn.execute("insert into ocp.monitor_settings (version, settings) values (1, '{}')")
    err = await refused(
        conn, "insert into ocp.monitor_settings (version, settings) values (2, '{}')"
    )
    assert isinstance(err, asyncpg.UniqueViolationError)
    err = await refused(
        conn, "insert into ocp.monitor_settings (id, version, settings) values (2, 1, '{}')"
    )
    assert isinstance(err, asyncpg.CheckViolationError)


async def test_device_checks(conn: asyncpg.Connection, rows: Rows) -> None:
    await rows.device(fcm_token="token-1")
    err = await refused_call(conn, rows.device(fcm_token="token-1"))
    assert isinstance(err, asyncpg.UniqueViolationError)
    err = await refused_call(conn, rows.device(platform="ios"))
    assert isinstance(err, asyncpg.CheckViolationError)
