"""/about, /whoami, /notify and /status, plus the My account button."""

from __future__ import annotations

import logging
from typing import Any

from .. import callbacks, rich
from ..context import Ctx
from ..db import parse_iso, utcnow
from ..services.portal import PortalError, PortalUnavailable
from . import (
    command,
    donate_button,
    portal_button,
    reply_id,
    web_app_button,
)

log = logging.getLogger("uwu.handlers.misc")

REPO_URL = "https://github.com/augy-studios/uwuapps-portal"
CODE_OF_CONDUCT_URL = f"{REPO_URL}/blob/main/CODE_OF_CONDUCT.md"
PLAY_URL = "https://play.google.com/store/apps/details?id=org.uwuapps.portal"

TOPIC_NEW_APPS = "new_apps"

ABOUT = (
    "UwU Suite is a directory of small web apps, games and tools made by UwU "
    "Apps. Everything in it runs in a browser, and the same directory is "
    "installable as an app on Android and as a progressive web app everywhere "
    "else.\n\n"
    "This chat is one more way in. It lists what is published, tells you when "
    "something new lands if you ask it to, and acts as the second step when "
    "you sign in to the portal with two factor authentication turned on.\n\n"
    "The portal is open source, and it has a code of conduct that applies to "
    "everyone taking part."
)


@command("about", "Read more about UwU Suite", weight=50)
async def handle_about(event: Any, args: str, ctx: Ctx) -> None:
    await rich.send_rich_message(
        ctx.client,
        event.chat_id,
        rich.message(ABOUT, title="About UwU Suite"),
        buttons=[
            [web_app_button(ctx), donate_button(ctx)],
            [rich.Btn.link("On Google Play", PLAY_URL)],
            [
                rich.Btn.link("Source code", REPO_URL),
                rich.Btn.link("Code of conduct", CODE_OF_CONDUCT_URL),
            ],
        ],
        reply_to=reply_id(event),
        owner_id=event.sender_id,
    )


# --- account ---------------------------------------------------------------


NOT_LINKED = (
    "This chat is not linked to a portal account yet.\n\n"
    "Run /link and it walks you through it."
)


async def build_account(ctx: Ctx, telegram_id: int) -> tuple[dict[str, str], list[list[rich.Btn]]]:
    """Who this chat is linked to, refreshed from the portal when it answers."""
    buttons = [[portal_button(ctx)]]
    row = await ctx.db.get_link(telegram_id)
    if row is None:
        return rich.message(NOT_LINKED, title="Not linked yet"), buttons

    mfa_line = ""
    roles = []
    try:
        account = await ctx.portal.lookup_link(telegram_id)
    except (PortalUnavailable, PortalError):
        account = None

    if account is not None:
        await ctx.db.upsert_link(
            telegram_id,
            account.portal_user_id,
            account.username,
            account.display_name,
            account.is_admin,
            account.is_editor,
            account.is_approved,
        )
        row = await ctx.db.get_link(telegram_id)
        if account.is_admin:
            roles.append("admin")
        elif account.is_editor:
            roles.append("editor")
        elif account.is_approved:
            roles.append("viewer")
        else:
            roles.append("awaiting approval")
        mfa_line = (
            "Two factor authentication is on."
            if account.mfa_enabled
            else "Two factor authentication is off."
        )
    elif row["is_admin"]:
        roles.append("admin")

    name = row["display_name"] or row["portal_username"] or "your portal account"
    linked_at = str(row["linked_at"] or "")[:10]

    details: list[str] = []
    if row["portal_username"]:
        details.append(f"- Username {rich.code(row['portal_username'])}")
    if roles:
        details.append(f"- Role: {rich.escape_md(', '.join(roles))}")
    if linked_at:
        details.append(f"- Linked on {rich.escape_md(linked_at)}")
    if mfa_line:
        details.append(f"- {mfa_line}")

    paragraphs = [f"Linked to {rich.bold(name)}."]
    if details:
        paragraphs.append("\n".join(details))
    if account is None:
        paragraphs.append(rich.STALE_DATA)
    paragraphs.append(
        "The link is managed from the Settings tab in the Admin Panel on the portal."
    )
    return rich.message("\n\n".join(paragraphs), title="Your account"), buttons


