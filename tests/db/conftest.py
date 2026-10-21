"""Fixtures for the database suite (TEST-016, DB-001…DB-011).

Every session creates a fresh database on the server named by
``OCP_TEST_DATABASE_URL`` (a superuser DSN to a ``pgvector/pgvector``
container, never Supabase), applies every file of ``supabase/migrations/`` in
order, and drops the database at the end. Each test runs inside a transaction
that is rolled back, so tests never see each other's rows.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest

T0 = datetime(2026, 10, 21, 10, 0, tzinfo=UTC)
MIGRATIONS = Path(__file__).resolve().parents[2] / "supabase" / "migrations"


def _admin_dsn() -> str:
    dsn = os.environ.get("OCP_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("OCP_TEST_DATABASE_URL is not set (needs a pgvector/pgvector container)")
    return dsn


def _with_database(dsn: str, name: str) -> str:
    base, _, _ = dsn.rpartition("/")
    return f"{base}/{name}"


async def _create_and_migrate(admin_dsn: str, name: str) -> None:
    admin = await asyncpg.connect(admin_dsn, timeout=10)
    try:
        await admin.execute(f'create database "{name}"')
    finally:
        await admin.close()
    conn = await asyncpg.connect(_with_database(admin_dsn, name), timeout=10)
    try:
        for path in sorted(MIGRATIONS.glob("*.sql")):
            await conn.execute(path.read_text(encoding="utf-8"))
    finally:
        await conn.close()


async def _drop(admin_dsn: str, name: str) -> None:
    admin = await asyncpg.connect(admin_dsn, timeout=10)
    try:
        await admin.execute(f'drop database if exists "{name}" with (force)')
    finally:
        await admin.close()


@pytest.fixture(scope="session")
def migrated_dsn() -> Iterator[str]:
    """DSN of a fresh database with every migration applied."""
    admin_dsn = _admin_dsn()
    name = f"ocp_test_{uuid.uuid4().hex[:12]}"
    asyncio.run(_create_and_migrate(admin_dsn, name))
    try:
        yield _with_database(admin_dsn, name)
    finally:
        asyncio.run(_drop(admin_dsn, name))


@pytest.fixture
async def conn(migrated_dsn: str) -> AsyncIterator[asyncpg.Connection]:
    """A superuser connection inside a transaction that is rolled back."""
    connection = await asyncpg.connect(migrated_dsn, timeout=10)
    tx = connection.transaction()
    await tx.start()
    try:
        await connection.execute("set search_path = ocp, public")
        yield connection
    finally:
        await tx.rollback()
        await connection.close()


class Rows:
    """Inserts valid rows with sensible defaults; tests override one field to break it."""

    def __init__(self, conn: asyncpg.Connection) -> None:
        self.conn = conn

    async def incident(self, service: str = "api", **cols: object) -> uuid.UUID:
        values: dict[str, object] = {
            "service": service,
            "severity": "sev2",
            "title": "error rate high on api",
            "trigger": "{}",
            "window_start": T0,
            "detector_config_version": "1",
        }
        values.update(cols)
        return await self._insert("incidents", values)

    async def evidence(self, incident_id: uuid.UUID, ref: str = "E1", **cols: object) -> uuid.UUID:
        values: dict[str, object] = {
            "incident_id": incident_id,
            "ref": ref,
            "kind": "detector_signal",
            "purpose": "seed",
            "summary": "error rate 12 %",
            "payload": "{}",
        }
        values.update(cols)
        return await self._insert("evidence", values)

    async def hypothesis(
        self, incident_id: uuid.UUID, evidence_ids: list[uuid.UUID], rank: int = 1, **cols: object
    ) -> uuid.UUID:
        values: dict[str, object] = {
            "incident_id": incident_id,
            "rank": rank,
            "summary": "release 1.5.0 raises IndexError",
            "root_cause_category": "bad_deploy",
            "confidence": Decimal("0.80"),
            "confidence_initial": Decimal("0.85"),
            "evidence_ids": evidence_ids,
        }
        values.update(cols)
        return await self._insert("hypotheses", values)

    async def proposal(self, incident_id: uuid.UUID, **cols: object) -> uuid.UUID:
        values: dict[str, object] = {
            "incident_id": incident_id,
            "action": "rollback_deploy",
            "params": '{"service": "api", "to_release": "1.4.0"}',
            "risk_tier": "medium",
            "approval_requirement": "biometric",
            "expected_effect": "error rate back under 1 %",
            "rationale": "E1 shows the error started with the deploy",
            "dry_run_result": "{}",
            "dry_run_at": T0 + timedelta(minutes=5),
            "fingerprint": "sha256:00",
            "expires_at": T0 + timedelta(minutes=20),
            "catalogue_version": "1",
        }
        values.update(cols)
        return await self._insert("proposals", values)

    async def approval(
        self, proposal_id: uuid.UUID, decision: str = "approved", **cols: object
    ) -> uuid.UUID:
        values: dict[str, object] = {
            "proposal_id": proposal_id,
            "user_id": uuid.uuid4(),
            "decision": decision,
            "auth_method": "biometric",
            "idempotency_key": uuid.uuid4().hex,
        }
        if decision == "approved":
            values["challenge_id"] = uuid.uuid4()
        else:
            values["reason"] = "not now"
        values.update(cols)
        return await self._insert("approvals", values)

    async def execution(self, proposal_id: uuid.UUID, approval_id: uuid.UUID) -> uuid.UUID:
        return await self._insert(
            "executions", {"proposal_id": proposal_id, "approval_id": approval_id}
        )

    async def device(self, **cols: object) -> uuid.UUID:
        values: dict[str, object] = {
            "user_id": uuid.uuid4(),
            "fcm_token": uuid.uuid4().hex,
            "platform": "android",
        }
        values.update(cols)
        return await self._insert("devices", values)

    async def audit(self, **cols: object) -> int:
        values: dict[str, object] = {"actor": "system", "event": "incident.opened"}
        values.update(cols)
        names = ", ".join(values)
        params = ", ".join(f"${i}" for i in range(1, len(values) + 1))
        row = await self.conn.fetchval(
            f"insert into ocp.audit_log ({names}) values ({params}) returning id",
            *values.values(),
        )
        assert isinstance(row, int)
        return row

    async def _insert(self, table: str, values: dict[str, object]) -> uuid.UUID:
        names = ", ".join(values)
        params = ", ".join(f"${i}" for i in range(1, len(values) + 1))
        row = await self.conn.fetchval(
            f"insert into ocp.{table} ({names}) values ({params}) returning id",
            *values.values(),
        )
        assert isinstance(row, uuid.UUID)
        return row


@pytest.fixture
def rows(conn: asyncpg.Connection) -> Rows:
    return Rows(conn)
