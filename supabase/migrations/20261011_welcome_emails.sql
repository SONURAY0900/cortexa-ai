-- ============================================================
-- Welcome email delivery record
-- Run once in the Supabase SQL editor (Dashboard → SQL Editor).
-- Safe to re-run: every statement is idempotent.
--
-- One row per account (primary key user_id) makes the welcome email
-- at-most-once: the backend claims the row with an INSERT before sending,
-- so concurrent requests or servers cannot both send.
--
--   status   SENDING  claimed; outcome unknown until updated (never resent)
--            SENT     delivered to the email provider
--            FAILED   provider reported nothing was sent (retried, max 3)
--            SKIPPED  not a new account, or no provider configured
--   reason   short code only (e.g. existing_account, smtp_auth)
--
-- Never stores email addresses, message content or credentials.
-- Written/read only by the backend (service key): RLS is enabled with
-- NO policies, so the anon and authenticated roles have no access.
--
-- Data change: every account that already exists gets a SKIPPED row, so
-- existing users never receive a welcome email. Nothing is deleted.
-- ============================================================

create table if not exists public.welcome_emails (
    user_id     uuid primary key references auth.users (id) on delete cascade,
    status      text not null check (status in ('SENDING', 'SENT', 'FAILED', 'SKIPPED')),
    attempts    integer not null default 0,
    reason      text,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now(),
    sent_at     timestamptz
);

alter table public.welcome_emails enable row level security;

revoke all on public.welcome_emails from anon, authenticated;
grant select, insert, update on public.welcome_emails to service_role;

insert into public.welcome_emails (user_id, status, reason)
select id, 'SKIPPED', 'existing_account'
from auth.users
on conflict (user_id) do nothing;
