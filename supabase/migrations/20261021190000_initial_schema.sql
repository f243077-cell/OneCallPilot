-- B1.1 initial schema (DB-001…DB-012; architecture §8.2, §8.3).
-- Forward-only (DB-013): never edit this file once it is on main; fix forward
-- with a new migration.

create extension if not exists vector;
create extension if not exists pgcrypto;
create schema if not exists ocp;
set search_path = ocp, public;

-- --- Enums (§8.2; the same value sets as contracts/python/.../enums.py) -------

create type incident_status as enum ('investigating','awaiting_approval','escalated','executing','verifying','resolved','action_failed');
create type severity        as enum ('sev1','sev2','sev3');
create type resolution      as enum ('action_succeeded','auto_recovered','manual','rolled_back');
create type evidence_kind   as enum ('detector_signal','log_query','metric_query','deploy_list','service_health','runbook_hit');
create type evidence_purpose as enum ('seed','gather','disproof');
create type risk_tier       as enum ('low','medium','high');
create type proposal_kind   as enum ('primary','rollback');
create type proposal_status as enum ('pending','approved','rejected','expired','superseded','executing','succeeded','failed');
create type decision        as enum ('approved','rejected');
create type execution_status as enum ('queued','running','succeeded','failed','aborted');

-- --- Tables (§8.2) ----------------------------------------------------------

create table incidents (
  id                      uuid primary key default gen_random_uuid(),
  service                 text not null,
  severity                severity not null,
  status                  incident_status not null default 'investigating',
  status_reason           text,
  title                   text not null,
  trigger                 jsonb not null,                -- signal snapshot + updates[]
  opened_at               timestamptz not null default now(),
  resolved_at             timestamptz,
  resolution              resolution,
  window_start            timestamptz not null,
  window_end              timestamptz,                   -- set when resolved
  state_version           integer not null default 1,    -- +1 on every aggregate change
  detector_config_version text not null,
  agent_meta              jsonb not null default '{}'::jsonb,
  benchmark_run_id        text,
  created_at              timestamptz not null default now(),
  updated_at              timestamptz not null default now(),
  check ((status = 'resolved') = (resolved_at is not null and resolution is not null))
);
create unique index incidents_one_active_per_service on incidents(service) where status <> 'resolved';
create index incidents_opened_at on incidents(opened_at desc);

create table evidence (
  id                 uuid primary key default gen_random_uuid(),
  incident_id        uuid not null references incidents(id) on delete restrict,
  ref                text not null check (ref ~ '^E[0-9]{1,3}$'),
  kind               evidence_kind not null,
  purpose            evidence_purpose not null,
  tool_name          text,                               -- null for seed
  params             jsonb not null default '{}'::jsonb,
  summary            text not null,
  payload            jsonb not null,                     -- REDACTED content only
  suspicious_content boolean not null default false,
  created_at         timestamptz not null default now(),
  unique (incident_id, ref)
);

create table hypotheses (
  id                  uuid primary key default gen_random_uuid(),
  incident_id         uuid not null references incidents(id) on delete restrict,
  rank                smallint not null check (rank between 1 and 3),
  summary             text not null,
  root_cause_category text not null,
  confidence          numeric(3,2) not null check (confidence between 0 and 1),
  confidence_initial  numeric(3,2) not null check (confidence_initial between 0 and 1),
  evidence_ids        uuid[] not null check (cardinality(evidence_ids) >= 1),
  status              text not null default 'active' check (status in ('active','dropped')),
  reflection_notes    text,
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now()
);
create unique index hypotheses_active_rank on hypotheses(incident_id, rank) where status = 'active';

create table devices (
  id           uuid primary key default gen_random_uuid(),
  user_id      uuid not null,
  fcm_token    text not null unique,
  platform     text not null check (platform = 'android'),
  app_version  text,
  push_enabled boolean not null default true,
  last_seen_at timestamptz,
  created_at   timestamptz not null default now()
);

create table proposals (
  id                   uuid primary key default gen_random_uuid(),
  incident_id          uuid not null references incidents(id) on delete restrict,
  hypothesis_id        uuid references hypotheses(id),          -- null for rollback
  kind                 proposal_kind not null default 'primary',
  parent_proposal_id   uuid references proposals(id),
  action               text not null,
  params               jsonb not null,
  risk_tier            risk_tier not null,
  approval_requirement text not null check (approval_requirement in ('tap','biometric')),
  expected_effect      text not null,
  rationale            text not null,
  evidence_ids         uuid[] not null default '{}',
  rollback_step        jsonb,                                    -- null = not reversible
  dry_run_result       jsonb not null,
  dry_run_at           timestamptz not null,
  fingerprint          text not null,
  status               proposal_status not null default 'pending',
  status_reason        text,
  expires_at           timestamptz not null,
  superseded_by        uuid references proposals(id),
  catalogue_version    text not null,
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now(),
  check ((kind = 'rollback') = (parent_proposal_id is not null))
);
create unique index proposals_one_pending_per_incident on proposals(incident_id) where status = 'pending';

