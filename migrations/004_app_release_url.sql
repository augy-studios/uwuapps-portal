-- 004_app_release_url.sql
--
-- Supabase. Run once in the Supabase SQL editor, before deploying the portal
-- and bot changes that read and write release_url. Safe to run twice.
--
-- Apps tagged "app" also ship as native apps. release_url is the link to their
-- latest release (a GitHub releases/latest page, a store listing and so on),
-- shown as an "Install App" button next to "Open App".
--
-- The value is put straight into an href, so the check keeps it to http and
-- https and rules out javascript: and data: links at the database as well as
-- in main-site/lib/uwu-apps.js.

begin;

alter table public.uwusuite_apps
  add column if not exists release_url text null;

alter table public.uwusuite_apps
  drop constraint if exists uwusuite_apps_release_url_chk;

alter table public.uwusuite_apps
  add constraint uwusuite_apps_release_url_chk check (
    release_url is null or release_url ~* '^https?://'
  );

comment on column public.uwusuite_apps.release_url is 'Latest native release, for apps tagged app. Cleared by the API when the tag is removed.';

commit;
