-- 002_telegram_mfa.sql
--
-- THIS IS THE SUPABASE ONE. Postgres, run once in the Supabase SQL editor,
-- before deploying api/telegram.js. Safe to run twice: every statement is
-- guarded. The base schema, uwusuite_users, uwusuite_apps, uwusuite_sessions
-- and the rest, predates this file and is not repeated here.
--
-- Not to be confused with telegram-bot/bot/migrations/001_init.sql, which is
-- SQLite on the VPS and is applied automatically at startup.
--
-- Covers account linking and two factor authentication. See telegram-bot/SETUP.md
-- step 10 for where this sits in the order of operations.
--
-- Conventions followed from the existing schema:
--   tables       public.uwusuite_<thing>
--   primary key  uwusuite_<table>_pkey
--   unique       uwusuite_<table>_<column>_key
--   foreign key  uwusuite_<table>_<thing>_fk
--   check        uwusuite_<table>_<thing>_chk
--   index        idx_uwusuite_<table>_<columns>, btree unless stated
--   timestamps   timestamp with time zone, defaulting to now()
--   enum columns are constrained by a check, not left as free text


-- One Telegram account maps to exactly one portal account, and the reverse.
-- Both unique constraints are load bearing: they are what the redeem action
-- relies on rather than checking and hoping.
create table if not exists public.uwusuite_telegram_links (
  id                uuid not null default gen_random_uuid (),
  user_id           uuid not null,
  telegram_id       bigint not null,
  telegram_username text null,
  linked_at         timestamp with time zone not null default now(),
  mfa_enabled       boolean not null default false,
  mfa_enabled_at    timestamp with time zone null,
  constraint uwusuite_telegram_links_pkey primary key (id),
  constraint uwusuite_telegram_links_telegram_id_key unique (telegram_id),
  constraint uwusuite_telegram_links_user_id_key unique (user_id),
  constraint uwusuite_telegram_links_user_fk foreign KEY (user_id) references uwusuite_users (id) on delete CASCADE
) TABLESPACE pg_default;

create index IF not exists idx_uwusuite_telegram_links_user_id on public.uwusuite_telegram_links using btree (user_id) TABLESPACE pg_default;


-- Minted by the portal for a signed in user, redeemed once by the bot. The bot
-- can never create a row here, which is the whole point of the linking flow.
create table if not exists public.uwusuite_telegram_link_codes (
  code                    text not null,
  user_id                 uuid not null,
  created_at              timestamp with time zone not null default now(),
  expires_at              timestamp with time zone not null,
  consumed_at             timestamp with time zone null,
  consumed_by_telegram_id bigint null,
  constraint uwusuite_telegram_link_codes_pkey primary key (code),
  constraint uwusuite_telegram_link_codes_user_fk foreign KEY (user_id) references uwusuite_users (id) on delete CASCADE
) TABLESPACE pg_default;

create index IF not exists idx_uwusuite_telegram_link_codes_user_id on public.uwusuite_telegram_link_codes using btree (user_id, expires_at desc) TABLESPACE pg_default;


-- A held sign in. No session exists until this is approved.
create table if not exists public.uwusuite_mfa_challenges (
  id               uuid not null default gen_random_uuid (),
  user_id          uuid not null,
  telegram_id      bigint not null,
  match_number     smallint not null,
  purpose          text not null default 'login'::text,
  status           text not null default 'pending'::text,
  ip               text null,
  device           text null,
  user_agent       text null,
  created_at       timestamp with time zone not null default now(),
  expires_at       timestamp with time zone not null,
  resolved_at      timestamp with time zone null,
  message_id       bigint null,
  code_message_id  bigint null,
  magic_token_hash text null,
  magic_used_at    timestamp with time zone null,
  poll_count       integer not null default 0,
  last_poll_at     timestamp with time zone null,
  constraint uwusuite_mfa_challenges_pkey primary key (id),
  constraint uwusuite_mfa_challenges_user_fk foreign KEY (user_id) references uwusuite_users (id) on delete CASCADE,
  constraint uwusuite_mfa_challenges_purpose_chk check (
    (
      purpose = any (array['login'::text, 'enroll'::text])
    )
  ),
  constraint uwusuite_mfa_challenges_status_chk check (
    (
      status = any (
        array[
          'pending'::text,
          'approved'::text,
          'denied'::text,
          'expired'::text
        ]
      )
    )
  )
) TABLESPACE pg_default;

