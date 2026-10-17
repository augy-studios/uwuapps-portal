# migrations

SQL for the Supabase database behind the portal. Run each file by hand in the
Supabase SQL editor, in number order, once.

| File | What it does |
| --- | --- |
| [002_telegram_mfa.sql](002_telegram_mfa.sql) | Telegram account linking and the second factor tables |
| [003_app_tag.sql](003_app_tag.sql) | Allows the `app` tag on `uwusuite_apps` |

The base schema (`uwusuite_users`, `uwusuite_apps`, `uwusuite_sessions` and the
rest) predates this folder and has no file here.

Not to be confused with [telegram-bot/bot/migrations/](../telegram-bot/bot/migrations/),
which is SQLite on the VPS and is applied automatically when the bot starts.

## Rules

- **Never edit a file once it is written.** It may already have run. A fix or a
  follow up change goes in a new file with the next number.
- Make every file safe to run twice (`if not exists`, `drop ... if exists`).
- Name things the way [002_telegram_mfa.sql](002_telegram_mfa.sql) lists in its
  header: `uwusuite_<table>_<thing>_chk` and so on.
- The allowed tags exist in three places: the `uwusuite_apps_tags_chk`
  constraint, `ALLOWED_TAGS` in [main-site/lib/uwu-apps.js](../main-site/lib/uwu-apps.js),
  and `ALLOWED_TAGS` in [telegram-bot/bot/handlers/manage.py](../telegram-bot/bot/handlers/manage.py).
  Changing the tags means a new migration plus both code lists.
