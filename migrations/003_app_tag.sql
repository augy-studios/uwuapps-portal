-- 003_app_tag.sql
--
-- Supabase. Run once in the Supabase SQL editor, before deploying the portal
-- and bot changes that offer the "app" tag. Safe to run twice: the constraint
-- is dropped if present and recreated.
--
-- Adds "app" to the tags uwusuite_apps accepts, for sites that also ship as
-- native apps (Android, Windows, macOS, Wear OS, Linux and so on). Without it,
-- saving an app with that tag fails on uwusuite_apps_tags_chk even though the
-- portal and the bot allow it.
--
-- The list here is the third copy of the vocabulary. Keep it in step with
-- ALLOWED_TAGS in main-site/lib/uwu-apps.js and telegram-bot/bot/handlers/manage.py.

begin;

alter table public.uwusuite_apps
  drop constraint if exists uwusuite_apps_tags_chk;

alter table public.uwusuite_apps
  add constraint uwusuite_apps_tags_chk check (
    (
      tags <@ array[
        'tools'::text,
        'games'::text,
        'bots'::text,
        'app'::text,
        'singapore'::text
      ]
    )
  );

commit;
