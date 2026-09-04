# UwU Suite on Telegram

A Telethon front end for the [UwU Suite portal](https://uwuapps.org). It lets you
browse the app directory from a chat, hear about new apps, link your portal
account, and use Telegram as the second step when you sign in.

New here? Start with [SETUP.md](SETUP.md), which walks through BotFather, the
VPS, and the portal side changes that linking depends on.

---

## What it does

| Command | What happens |
| --- | --- |
| `/start` | The whole reference surface. What UwU Suite is, every command, and the buttons. There is no `/help`, this is it |
| `/link` | Starts or finishes linking this chat to your portal account. With a code, redeems it. Without one, explains where a code comes from |
| `/code` | A six digit one time code for signing in, with a copy button. Mostly a fallback, since a sign in already pushes one |
| `/browse` | The published directory, five per page, with Previous and Next, and a sort button under the pager |
| `/about` | Background, plus links to the site, the Play listing and the code of conduct |
| `/whoami` | Which portal account is linked here, the role, and when the link was made |
| `/notify` | Turn new app announcements on or off |
| `/status` | Uptime, database size, last successful portal call, jobs waiting |
| `/stats` | Admin only, usage numbers for the last seven days |
| `/unlink` | Explains that unlinking is done on the portal. It never unlinks anything, by design |
| `/manage` | Editors and admins only. The way in to everything below |
| `/add` | Editors and admins only. Opens a form and adds an app to the directory |
| `/edit` | Editors and admins only. The same form, over an app that is already listed |
| `/publish` | Editors and admins only. Publishes an app, or takes one back to a draft |
| `/delete` | Admins only. Removes an app, after a confirmation |

**The sort button sits under the pager**, on browse pages and on search results
alike. Pressing it moves one step round **Default**, **A to Z**, **Z to A**,
**Newest**, **Oldest** and back to the start, and the label always names the
order you are looking at rather than the one you are about to get. The order is
carried by every button on the page, so opening an app and coming back keeps it.
There is no `/new`, because newest first is now one press away.

**Anything that is not a command is a search.** Type `wordle` in a private chat
and it finds the app. One match opens the card directly, several show a page of
results, none offers you a way onward rather than a dead end.

### Sample output

```
UwU Suite

UwU Suite is a small directory of web apps, games and tools by UwU Apps.
This chat is a front door to it. Browse what is published, hear about new
arrivals, and use it as the second step when you sign in to the portal.

Commands
/start - See what this is and how to begin
/link - Link this Telegram account to your portal account
/code - Get a one time code for signing in
/browse - Browse the published apps
/about - Read more about UwU Suite
/whoami - See which portal account is linked here
/notify - Turn new app announcements on or off
/status - Check that everything is running

You can also just type a name. Anything that is not a command searches the
directory, so typing wordle finds the app.

[ Open the web app ] [ Support the project ]
[ Link my account ]
```

---

## Managing the directory

The five management commands are not in the public command list and not in the
`/start` list for people who cannot run them. Asking for one you cannot run gets
the same unknown command reply as a typo, so the surface never announces itself.

### Who gets in

| | Sees the commands | May add and edit | May delete |
| --- | --- | --- | --- |
| Not linked | no, unless listed in `ADMIN_TELEGRAM_IDS` | no | no |
| Linked, awaiting approval | no | no | no |
| Linked viewer | no | no | no |
| Linked editor | yes | own apps only | no |
| Linked admin | yes | any app | yes |

Two gates, deliberately different. Whether to *offer* a command is decided
locally, from the role cached when the portal was last asked, so an outage does
not hide the whole surface. Whether a *write* is allowed is decided by the
portal, against the linked account, every single time. An operator listed in
`ADMIN_TELEGRAM_IDS` is offered the commands, and still has to link an account
before anything can be written, because the directory is changed as an account
and never as an anonymous caller.

### The form

`/add` opens one message with a button per field. Press **Title**, send the
title, and the message becomes the form again with the title filled in. Tags are
buttons rather than typing, because the portal only accepts four of them. The
whole flow lives in that one message, so a long draft leaves one message in the
chat and not a column of them.

```
New app

Title: Tip Splitter
Link: https://tips.uwuapps.org
Description: Split a bill and the service charge in Singapore
Tags: tools, singapore
Cover image: not set
Published date: 2026-09-02
Sort order: 10
Visibility: Kept as a draft

[ Title ] [ Link ]
[ Description ] [ Tags ]
[ Cover image ] [ Published date ]
[ Sort order ]
[ Publish it ]
[ Save it ] [ Discard ]
```

A draft lives in SQLite, so a restart in the middle of one costs nothing. One
draft is kept per person: `/add` while another is open offers to carry on with
it or throw it away, which keeps resuming unambiguous.

`/edit` and `/publish` and `/delete` all start with a picker. An editor is only
shown the apps they created, since the portal refuses the rest, and offering a
row that always ends in a refusal is worse than not offering it.

An edit sends only the fields that were actually changed. An untouched gallery,
or any column the chat does not model, is left exactly as the Admin Panel left
it.

### What is deliberately absent

- **Uploading images.** A cover image is set by URL. Pictures belong in the
  Admin Panel, which has the upload endpoint and a preview
- **Deleting as an editor.** Deleting is an admin action on the portal, so it is
  one here too. `/publish` takes an app back to a draft, which hides it from the
  directory without losing the row
- **Approving users, and changing roles.** Those stay on the portal

---

## Linking your account

Linking does two things. It lets this chat answer as a known portal user, and
it turns Telegram into the second step for portal sign ins.

1. Sign in on the portal, open the **Admin Panel**, and choose the **Settings**
   tab
2. Press **Link Telegram**. The portal makes an eight character code and opens
   the chat for you
3. Press **Start** in the chat. That is it, the link is made
4. If the link did not open, the panel also shows the code as plain text. Send
   `/link YOURCODE` in the chat instead

A code lasts ten minutes and works once. Five failed attempts in fifteen minutes
pause linking from that Telegram account for an hour. One Telegram account maps
to exactly one portal account, and the reverse.

### Unlinking is done on the portal

`/unlink` in the chat will not unlink anything. It exists only to tell you
where to go. That is deliberate: somebody who picks up your unlocked phone
should not be able to detach your account from a chat window.

Unlinking takes three deliberate steps in the **Settings** tab:

1. Press **Unlink** and confirm what is about to be lost
2. Press **Continue**. A six digit code is sent to the linked chat
3. Type that code back into the page

So it costs both your portal session and access to the chat. If two factor
authentication is on, unlinking is refused until you turn it off, which costs
your password. The two stay separate and in that order on purpose: folding them
together would turn an unlink into a password free way to strip the second
factor off a hijacked session.

---

## Two factor authentication

Once you turn it on from the **Settings** tab, a correct password alone no
longer signs you in.

### What a sign in looks like

You type your password on the portal. The page then shows a two digit **match
number** and a field for a six digit code, and two messages arrive in your chat:

- **The approval prompt.** It says what is being approved, the time, the device
  and the rough location, and it repeats the match number. Buttons: **Approve**
  and **This was not me**
- **The code.** Six digits in a tap to copy block, a **Sign in** button, and a
  **Copy the code** button

They are two messages on purpose. One is a decision, the other is a credential,
and a copy button does not belong next to a deny button.

**Check the match number before you approve.** The page you are signing in from
shows the same two digits. If they do not match, somebody else has your password
and is trying to get you to wave them through. Press **This was not me**, which
kills every session on the account and tells you to change your password.

Three ways to finish, and any one of them works:

- Press **Approve** in the chat
- Type or paste the six digits into the page
- Press **Sign in** on the code message

### About that Sign in button

It opens a page that asks you one more time, showing the match number, the time
and the device, with **Yes, this was me** and **No, this was not me**. That
extra press is the point. Link scanners, mail filters and antivirus proxies all
fetch URLs they see with nobody involved, so simply opening the link approves
nothing. It is also where the match number defence survives, since a one tap
approval is exactly the reflex the match number exists to stop.

The tap usually lands on your phone while the sign in is on a laptop. The
session goes to the browser that was waiting, which is what the page says. When
both are the same device, the page offers **Continue here**.

### `/code`

`/code` gets a fresh code whenever the pushed one is gone, expired, or never
arrived, and it is the only way to hold a code before a sign in is even started.
Requesting one invalidates the previous one, so there is never more than one
live code. When `/code` supersedes a pushed code, the older message is edited
down and its copy button removed.

The portal generates every code. This chat only ever displays what it is handed.

### The standing warning

**Nobody from the team will ever ask you for a code.** Not in a chat, not on a
call, not in an email.

**A code arriving out of nowhere means somebody has your password.** You did not
start that sign in, so somebody else did, with a password that works. Press
**This was not me** on the prompt, then change your password immediately.

### Recovery codes

Ten codes, shown once, at the moment you turn the second factor on. Each works
once, and each signs you in without Telegram. Save them somewhere that is not
your phone.

Entering one at the waiting step completes the sign in with the chat unreachable
and this service fully stopped. Regenerating them invalidates every previous one.

### If you lose access to your Telegram account

Recovery codes get you back into the portal. They do not detach the old link,
because unlinking needs a code sent to the chat that is gone, and this side
refuses to unlink by design.

So: sign in with a recovery code, then ask an administrator to clear the link
and the second factor on your account. There is no self serve path out of that
state, which is a consequence of unlinking being deliberately expensive.

---

## Privacy

What is kept on the VPS, in one SQLite file:

| Stored | For how long |
| --- | --- |
| Your Telegram id, username, first name and language | Until you block the chat or the row is cleaned up |
| Which portal account is linked, its username and role | Until the link is removed on the portal |
| Announcement subscriptions | Until you turn them off |
| Command name, outcome and duration, with your Telegram id | 30 days |
| That a code was issued, and that an approval or refusal happened | 30 days |
| A cached copy of the published app list | 15 minutes |

What is never stored, anywhere on this side:

- The digits of any one time code, in the database or in a log file
- Recovery codes
- Linking codes
- The contents of your messages, including anything you search for
- Sign in tokens

Codes are the portal's to hold, and it holds only their hashes. The reply
carrying a code is deleted from the chat when the code expires.

---

## Running it

The bot runs inside tmux on a Debian 13 box.

```bash
tmux new -s uwubot
cd ~/uwuapps-portal/telegram-bot
./run.sh
```

Detach with `Ctrl-b` then `d`. Reattach with `tmux attach -t uwubot`.

To update:

```bash
tmux attach -t uwubot
# Ctrl-c to stop
git pull
.venv/bin/pip install -r requirements.txt
./run.sh
# Ctrl-b then d to detach
```

tmux does not survive a reboot. [systemd/uwu-telegram-bot.service](systemd/uwu-telegram-bot.service)
is there for unattended restarts, and [SETUP.md](SETUP.md) covers it.

Logs go to `logs/bot.log`, rotating at 10 MB with five backups, and to stdout so
the tmux pane stays useful.

### Tests

```bash
.venv/bin/python -m pytest
```

They need no Telegram connection and no portal. They cover the message style
rules, that no code path can delete a link, that buttons survive a restart, and
that a hard kill strands no job.

### The BotFather command list

Generated from the same registry that renders `/start`, so the two cannot drift:

```bash
.venv/bin/python bot.py --botfather
```

---

## Project layout

```
telegram-bot/
  bot.py                        entrypoint, wires everything and blocks
  run.sh                        venv activation plus exec python
  requirements.txt              pinned exactly
  bot/
    config.py                   env parsing, fails fast on a missing key
    logging_setup.py            rotating file handler plus stdout
    db.py                       connection, WAL pragmas, migration runner
    context.py                  the object every handler is handed
    rich.py                     send_rich_message, the one outgoing door
    callbacks.py                persistent inline button registry
    scheduler.py                SQLite backed job loop
    migrations/001_init.sql     the whole local schema
    handlers/
      __init__.py               command registry and the dispatcher
      start.py link.py mfa.py apps.py misc.py admin.py fallback.py
    services/
      signing.py                the bot to portal signature headers
      portal.py                 typed wrappers over the portal API
      cache.py                  read through cache over api_cache
    jobs/
      __init__.py new_apps.py gc.py
  systemd/uwu-telegram-bot.service
  tests/
```

The portal side of this lives in [../main-site/](../main-site/):
`api/telegram.js`, `api/auth/magic.js`, the `login` change in `api/auth.js`, the
Settings tab in `index.html` and `script.js`, and the schema in
`sql/002_telegram_mfa.sql`.

## Licence

See [LICENSE](../LICENSE) in the repository root.
