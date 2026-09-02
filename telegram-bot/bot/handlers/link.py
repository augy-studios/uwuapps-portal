"""Linking, and the refusal that answers /unlink.

There is no code path in this file, or anywhere else in the bot, that deletes a
link. `redeem` writes the local mirror row, `lookup` refreshes it, and that is
the whole surface. Unlinking is a portal side action, so a borrowed Telegram
session cannot detach somebody's account. `tests/test_no_unlink.py` proves it
by driving /unlink and every button the bot can send.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from .. import callbacks, rich
from ..context import Ctx
from ..services.portal import PortalError, PortalUnavailable
from . import command, portal_button, reply_id

log = logging.getLogger("uwu.handlers.link")

# 8 uppercase base32 characters, as minted by the portal
CODE_RE = re.compile(r"^[A-Z2-7]{8}$")

FAILURE_WINDOW_SECONDS = 15 * 60
FAILURE_LIMIT = 5
LOCKOUT_SECONDS = 60 * 60

# Telegram ids currently mid flow, waiting on a typed code. In memory on
# purpose: after a restart the user simply runs /link again, and nothing about
# a half finished flow is worth persisting.
_awaiting: set[int] = set()

WHERE_TO_GET_A_CODE = (
    "Open the portal, sign in, then open the Admin Panel and choose the "
    "Settings tab. Press Link Telegram there and it hands you a code."
)

UNLINK_REFUSAL = (
    "Unlinking is done on the portal, not here.\n\n"
    "Open the Settings tab in the Admin Panel and press Unlink. It asks you to "
    "confirm, then sends a six digit code to this chat that you type back into "
    "the page. Doing it that way means somebody holding this chat alone cannot "
    "detach your account.\n\n"
    "If two factor authentication is on, turn it off first. That step costs "
    "your password, which is deliberate."
)


def normalize(raw: str) -> str:
    """Uppercase, strip the spaces and dashes people paste along with a code."""
    return re.sub(r"[\s-]+", "", raw or "").upper()


def is_awaiting(telegram_id: int) -> bool:
    return telegram_id in _awaiting


def clear_pending(telegram_id: int) -> None:
    _awaiting.discard(telegram_id)


async def consume_pending(event: Any, text: str, ctx: Ctx) -> bool:
    """Free text belongs to the linking flow when one is waiting on a code.

    Checked before the free text search, which is the priority rule the
    specification asks for.
    """
    telegram_id = event.sender_id
    if telegram_id not in _awaiting:
        return False
    candidate = normalize(text)
    if not CODE_RE.match(candidate):
        # Not a code at all, so the user has moved on. Drop the flow and let
        # the message fall through to search rather than nagging.
        clear_pending(telegram_id)
        return False
    await redeem(event, candidate, ctx)
    return True


# --- commands --------------------------------------------------------------


@command("link", "Link this Telegram account to your portal account", weight=10)
async def handle_link(event: Any, args: str, ctx: Ctx) -> None:
    telegram_id = event.sender_id

    existing = await ctx.db.get_link(telegram_id)
    if existing is not None and not args.strip():
        await _already_linked(event, ctx, existing)
        return

    code = normalize(args)
    if not code:
        await _explain(event, ctx)
        return

    await redeem(event, code, ctx)


@command("unlink", "Explains where unlinking is done", visible=False, weight=15)
async def handle_unlink(event: Any, args: str, ctx: Ctx) -> None:
    """Never unlinks anything.

    Kept as a handler rather than dropped so the word lands somewhere useful
    instead of falling through to the unknown command reply. There is no
    confirmation row and no callback, because there is nothing to confirm.
    """
    await rich.send_rich_message(
        ctx.client,
        event.chat_id,
        UNLINK_REFUSAL,
        title="Unlinking happens on the portal",
        buttons=[[portal_button(ctx)]],
        reply_to=reply_id(event),
        owner_id=event.sender_id,
    )


# --- the flow --------------------------------------------------------------


async def _explain(event: Any, ctx: Ctx) -> None:
    _awaiting.add(event.sender_id)
    await rich.send_rich_message(
        ctx.client,
        event.chat_id,
        (
            f"{WHERE_TO_GET_A_CODE}\n\n"
            "Press the link it opens, or send the code here on its own, or send "
            "it as <code>/link YOURCODE</code>. A code lasts ten minutes."
        ),
        title="Linking your account",
        buttons=[[portal_button(ctx)]],
        reply_to=reply_id(event),
        owner_id=event.sender_id,
    )


async def _already_linked(event: Any, ctx: Ctx, row: Any) -> None:
    name = row["display_name"] or row["portal_username"] or "your portal account"
    await rich.send_rich_message(
        ctx.client,
        event.chat_id,
        (
            f"This chat is already linked to {rich.esc(name)}.\n\n"
            "To attach a different account, or to remove this one, use the "
            "Settings tab in the Admin Panel on the portal."
        ),
        title="Already linked",
        buttons=[[portal_button(ctx)]],
        reply_to=reply_id(event),
        owner_id=event.sender_id,
    )


async def _locked_out(event: Any, ctx: Ctx) -> None:
    await rich.send_rich_message(
        ctx.client,
        event.chat_id,
        (
            "Too many codes have failed here recently, so linking is paused for "
            "an hour. Nothing is wrong with your account. Mint a fresh code from "
            "the Settings tab when the hour is up."
        ),
        title="Linking is paused",
        buttons=[[portal_button(ctx)]],
        reply_to=reply_id(event),
        owner_id=event.sender_id,
    )


async def redeem(event: Any, raw_code: str, ctx: Ctx, *, greeting: bool = False) -> None:
    """Redeem a code. The code itself is never echoed back or logged."""
    telegram_id = event.sender_id
    clear_pending(telegram_id)
    code = normalize(raw_code)

    failures = await ctx.db.recent_failed_link_attempts(telegram_id, LOCKOUT_SECONDS)
    if failures >= FAILURE_LIMIT:
        await _locked_out(event, ctx)
        return

    if not CODE_RE.match(code):
        await ctx.db.record_link_attempt(telegram_id, succeeded=False)
        await rich.send_rich_message(
            ctx.client,
            event.chat_id,
            (
                "That does not look like a linking code. A code is eight "
                "characters, letters and digits.\n\n" + WHERE_TO_GET_A_CODE
            ),
            title="Code not recognised",
            buttons=[[portal_button(ctx)]],
            reply_to=reply_id(event),
            owner_id=telegram_id,
        )
        return

    sender = await event.get_sender()
    username = getattr(sender, "username", None)

    try:
        account = await ctx.portal.redeem_link_code(code, telegram_id, username)
    except PortalUnavailable:
        await rich.send_rich_message(
            ctx.client, event.chat_id, rich.PORTAL_DOWN,
            reply_to=reply_id(event), owner_id=telegram_id,
        )
        return
    except PortalError as exc:
        await ctx.db.record_link_attempt(telegram_id, succeeded=False)
        recent = await ctx.db.recent_failed_link_attempts(telegram_id, FAILURE_WINDOW_SECONDS)
        note = ""
        if recent >= FAILURE_LIMIT - 1:
            note = "\n\nOne more failure and linking pauses here for an hour."
        await rich.send_rich_message(
            ctx.client,
            event.chat_id,
            f"{rich.esc(exc.message)}\n\n{WHERE_TO_GET_A_CODE}{note}",
            title="That code did not work",
            buttons=[[portal_button(ctx)]],
            reply_to=reply_id(event),
            owner_id=telegram_id,
        )
        return

    await ctx.db.record_link_attempt(telegram_id, succeeded=True)
    # The link row references users, and redeem is reachable from a deep link,
    # so make sure the user row is there rather than assuming the dispatcher ran.
    await ctx.db.touch_user(
        telegram_id,
        username,
        getattr(sender, "first_name", None),
        getattr(sender, "lang_code", None),
    )
    await ctx.db.upsert_link(
        telegram_id,
        account.portal_user_id,
        account.username,
        account.display_name,
        account.is_admin,
    )
    log.info("Linked telegram id %s to a portal account", telegram_id)

    name = account.display_name or account.username or "your portal account"
    opening = "Welcome. " if greeting else ""
    body = (
        f"{opening}This chat is now linked to {rich.esc(name)}.\n\n"
        "The link is removed from the Settings tab on the portal, not from this "
        "chat. You can also turn on two factor authentication there, which uses "
        "this chat as the second step when you sign in."
    )
    await rich.send_rich_message(
        ctx.client,
        event.chat_id,
        body,
        title="Linked",
        buttons=[[portal_button(ctx)]],
        reply_to=reply_id(event),
        owner_id=telegram_id,
    )


# --- callbacks -------------------------------------------------------------


@callbacks.action("link.start")
async def _cb_link_start(event: Any, payload: dict[str, Any], ctx: Ctx) -> None:
    await event.answer()
    telegram_id = event.sender_id
    existing = await ctx.db.get_link(telegram_id)
    if existing is not None:
        await _already_linked(event, ctx, existing)
        return
    _awaiting.add(telegram_id)
    await rich.send_rich_message(
        ctx.client,
        event.chat_id,
        (
            f"{WHERE_TO_GET_A_CODE}\n\n"
            "Then send the code here on its own, or as <code>/link YOURCODE</code>. "
            "A code lasts ten minutes."
        ),
        title="Linking your account",
        buttons=[[portal_button(ctx)]],
        owner_id=telegram_id,
    )
