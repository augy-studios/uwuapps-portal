-- 001_init.sql, the whole local datastore.
--
-- THIS IS THE SQLITE ONE, and it is local to the VPS. Do not paste it into
-- Supabase. db.py applies it automatically at startup, so it needs no manual
-- step, and the Supabase schema lives in main-site/sql/002_telegram_mfa.sql.
--
-- Table names are deliberately bare. This is a separate database file with
-- nothing else in it, so there is nothing to namespace against, and the
-- uwusuite_ prefix is reserved for tables that actually live in Supabase.
--
-- Nothing in here mirrors a Supabase table except `links`, which caches which
-- portal account a Telegram id belongs to so /whoami and /code can answer
-- while the portal is unreachable. The portal stays authoritative.
--
-- Timestamps are ISO 8601 UTC text throughout.

create table schema_version (version integer not null);

create table users (
  telegram_id   integer primary key,
  username      text,
  first_name    text,
  language_code text,
  is_blocked    integer not null default 0,
  first_seen_at text not null,
  last_seen_at  text not null
);

create table links (
  telegram_id     integer primary key references users(telegram_id) on delete cascade,
  portal_user_id  text not null unique,
  portal_username text,
  display_name    text,
  is_admin        integer not null default 0,
  linked_at       text not null,
  last_synced_at  text
);

create table link_attempts (
  id           integer primary key autoincrement,
  telegram_id  integer not null,
  succeeded    integer not null,
  attempted_at text not null
);
create index idx_link_attempts on link_attempts(telegram_id, attempted_at);

create table callbacks (
  id         text primary key,           -- 16 hex chars, the callback_data payload
  action     text not null,              -- dispatch key, for example 'apps.page'
  payload    text not null default '{}', -- JSON arguments
  owner_id   integer,                    -- NULL means anyone may press
  chat_id    integer,
  message_id integer,
  created_at text not null,
  expires_at text,                       -- NULL means never expires
  use_count  integer not null default 0,
  max_uses   integer                     -- NULL means unlimited
);
create index idx_callbacks_message on callbacks(chat_id, message_id);
create index idx_callbacks_expiry  on callbacks(expires_at);

create table scheduled_jobs (
  id            integer primary key autoincrement,
  job_type      text not null,
  payload       text not null default '{}',
  run_at        text not null,
  interval_secs integer,                          -- NULL means one shot
  status        text not null default 'pending',  -- pending running done failed cancelled
  attempts      integer not null default 0,
  max_attempts  integer not null default 5,
  last_error    text,
  locked_by     text,
  locked_at     text,
  created_at    text not null,
  updated_at    text not null
);
create index idx_jobs_due on scheduled_jobs(status, run_at);

create table subscriptions (
  telegram_id integer not null references users(telegram_id) on delete cascade,
  topic       text not null,             -- for example 'new_apps'
  created_at  text not null,
  primary key (telegram_id, topic)
);

create table api_cache (
  cache_key  text primary key,
  body       text not null,
  fetched_at text not null,
  expires_at text not null
);

create table seen_apps (
  app_id        text primary key,
  title         text,
  first_seen_at text not null
);

create table mfa_events (
  id           integer primary key autoincrement,
  telegram_id  integer not null,
  event        text not null,            -- code_issued approved denied expired
  challenge_id text,                     -- NULL for code_issued
  created_at   text not null
);
create index idx_mfa_events on mfa_events(telegram_id, created_at);

create table command_log (
  id          integer primary key autoincrement,
  telegram_id integer,
  command     text not null,
  ok          integer not null,
  duration_ms integer,
  created_at  text not null
);
create index idx_command_log_time on command_log(created_at);

create table feedback (
  id          integer primary key autoincrement,
  telegram_id integer not null,
  body        text not null,
  created_at  text not null,
  handled_at  text
);
