# Setup

Follow these in order. Steps 1 to 9 stand the service up. **Step 10 is the one
that is easy to skip and must not be**, because linking and the second factor
cannot work until the portal side ships.

---

## 1. Get `api_id` and `api_hash`

Telethon needs these even in bot mode.

1. Open <https://my.telegram.org> and sign in with your phone number
2. Choose **API development tools**
3. Fill in any app name and short name
4. Copy the **App api_id** and **App api_hash**

These belong to your Telegram account, not to the bot. Treat them as secrets.

## 2. Create the bot

In Telegram, message [@BotFather](https://t.me/BotFather):

```
/newbot
```

It asks for a display name, then a username ending in `bot`. Copy the token it
gives you. That token is the bot, so anyone holding it can act as the bot.

Suggested display name: **UwU Suite**

## 3. Set the about text

`/setabouttext`, or **Edit Bot** then **Edit About**. 120 characters maximum.
Paste this:

```
Browse UwU Suite apps, link your portal account, and approve sign ins with a one time code.
```

## 4. Set the description

`/setdescription`, or **Edit Bot** then **Edit Description**. 512 characters
maximum. Paste this:

```
UwU Suite is a directory of small web apps, games and tools by UwU Apps. Browse everything that is published, hear about new arrivals, and link your portal account. Once linked you can turn on two factor authentication and approve sign ins from this chat, or use the six digit code that arrives with each attempt. Type a name to search the directory. Send /start to see everything.
```

## 5. Upload the pictures

- `/setuserpic`, then send [main-site/UUS-512.png](../main-site/UUS-512.png)
- `/setdescriptionpic`, then send a wider image. [main-site/images/screenshot_1.png](../main-site/images/screenshot_1.png)
  works, or make a banner

## 6. Set the command list

`/setcommands`, or **Edit Bot** then **Edit Commands**. Paste this block exactly.
No line names the bot, which is deliberate.

```
start - See what this is and how to begin
link - Link this Telegram account to your portal account
code - Get a one time code for signing in
apps - Browse the published apps
new - See the most recently published apps
about - Read more about UwU Suite
whoami - See which portal account is linked here
notify - Turn new app announcements on or off
status - Check that everything is running
```

`/unlink` and `/stats` are absent on purpose. Both have handlers. `/unlink` only
ever refuses, and advertising a command whose only answer is a refusal is worse
than saying nothing. `/stats` answers admins alone.

This block is generated from the same registry that renders `/start`, so if you
add a command later, regenerate it rather than editing by hand:

```bash
.venv/bin/python bot.py --botfather
```

## 7. Privacy settings

Still in BotFather, under **Edit Bot**:

| Setting | Value | Why |
| --- | --- | --- |
| `/setprivacy` | **Enable** | Ordinary group messages are then never delivered, which is what makes free text search a private chat feature rather than something that fires on every message in every group |
| `/setjoingroups` | **Disable** | This is a direct message service |
| `/setinline` | **Disable** | Inline mode is not implemented |

## 8. Prepare the Debian 13 box

```bash
sudo apt update
sudo apt install -y python3 python3-venv git tmux systemd-timesyncd
sudo systemctl enable --now systemd-timesyncd
timedatectl status
```

**The clock matters.** Every read from the portal is signed with a timestamp,
and the portal rejects anything more than 30 seconds out. Clock drift shows up
as a mysterious 403 that looks nothing like a clock problem. `timedatectl status`
should say `System clock synchronized: yes`. `chrony` works just as well if you
prefer it.

## 9. Clone, install, configure

```bash
cd ~
git clone https://github.com/augy-studios/uwuapps-portal.git
cd uwuapps-portal/telegram-bot
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
chmod +x run.sh
cp .env.example .env
chmod 600 .env
nano .env
```

Fill in every key marked required in [.env.example](.env.example). Generate the
shared secret with:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

The same value goes into the Vercel environment in the next step. `config.py`
validates on import and exits naming the missing key, so a typo fails at startup
rather than inside a handler hours later.

## 10. The portal side, all of it required before linking works

Nothing about linking or the second factor works until this is deployed. The bot
can redeem a linking code but can never mint one, because minting takes a signed
in portal session.

**a. Run the schema.** Paste [main-site/sql/002_telegram_mfa.sql](../main-site/sql/002_telegram_mfa.sql)
into the Supabase SQL editor and run it. Every statement is guarded, so running
it twice is safe.

**b. Set the Vercel environment variables** on the `uwuapps-portal` project:

| Key | Value |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | The same token from step 2. The portal sends the approval prompt itself, so it needs this |
| `TELEGRAM_BOT_USERNAME` | The username from step 2, without the `@` |
| `TELEGRAM_BOT_SHARED_SECRET` | The same value as in `.env` |
| `MFA_CODE_PEPPER` | A fresh 64 character hex string, `python3 -c "import secrets; print(secrets.token_hex(32))"`. Changing it later invalidates every live code |
| `PORTAL_ORIGIN` | `https://uwuapps.org`, used to build the Sign in button URL |

**c. Deploy.** These files are already in the repository:

| File | What it is |
| --- | --- |
| `main-site/api/telegram.js` | The action endpoint, web actions and bot actions |
| `main-site/api/_mfa.js` | The internals both endpoints share |
| `main-site/api/auth/magic.js` | The Sign in button, a browser navigation rather than a JSON action |
| `main-site/lib/uwu-telegram.js` | Bot signature checking, the code generator, the Bot API calls, and the message copy |
| `main-site/api/auth.js` | The `login` change plus `mfa_status`, `mfa_verify_code` and `mfa_recover` |
| `main-site/index.html`, `script.js`, `style.css` | The Settings tab, the held sign in step, and the unlink confirmation |

**d. Check it.** Sign in to the portal as an admin, open the **Admin Panel**, and
confirm the **Settings** tab is there with a **Link Telegram** button. The panel
is admin only today, because `script.js` hides it unless the account is an
admin. Widening that is a separate decision.

## 11. First run

```bash
tmux new -s uwubot
cd ~/uwuapps-portal/telegram-bot
./run.sh
```

Healthy startup looks like this:

```
2026-09-02 10:14:02 INFO    uwu.main               Starting up, portal at https://uwuapps.org
2026-09-02 10:14:02 INFO    uwu.db                 Applying migration 001_init.sql
2026-09-02 10:14:02 INFO    uwu.db                 Database schema is now at version 1
2026-09-02 10:14:03 INFO    uwu.main               Signed in as the bot account, id 1234567890
2026-09-02 10:14:03 INFO    uwu.scheduler          Seeded recurring job apps.refresh every 900s
2026-09-02 10:14:03 INFO    uwu.handlers           Registered 11 commands and 5 callback actions
2026-09-02 10:14:03 INFO    uwu.scheduler          Scheduler started, ticking every 15s
2026-09-02 10:14:04 INFO    uwu.main               App list ready, 12 published apps
2026-09-02 10:14:04 INFO    uwu.main               Ready. 11 commands registered.
```

The migration lines appear on the first run only. Detach with `Ctrl-b` then `d`.

Now message the bot `/start`, then `/apps`. Both should answer without any
portal side work being finished, because reading the directory needs no
credentials at all.

## 12. Optional, the systemd unit

tmux is the default and does not survive a reboot. For unattended restarts:

```bash
sudo cp systemd/uwu-telegram-bot.service /etc/systemd/system/
sudo nano /etc/systemd/system/uwu-telegram-bot.service   # fix User and the paths
sudo systemctl daemon-reload
sudo systemctl enable --now uwu-telegram-bot
systemctl status uwu-telegram-bot
journalctl -u uwu-telegram-bot -f
```

Run one or the other, never both at once. Two processes on one bot token fight
over updates.

## 13. Troubleshooting

| Symptom | What it usually is |
| --- | --- |
| Every `/link` or `/code` returns 403 | The VPS clock. The bot signature window is 60 seconds. Check `timedatectl status` and that a time sync daemon is enabled |
| 403 that says "Token already used" | A bot call was retried with the same headers. Nonces are single use, and `services/portal.py` builds fresh headers on every attempt, so this points at a proxy retrying for you |
| `Flood wait of Ns` in the log | Telegram rate limiting. Short waits are slept through, long ones reschedule the send. Nothing to do |
| `database is locked` | Two processes on one database. Check for both a tmux session and a systemd unit running |
| A button answers "That button has expired" | The row was deleted or never written. Navigation buttons never expire, so this means the database was replaced. Run the command again |
| The bot is silent in a group | Expected. Privacy mode is on and group joining is off, so this is a direct message service |
| The approval prompt never arrives | Check `TELEGRAM_BOT_TOKEN` and `TELEGRAM_BOT_USERNAME` in the **Vercel** environment, not just in `.env`. The portal sends that message, not the VPS. Check the Vercel function logs for a `[telegram]` line. The account also has to have pressed Start at least once, since a bot cannot message someone who never opened the chat |
| No code arrives with the prompt | The account is at its code budget, three in five minutes counting the automatic pushes and `/code` together. The prompt still works, and so do recovery codes |
| The Sign in button says the request is no longer valid | The token is single use and dies with its challenge, which lasts two minutes. Somebody opened it twice, it was already approved, or it expired. Start the sign in again |
| `/code` answers that two factor authentication is off | The account is linked but has not turned it on. That is the toggle in the Settings tab |
| Locked out with the second factor on | Use a recovery code at the waiting step. It works with this service fully stopped |
| Telegram access lost on a linked account | Recovery codes get you into the portal, but they do not detach the link, since unlinking needs a code sent to a chat that is gone and this service refuses to unlink by design. An administrator has to clear the link and the second factor on that account directly |
| The Settings tab is not there | It sits inside the Admin Panel, which opens for admins only. Confirm the account is an admin, and that the deploy in step 10c went out |
