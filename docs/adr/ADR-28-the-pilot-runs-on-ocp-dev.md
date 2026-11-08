# ADR-28 — The pilot runs on ocp-dev; ocp-bench holds only the counted runs

- **Status:** Accepted, 2026-11-08 (Tanzeel)
- **Changes:** architecture §12.2 (environments); `docs/phases.md` P4.1 and P4.4 (wording: "cleared" becomes "verified empty"). `CLAUDE.md` §4.7 stays true and gets one more sentence.

## Context
`docs/phases.md` P4.4 says "`ocp-bench` database cleared" at the freeze. That assumes the pilot (P4.1) already wrote to `ocp-bench`. It cannot be cleared:
- `audit_log` refuses UPDATE, DELETE and TRUNCATE (an append-only row trigger and a statement trigger, `supabase/migrations/20261021190000_initial_schema.sql`);
- `ocp_backend` has no DELETE on any table (DB-011).

The only way to empty a used `ocp-bench` is to drop the `ocp` schema with the admin login and migrate again.

There is a second reason. `bench audit-safety` (BENCH-010) checks **every** proposal and execution in the database. Pilot proposals were made by older code, before the pilot fixes. If they sat in `ocp-bench`, they would count toward the unsafe-action number of the counted runs.

`CLAUDE.md` §4.7 already says `ocp-bench` is migrated only during the freeze procedure.

## Decision
1. **The pilot (P4.1) runs on `ocp-dev`**, on Tanzeel's laptop. Only one stack uses `ocp-dev` at that time (Usman's own stack is down).
2. **`ocp-bench` is migrated at the start of the freeze procedure** (by Tanzeel, with the final migrations) and receives no benchmark data before the first counted run.
3. P4.4 "cleared" becomes **"verified empty"**: before the tag, the counts of incidents, proposals, executions, approvals and audit rows in `ocp-bench` are all 0 (the runbook chunks and the settings row are expected).
4. Before the tag, a **no-write readiness check** on `ocp-bench`: `GET /healthz` shows `db: up`; the test user and the operators sign in; the incident list is empty; the phone app, rebuilt with the `ocp-bench` `SUPABASE_URL` and anon key, signs in. None of these creates an incident.
5. The pilot rows leave `results/runs.csv` before the freeze (they belong to older commits, BENCH-012).

## Consequences
- The pilot does not exercise `ocp-bench` itself (its login role, JWKS, pooler). Step 4 covers that, and the freeze procedure starts a few days before the first counted run so a problem can be fixed without pressure.
- At the freeze, the `.env` of Tanzeel's laptop changes `DATABASE_URL`, `SUPABASE_URL` and the `ocp-bench` test-user values, and the `.env.local` changes `SUPABASE_ANON_KEY`. The backend derives its JWKS URL from `SUPABASE_URL`, so these change together. The APK is rebuilt for the same values.
- `ocp-dev` holds incidents from earlier work. `bench run` resolves leftovers as `bench_cleanup` and aborts if one is still open after 30 s; resolve old ones in one go before the pilot.
- No change to agent behaviour, so the freeze rules (`CLAUDE.md` §6.14) are not touched.