create table approvals (
  id              uuid primary key default gen_random_uuid(),
  proposal_id     uuid not null unique references proposals(id),
  user_id         uuid not null,
  decision        decision not null,
  reason          text,
  auth_method     text not null check (auth_method in ('biometric','tap')),
  challenge_id    uuid,
  device_id       uuid references devices(id),
  idempotency_key text not null unique,
  decided_at      timestamptz not null default now(),
  check (decision = 'approved' or (reason is not null and length(reason) >= 3)),
  check (decision = 'rejected' or challenge_id is not null)
);

create table executions (
  id           uuid primary key default gen_random_uuid(),
  proposal_id  uuid not null unique references proposals(id),
  approval_id  uuid not null unique references approvals(id),
  status       execution_status not null default 'queued',
  output       jsonb not null default '{}'::jsonb,   -- steps[], runner messages
  error_code   text,
  health_after jsonb,                                -- {structural:{...}, signals:{...}, verdict}
  started_at   timestamptz,
  finished_at  timestamptz,
  created_at   timestamptz not null default now()
);

create table audit_log (
  id          bigint generated always as identity primary key,
  actor       text not null,
  event       text not null,
  ref_type    text,
  ref_id      uuid,
  incident_id uuid references incidents(id),
  payload     jsonb not null default '{}'::jsonb,
  created_at  timestamptz not null default now()
);
create index audit_log_incident on audit_log(incident_id, id);

create table runbook_chunks (
  id              uuid primary key default gen_random_uuid(),
  source          text not null,             -- e.g. runbooks/rollback.md
  chunk_index     integer not null,
  heading         text,
  content         text not null,
  content_hash    text not null,
  embedding       vector(768) not null,      -- ADR-14
  embedding_model text not null,
  created_at      timestamptz not null default now(),
  unique (source, chunk_index)
);
create index runbook_chunks_embedding on runbook_chunks using hnsw (embedding vector_cosine_ops);

create table monitor_settings (
  id         smallint primary key default 1 check (id = 1),
  version    integer not null,
  settings   jsonb not null,     -- {services[], thresholds{}, notify{min_push_severity}}
  updated_by uuid,
  updated_at timestamptz not null default now()
);

-- --- Triggers ---------------------------------------------------------------

-- DB-007: an execution needs an approval with decision 'approved' for the same
-- proposal. Fails closed: a missing approval raises too.
create function executions_require_approved() returns trigger
language plpgsql
set search_path = ocp, pg_temp
as $$
begin
  if not exists (
    select 1 from ocp.approvals a
    where a.id = new.approval_id
      and a.decision = 'approved'
      and a.proposal_id = new.proposal_id
  ) then
    raise exception 'execution requires an approved approval for proposal %', new.proposal_id
      using errcode = 'check_violation';
  end if;
  return new;
end;
$$;

create trigger executions_require_approved
  before insert on executions
  for each row execute function executions_require_approved();

-- DB-008: audit_log is append-only, even for the table owner.
create function audit_log_append_only() returns trigger
language plpgsql
set search_path = ocp, pg_temp
as $$
begin
  raise exception 'audit_log is append-only (% refused)', tg_op
    using errcode = 'insufficient_privilege';
end;
$$;

create trigger audit_log_append_only
  before update or delete on audit_log
  for each row execute function audit_log_append_only();

-- TRUNCATE skips row triggers, so it gets its own statement trigger.
create trigger audit_log_no_truncate
  before truncate on audit_log
  for each statement execute function audit_log_append_only();

-- --- Access model (§8.3, DB-011, DB-012) -------------------------------------

-- RLS on every table with no policies: any role that does not bypass RLS
-- (Supabase's anon and authenticated) sees no rows, as defence in depth.
alter table incidents        enable row level security;
alter table evidence         enable row level security;
alter table hypotheses       enable row level security;
alter table devices          enable row level security;
alter table proposals        enable row level security;
alter table approvals        enable row level security;
alter table executions       enable row level security;
alter table audit_log        enable row level security;
alter table runbook_chunks   enable row level security;
alter table monitor_settings enable row level security;

-- The backend's role. NOLOGIN here: the login and its password are set by hand
-- on each Supabase project (never in git). BYPASSRLS because the tables have
-- RLS with no policies; privileges below are what limits it.
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'ocp_backend') then
    create role ocp_backend nologin bypassrls;
  end if;
end;
$$;

grant usage on schema ocp to ocp_backend;
grant select, insert, update on
  incidents, evidence, hypotheses, devices, proposals, approvals, executions,
  runbook_chunks, monitor_settings
  to ocp_backend;
grant select, insert on audit_log to ocp_backend;
grant usage on type
  incident_status, severity, resolution, evidence_kind, evidence_purpose,
  risk_tier, proposal_kind, proposal_status, decision, execution_status
  to ocp_backend;

-- Supabase's API roles get nothing in ocp (the schema is also left out of the
-- Data API's exposed schemas). The roles do not exist in the test container.
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    execute 'revoke all on schema ocp from anon, authenticated';
    execute 'revoke all on all tables in schema ocp from anon, authenticated';
  end if;
end;
$$;