async def show_account(event: Any, ctx: Ctx, edit: Any = None) -> None:
    telegram_id = event.sender_id
    view, buttons = await build_account(ctx, telegram_id)
    await rich.send_rich_message(
        ctx.client,
        event.chat_id,
        view,
        buttons=buttons,
        reply_to=reply_id(event),
        owner_id=telegram_id,
        edit=edit,
    )


@command("whoami", "See which portal account is linked here", weight=60)
async def handle_whoami(event: Any, args: str, ctx: Ctx) -> None:
    await show_account(event, ctx)


@callbacks.action("account.show")
async def _cb_account(event: Any, payload: dict[str, Any], ctx: Ctx) -> None:
    await event.answer()
    await show_account(event, ctx)


# --- announcements ---------------------------------------------------------


async def _notify_state(event: Any, ctx: Ctx, edit: Any = None) -> None:
    telegram_id = event.sender_id
    on = await ctx.db.is_subscribed(telegram_id, TOPIC_NEW_APPS)
    body = (
        "New app announcements are on. You will get one short message when "
        "something new is published."
        if on
        else "New app announcements are off. Nothing will be sent unless you ask."
    )
    label = "Turn them off" if on else "Turn them on"
    await rich.send_rich_message(
        ctx.client,
        event.chat_id,
        rich.message(body, title="Announcements"),
        buttons=[
            [
                rich.Btn.callback(
                    label, "notify.toggle", {"on": not on}, owner_id=telegram_id
                )
            ]
        ],
        reply_to=reply_id(event),
        owner_id=telegram_id,
        edit=edit,
    )


@command("notify", "Turn new app announcements on or off", weight=70)
async def handle_notify(event: Any, args: str, ctx: Ctx) -> None:
    choice = args.strip().lower()
    telegram_id = event.sender_id
    if choice in {"on", "yes", "start"}:
        await ctx.db.subscribe(telegram_id, TOPIC_NEW_APPS)
    elif choice in {"off", "no", "stop"}:
        await ctx.db.unsubscribe(telegram_id, TOPIC_NEW_APPS)
    await _notify_state(event, ctx)


@callbacks.action("notify.toggle")
async def _cb_notify(event: Any, payload: dict[str, Any], ctx: Ctx) -> None:
    telegram_id = event.sender_id
    if payload.get("on"):
        await ctx.db.subscribe(telegram_id, TOPIC_NEW_APPS)
        await event.answer("Announcements are on.")
    else:
        await ctx.db.unsubscribe(telegram_id, TOPIC_NEW_APPS)
        await event.answer("Announcements are off.")
    await _notify_state(event, ctx, edit=await event.get_message())


# --- status ----------------------------------------------------------------


async def build_status(ctx: Ctx) -> dict[str, str]:
    started = parse_iso(ctx.started_at)
    uptime = (
        rich.humanize_seconds((utcnow() - started).total_seconds())
        if started
        else "unknown"
    )

    try:
        size_bytes = ctx.config.db_path.stat().st_size
        size = f"{size_bytes / 1024 / 1024:.1f} MB"
    except OSError:
        size = "unknown"

    last_call = ctx.portal.last_success_at or "not yet this run"
    pending = await ctx.scheduler.pending_count()

    body = rich.table(
        ["Value"],
        [
            ("Running for", uptime),
            ("Local database", size),
            ("Last successful portal call", last_call),
            ("Jobs waiting", pending),
        ],
    )
    return rich.message(body, title="Status")


@command("status", "Check that everything is running", weight=80)
async def handle_status(event: Any, args: str, ctx: Ctx) -> None:
    await rich.send_rich_message(
        ctx.client, event.chat_id, await build_status(ctx),
        reply_to=reply_id(event), owner_id=event.sender_id,
    )
