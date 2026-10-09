-- ============================================================
-- Let the backend read and update public.profiles
-- Run once in the Supabase SQL editor. Safe to re-run.
--
-- Why: the backend uses the server-side (service_role) key, and that
-- role currently has no privileges on public.profiles
-- ("permission denied for table profiles"), so the Profile page could
-- not show or save the display name.
--
-- What it does NOT do:
--   * no new tables, columns, constraints or indexes;
--   * nothing is granted to anon or authenticated (browser keys);
--   * DELETE is not granted; the role column is never written by the
--     application (users cannot change their own role).
-- ============================================================

grant select, insert, update on table public.profiles to service_role;
