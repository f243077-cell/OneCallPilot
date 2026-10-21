"""Grants and row-level security (TEST-016, DB-008, DB-011, DB-012).

Tests switch to ``ocp_backend`` with ``SET LOCAL ROLE`` inside the rolled-back
test transaction, so the privilege checks are the real ones.
"""

from __future__ import annotations

import asyncpg
import pytest
from conftest import Rows
from test_constraints import TABLES, refused

pytestmark = pytest.mark.db

WRITABLE = sorted(TABLES - {"audit_log"})


async def as_backend(conn: asyncpg.Connection) -> None:
    await conn.execute("set local role ocp_backend")


async def test_backend_role_attributes(conn: asyncpg.Connection) -> None:
    role = await conn.fetchrow(
        "select rolsuper, rolcreaterole, rolcreatedb, rolbypassrls"
        " from pg_roles where rolname = 'ocp_backend'"
    )
    assert role is not None
    assert not role["rolsuper"]
    assert not role["rolcreaterole"]
    assert not role["rolcreatedb"]
    # RLS has no policies (DB-012); the grants below are what limit the backend.
    assert role["rolbypassrls"]


@pytest.mark.parametrize("table", WRITABLE)
async def test_backend_can_select_insert_update_but_not_delete(
    conn: asyncpg.Connection, table: str
) -> None:
    privileges = await conn.fetchrow(
        "select has_table_privilege('ocp_backend', $1, 'SELECT') as s,"
        " has_table_privilege('ocp_backend', $1, 'INSERT') as i,"
        " has_table_privilege('ocp_backend', $1, 'UPDATE') as u,"
        " has_table_privilege('ocp_backend', $1, 'DELETE') as d,"
        " has_table_privilege('ocp_backend', $1, 'TRUNCATE') as t",
        f"ocp.{table}",
    )
    assert privileges is not None
    assert (privileges["s"], privileges["i"], privileges["u"]) == (True, True, True)
    assert (privileges["d"], privileges["t"]) == (False, False)
    await as_backend(conn)
    err = await refused(conn, f"delete from ocp.{table}")
    assert isinstance(err, asyncpg.InsufficientPrivilegeError)


async def test_backend_writes_the_incident_aggregate(conn: asyncpg.Connection) -> None:
    await as_backend(conn)
    rows = Rows(conn)
    incident = await rows.incident()
    evidence = await rows.evidence(incident)
    await rows.hypothesis(incident, [evidence])
    proposal = await rows.proposal(incident, status="approved")
    approval = await rows.approval(proposal)
    await rows.execution(proposal, approval)
    await conn.execute("update ocp.incidents set status = 'executing' where id = $1", incident)
    assert await conn.fetchval("select count(*) from ocp.incidents") == 1


async def test_backend_audit_log_is_insert_and_select_only(conn: asyncpg.Connection) -> None:
    await as_backend(conn)
    entry = await Rows(conn).audit()
    assert await conn.fetchval("select actor from ocp.audit_log where id = $1", entry) == "system"
    for sql in (
        "update ocp.audit_log set actor = 'x'",
        "delete from ocp.audit_log",
        "truncate ocp.audit_log",
    ):
        err = await refused(conn, sql)
        assert isinstance(err, asyncpg.InsufficientPrivilegeError)
        # The grant refuses before the trigger runs.
        assert "permission denied" in str(err)


@pytest.mark.parametrize(
    "sql",
    [
        "create table ocp.extra (id int)",
        "alter table ocp.incidents add column extra text",
        "drop table ocp.monitor_settings",
        "create index extra on ocp.incidents(title)",
        "alter table ocp.incidents disable row level security",
        "alter table ocp.audit_log disable trigger audit_log_append_only",
    ],
)
async def test_backend_has_no_ddl(conn: asyncpg.Connection, sql: str) -> None:
    await as_backend(conn)
    err = await refused(conn, sql)
    assert isinstance(err, asyncpg.InsufficientPrivilegeError)


async def test_rls_is_enabled_on_every_table_without_policies(conn: asyncpg.Connection) -> None:
    rls = await conn.fetch(
        "select c.relname, c.relrowsecurity from pg_class c"
        " join pg_namespace n on n.oid = c.relnamespace"
        " where n.nspname = 'ocp' and c.relkind = 'r'"
    )
    assert {r["relname"] for r in rls} == TABLES
    assert all(r["relrowsecurity"] for r in rls)
    assert await conn.fetchval("select count(*) from pg_policies where schemaname = 'ocp'") == 0


async def test_a_role_without_bypassrls_sees_no_rows(conn: asyncpg.Connection) -> None:
    # Stands in for Supabase's anon and authenticated roles, which do not exist
    # in the test container: even a SELECT grant reads nothing (DB-012).
    await Rows(conn).incident()
    await conn.execute("create role ocp_rls_probe nologin")
    await conn.execute("grant usage on schema ocp to ocp_rls_probe")
    await conn.execute("grant select on ocp.incidents to ocp_rls_probe")
    await conn.execute("set local role ocp_rls_probe")
    assert await conn.fetchval("select count(*) from ocp.incidents") == 0


async def test_public_has_no_table_privileges(conn: asyncpg.Connection) -> None:
    granted = await conn.fetchval(
        "select count(*) from information_schema.role_table_grants"
        " where table_schema = 'ocp' and grantee = 'PUBLIC'"
    )
    assert granted == 0
