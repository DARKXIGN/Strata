-- Strata diagnosis history. Paste into the Supabase SQL editor and run once.
-- Safe to re-run: every statement is idempotent.
--
-- Access model (new API keys):
--   * The server writes with SUPABASE_SECRET_KEY (sb_secret_...), which maps to the
--     service_role role. service_role bypasses RLS but still needs table grants (below).
--   * anon (what a publishable key maps to) may only SELECT; RLS is on and there are no
--     insert/update/delete policies, and those privileges are revoked as well.
--   * authenticated gets nothing: no grant, no policy.
-- The dashboard never talks to Supabase; it goes through the Strata server API.

-- 1. Tables --------------------------------------------------------------------------

create table if not exists public.agents (
    id          text primary key,                       -- e.g. 'strata-a'
    name        text not null,
    region      text not null,
    created_at  timestamptz not null default now()
);

create table if not exists public.diagnoses (
    id             uuid primary key default gen_random_uuid(),  -- the server sends its own UUIDs
    agent_id       text not null references public.agents (id) on delete cascade,
    ts             timestamptz not null,
    label          text not null,
    probs          jsonb not null default '{}'::jsonb,           -- class -> probability
    anomaly_score  double precision,                             -- largest robust z of any feature
    features       jsonb not null default '{}'::jsonb            -- measured feature row
);

comment on table public.diagnoses is 'One row per recorded Strata diagnosis (default: every 10 s per agent, plus label changes).';

-- 2. Indexes -------------------------------------------------------------------------

create index if not exists diagnoses_agent_ts_idx on public.diagnoses (agent_id, ts desc);
create index if not exists diagnoses_ts_idx       on public.diagnoses (ts desc);

-- 3. Row Level Security ----------------------------------------------------------------

alter table public.agents    enable row level security;
alter table public.diagnoses enable row level security;

drop policy if exists "anon can read agents" on public.agents;
create policy "anon can read agents"
    on public.agents for select to anon using (true);

drop policy if exists "anon can read diagnoses" on public.diagnoses;
create policy "anon can read diagnoses"
    on public.diagnoses for select to anon using (true);

-- 4. Grants (checked before RLS) -----------------------------------------------------------

grant usage on schema public to anon, service_role;

revoke all on public.agents, public.diagnoses from anon, authenticated;
grant select on public.agents, public.diagnoses to anon;

grant select, insert, update, delete on public.agents, public.diagnoses to service_role;

-- 5. Optional: retention (free plan has 500 MB). Three agents at the default rate store
--    roughly 40 MB/day. To keep 7 days, run this now and then, or schedule it with pg_cron:
--
--    delete from public.diagnoses where ts < now() - interval '7 days';
