-- 002_app_management.sql, managing the directory from a chat.
--
-- SQLITE, local to the VPS, like 001. Applied automatically by db.py at
-- startup. The Supabase side of this feature is the app_ actions in
-- main-site/api/telegram.js, which needs no schema change at all.
--
-- Two things arrive here.
--
-- 1. `links` learns the other two role columns. The mirror already carried
--    is_admin so /whoami could answer during an outage, and the same reasoning
--    applies to the management commands: whether to offer them is decided
--    locally, while whether a write is allowed is always decided by the portal.
--
-- 2. `app_drafts`, one unfinished app per person. A draft outlives a restart on
--    purpose. Somebody halfway through describing an app should not lose it to
--    a deploy, and one row per Telegram id keeps resuming unambiguous.

alter table links add column is_editor integer not null default 0;
alter table links add column is_approved integer not null default 0;

create table app_drafts (
  telegram_id integer primary key references users(telegram_id) on delete cascade,
  app_id      text,                        -- NULL while the app is new
  fields      text not null default '{}',  -- JSON, the values collected so far
  touched     text not null default '[]',  -- JSON, which of them were edited
  awaiting    text,                        -- the field a typed reply belongs to
  chat_id     integer,                     -- the form message, edited in place
  message_id  integer,
  created_at  text not null,
  updated_at  text not null
);