comment on column public.uwusuite_mfa_challenges.message_id is 'The approval prompt, kept so it can be edited down once the challenge resolves.';
comment on column public.uwusuite_mfa_challenges.code_message_id is 'The code pushed at login, cleared the same way. The portal owns both, so the portal cleans them up.';
comment on column public.uwusuite_mfa_challenges.magic_token_hash is 'HMAC of the Sign in button token. The token itself is never stored and never logged.';

create index IF not exists idx_uwusuite_mfa_challenges_user_id on public.uwusuite_mfa_challenges using btree (user_id, status, created_at desc) TABLESPACE pg_default;

create index IF not exists idx_uwusuite_mfa_challenges_magic_token on public.uwusuite_mfa_challenges using btree (magic_token_hash) TABLESPACE pg_default;


-- Ten codes at enrolment, shown once, each usable once. The only way back in
-- when the linked chat is unreachable.
create table if not exists public.uwusuite_mfa_recovery_codes (
  id         uuid not null default gen_random_uuid (),
  user_id    uuid not null,
  code_hash  text not null,
  created_at timestamp with time zone not null default now(),
  used_at    timestamp with time zone null,
  constraint uwusuite_mfa_recovery_codes_pkey primary key (id),
  constraint uwusuite_mfa_recovery_codes_user_fk foreign KEY (user_id) references uwusuite_users (id) on delete CASCADE
) TABLESPACE pg_default;

comment on column public.uwusuite_mfa_recovery_codes.code_hash is 'bcrypt, through the existing hashPassword helper in api/_supabase.js.';

create index IF not exists idx_uwusuite_mfa_recovery_codes_user_id on public.uwusuite_mfa_recovery_codes using btree (user_id, used_at) TABLESPACE pg_default;


-- Six digits is a small keyspace, so the hash is not the defence. The defence
-- is the five minute expiry, the five attempt cap, and one live code per
-- account per purpose. Hashed with an HMAC keyed by a server side pepper
-- rather than bcrypt, because verification is on a hot path and bcrypt buys
-- nothing against a keyspace this size.
--
-- The purpose is part of what is verified, not a label on the row, so a code
-- issued for an unlink can never satisfy a sign in and the reverse.
create table if not exists public.uwusuite_mfa_otps (
  id         uuid not null default gen_random_uuid (),
  user_id    uuid not null,
  code_hash  text not null,
  attempts   smallint not null default 0,
  purpose    text not null default 'login'::text,
  source     text not null default 'command'::text,
  created_at timestamp with time zone not null default now(),
  expires_at timestamp with time zone not null,
  used_at    timestamp with time zone null,
  constraint uwusuite_mfa_otps_pkey primary key (id),
  constraint uwusuite_mfa_otps_user_fk foreign KEY (user_id) references uwusuite_users (id) on delete CASCADE,
  constraint uwusuite_mfa_otps_purpose_chk check (
    (
      purpose = any (array['login'::text, 'unlink'::text])
    )
  ),
  constraint uwusuite_mfa_otps_source_chk check (
    (
      source = any (
        array['command'::text, 'login'::text, 'web'::text]
      )
    )
  )
) TABLESPACE pg_default;

comment on column public.uwusuite_mfa_otps.code_hash is 'HMAC of purpose, user id and the digits, keyed by MFA_CODE_PEPPER. Never the digits themselves.';
comment on column public.uwusuite_mfa_otps.source is 'command for /code, login for a code pushed at sign in, web for an unlink confirmation.';

create index IF not exists idx_uwusuite_mfa_otps_lookup on public.uwusuite_mfa_otps using btree (user_id, purpose, used_at, expires_at) TABLESPACE pg_default;


-- Replay protection for bot to portal calls uses uwu_used_request_tokens, with
-- a 'bot:' prefix on the token so nothing else in that table can collide.
--
-- That table predates this file and may already index `token`, through a
-- primary key, a unique constraint or an index under a different name, so this
-- checks for one on the column rather than trusting a name and creating a
-- duplicate that would only slow inserts down.
do $$
begin
  if to_regclass('public.uwu_used_request_tokens') is not null
     and not exists (
       select 1 from pg_indexes
       where schemaname = 'public'
         and tablename = 'uwu_used_request_tokens'
         and indexdef like '%(token)%'
     )
  then
    create index idx_uwu_used_request_tokens_token
      on public.uwu_used_request_tokens using btree (token);
  end if;
end $$;
