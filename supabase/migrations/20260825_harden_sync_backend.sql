-- Production-safe migration: no table drops, no credential exposure.
begin;

alter table public.users
    add column if not exists sync_claimed_at timestamptz,
    add column if not exists sync_claim_token uuid,
    add column if not exists last_sync_attempt_at timestamptz,
    add column if not exists last_sync_success_at timestamptz,
    add column if not exists last_sync_error text,
    add column if not exists consecutive_sync_failures integer not null default 0;

alter table public.scrobbles
    alter column track_uid type varchar(512),
    add column if not exists observed_at timestamptz not null default now(),
    add column if not exists timestamp_confidence text not null default 'estimated'
        check (timestamp_confidence in ('exact', 'estimated'));

alter table public.users enable row level security;
alter table public.scrobbles enable row level security;

drop policy if exists "Allow all for users" on public.users;
drop policy if exists "Allow all for scrobbles" on public.scrobbles;
drop policy if exists "Service role full access to users" on public.users;
drop policy if exists "Service role full access to scrobbles" on public.scrobbles;

revoke all on public.users from anon, authenticated;
revoke all on public.scrobbles from anon, authenticated;
create index if not exists idx_users_sync_candidates
    on public.users (last_sync_success_at asc nulls first)
    where is_active = true and (settings->>'auto_scrobble') = 'true';

create index if not exists idx_users_stale_claims
    on public.users (sync_claimed_at)
    where sync_claim_token is not null;

create or replace function public.claim_scrobble_users(
    claim_limit integer default 20,
    minimum_interval_seconds integer default 300
)
returns setof public.users
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
    return query
    with candidates as (
        select u.id
        from public.users u
        where u.is_active = true
          and (u.settings->>'auto_scrobble') = 'true'
          and (
              u.last_sync_success_at is null
              or u.last_sync_success_at < now() - make_interval(secs => greatest(minimum_interval_seconds, 60))
          )
          and (
              u.last_sync_attempt_at is null
              or u.last_sync_attempt_at < now() - make_interval(
                  secs => least(
                      3600,
                      greatest(minimum_interval_seconds, 60) * (1 + least(u.consecutive_sync_failures, 11))
                  )
              )
          )
          and (
              u.sync_claim_token is null
              or u.sync_claimed_at < now() - interval '2 minutes'
          )
        order by u.last_sync_success_at asc nulls first
        for update skip locked
        limit least(greatest(claim_limit, 1), 100)
    )
    update public.users u
       set sync_claim_token = gen_random_uuid(),
           sync_claimed_at = now(),
           last_sync_attempt_at = now()
      from candidates c
     where u.id = c.id
    returning u.*;
end;
$$;

revoke all on function public.claim_scrobble_users(integer, integer) from public, anon, authenticated;
grant execute on function public.claim_scrobble_users(integer, integer) to service_role;

create or replace view public.active_users_stats
with (security_invoker = true)
as
select
    count(*) filter (where is_active = true) as total_active,
    count(*) filter (where is_active = true and (settings->>'auto_scrobble') = 'true') as auto_scrobble_enabled,
    count(*) filter (where lastfm_username is not null) as lastfm_connected,
    count(*) filter (where ytmusic_headers is not null and ytmusic_headers != '') as ytmusic_connected,
    count(*) filter (where last_sync_success_at > now() - interval '1 hour') as synced_last_hour,
    count(*) filter (where created_at > now() - interval '24 hours') as new_today
from public.users;

revoke all on public.active_users_stats from anon, authenticated;

-- Keep the existing timestamp trigger function from resolving attacker-controlled
-- objects through a mutable search path.
alter function public.update_updated_at_column()
    set search_path = public, pg_temp;

commit;
