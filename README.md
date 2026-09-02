# UwU Apps Portal

The monorepo behind **UwU Suite**, the official homepage for all UwU Apps web apps, all under one roof.

UwU Suite is a browsable directory of web apps built by [Augy Studios](https://github.com/augy-studios). Visitors can filter by category, spot recently published apps through the NEW badge, and launch anything straight from the browser. Approved editors and admins manage the catalogue through a signed in dashboard on the same site.

---

## Repository layout

| Directory | What lives there |
| --- | --- |
| [main-site/](main-site/) | The portal itself. A static PWA plus Vercel serverless functions backed by Supabase. |
| [telegram-bot/](telegram-bot/) | A Telethon based Telegram bot that fronts the portal, and the second factor behind portal sign ins. |

### [main-site/](main-site/)

The public site and its API.

- Progressive web app, installable, with an offline fallback and a service worker
- Installable from the Play Store as `org.uwuapps.portal`
- Themeable, with a light and dark mode plus several colour themes
- Serverless API under [main-site/api/](main-site/api/) covering [authentication](main-site/api/auth.js), the [app catalogue](main-site/api/apps.js), [user administration](main-site/api/users.js), [uploads](main-site/api/upload.js), and the [Telegram link and second factor](main-site/api/telegram.js)
- Deployed on Vercel, configured in [main-site/vercel.json](main-site/vercel.json)

Start with [main-site/README.md](main-site/README.md) and [main-site/release-notes.md](main-site/release-notes.md).

### [telegram-bot/](telegram-bot/)

A Telegram bot that lets people browse the directory, link their portal account, and hear about new apps without opening a browser. Editors and admins can also add, edit, publish and delete apps from a chat. A linked account can use Telegram as a second factor when signing in to the portal, either by approving a prompt or by pasting in a one time code. It runs on a Debian VPS inside tmux, stores everything in SQLite, and talks to the portal over the same API the website uses.

Read [telegram-bot/README.md](telegram-bot/README.md) for what it does, and [telegram-bot/SETUP.md](telegram-bot/SETUP.md) for BotFather, the VPS, and the portal side changes that linking depends on.

The portal half lives in [main-site/](main-site/): [api/telegram.js](main-site/api/telegram.js), [api/auth/magic.js](main-site/api/auth/magic.js), the Settings tab in the Admin Panel, and the schema in [sql/002_telegram_mfa.sql](main-site/sql/002_telegram_mfa.sql).

---

## Getting started

The portal is a static site, so most work needs nothing more than a local web server pointed at [main-site/](main-site/).

```bash
git clone https://github.com/augy-studios/uwuapps-portal.git
cd uwuapps-portal/main-site
npm install          # only needed for the serverless functions
npx vercel dev       # serves the site and the /api routes together
```

The API functions expect `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, and `ALLOWED_ORIGINS` in the environment. Environment files are git ignored, so copy the values from the Vercel project rather than committing them.

---

## Contributing

Issues and pull requests are welcome. Please read [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) first, keep changes scoped to one directory where possible, and never commit secrets, keystores, or session files.

## Licence

Released under the MIT Licence, see [LICENSE](LICENSE).
