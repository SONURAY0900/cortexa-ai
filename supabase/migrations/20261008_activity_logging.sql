-- ============================================================
-- Activity logging + admin audit trail
-- Run once in the Supabase SQL editor (Dashboard → SQL Editor).
-- Safe to re-run: every statement is idempotent.
--
-- Records ONLY events that no existing table already stores:
--   activity_logs   voice transcriptions, failed requests/uploads
--                   (chat queries live in public.messages, PDF uploads
--                   in public.documents, images in public.files/images)
--   admin_audit_log every destructive admin action
--
-- Never stores passwords, tokens, API keys or file contents.
-- Both tables are written/read only by the backend (service key).
-- RLS is enabled with NO policies, so the public/anon and
-- authenticated roles cannot read or write them.
-- ============================================================

create table if not exists public.activity_logs (
    id            uuid primary key default gen_random_uuid(),
    user_id       uuid references auth.users (id) on delete set null,
    activity_type text not null,          -- VOICE, CHAT_ERROR, PDF_UPLOAD_FAILED, IMAGE_UPLOAD_FAILED, ...
    action        text,                   -- short description / query text (truncated by the backend)
    status        text not null default 'SUCCESS' check (status in ('SUCCESS', 'FAILED')),
    route         text,
    document_id   uuid,
    file_name     text,
    metadata      jsonb not null default '{}'::jsonb,
    created_at    timestamptz not null default now()
);

create index if not exists activity_logs_created_at_idx on public.activity_logs (created_at desc);
create index if not exists activity_logs_user_id_idx    on public.activity_logs (user_id);
create index if not exists activity_logs_type_idx       on public.activity_logs (activity_type);

create table if not exists public.admin_audit_log (
    id          uuid primary key default gen_random_uuid(),
    admin_id    uuid,
    admin_email text,
    action      text not null,            -- DELETE_USER, DELETE_DOCUMENT, DELETE_IMAGE, DELETE_ACTIVITY
    target_type text not null,
    target_id   text,
    status      text not null check (status in ('SUCCESS', 'FAILED')),
    details     jsonb not null default '{}'::jsonb,
    created_at  timestamptz not null default now()
);

create index if not exists admin_audit_log_created_at_idx on public.admin_audit_log (created_at desc);

alter table public.activity_logs   enable row level security;
alter table public.admin_audit_log enable row level security;

revoke all on public.activity_logs   from anon, authenticated;
revoke all on public.admin_audit_log from anon, authenticated;

grant select, insert, delete on public.activity_logs   to service_role;
grant select, insert         on public.admin_audit_log to service_role;
