-- ============================================================
-- Protect public.profiles.role
-- Run once in the Supabase SQL editor. Safe to re-run.
--
-- Why: the backend now treats public.profiles.role = 'ADMIN' as the
-- admin role (together with the ADMIN_EMAIL allowlist). The role must
-- therefore be impossible to change from the browser, even if a future
-- RLS policy lets users update their own profile row.
--
-- What it does:
--   * a BEFORE INSERT/UPDATE trigger on public.profiles that rejects
--     any change to `role`, and any new row whose role is not 'USER',
--     unless the statement runs as a trusted server role
--     (postgres / service_role / supabase_admin: the SQL editor, the
--     backend's secret key, and Supabase internals).
--
-- What it does NOT do:
--   * no table, column, constraint, policy or data changes;
--   * RLS stays exactly as it is;
--   * nobody is promoted or demoted.
-- ============================================================

create or replace function public.protect_profile_role()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    if current_user in ('postgres', 'service_role', 'supabase_admin') then
        return new;
    end if;

    if tg_op = 'INSERT' then
        if new.role is distinct from 'USER' then
            raise exception 'profiles.role can only be set by an administrator'
                using errcode = '42501';
        end if;
    elsif new.role is distinct from old.role then
        raise exception 'profiles.role can only be changed by an administrator'
            using errcode = '42501';
    end if;

    return new;
end;
$$;

drop trigger if exists protect_profile_role on public.profiles;

create trigger protect_profile_role
    before insert or update on public.profiles
    for each row execute function public.protect_profile_role();
