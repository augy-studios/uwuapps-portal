"""Two factor authentication, the small part of it that lives in the bot.

The portal owns the flow. It verifies, so it issues: the bot never generates a
code, never mints a Sign in token, and never decides whether a challenge is
still valid. What is here is a callback handler for the buttons the portal
sends, a resolution call, and /code as the pull path when a pushed message did
not arrive or expired.

Handling rule for /code: the reply carries a live credential in two places, the
<code> block and the copy button markup. The whole message is exempt from
logging, never reaches logs/bot.log, and is deleted by a scheduled job when the
code expires. Messages the portal pushed are the portal's to clear, because the
bot holds no ids for them.
"""

from __future__ import annotations

import logging
from typing import Any

from .. import rich
from ..context import Ctx
from ..db import parse_iso, utcnow
from ..services.portal import PortalError, PortalUnavailable
from . import command, portal_button, reply_id

log = logging.getLogger("uwu.handlers.mfa")

PREFIX = "mfa:"
NEVER_ASKED = "Nobody from the team will ever ask you for this code."

NOT_LINKED = (
    "This chat is not linked to a portal account yet, so there is no account to "
    "sign in to.\n\n"
    "Run /link to attach one."
)

NOT_ENABLED = (
    "Two factor authentication is off for your account, so a code is not needed "
    "to sign in.\n\n"
    "You can turn it on from the Settings tab in the Admin Panel on the portal."
)

NO_LONGER_VALID = "That request is no longer valid. Start the sign in again."


@command("code", "Get a one time code for signing in", weight=20)
async def handle_code(event: Any, args: str, ctx: Ctx) -> None:
    telegram_id = event.sender_id

    link = await ctx.db.get_link(telegram_id)
    if link is None:
        await rich.send_rich_message(
            ctx.client, event.chat_id, NOT_LINKED, title="Not linked yet",
            buttons=[[portal_button(ctx)]],
            reply_to=reply_id(event), owner_id=telegram_id,
        )
        return

    try:
        issued = await ctx.portal.mfa_issue_code(telegram_id)
    except PortalUnavailable:
        await rich.send_rich_message(
            ctx.client, event.chat_id, rich.PORTAL_DOWN,
            reply_to=reply_id(event), owner_id=telegram_id,
        )
        return
    except PortalError as exc:
        message = NOT_ENABLED if exc.code == "mfa_disabled" else rich.esc(exc.message)
        await rich.send_rich_message(
            ctx.client, event.chat_id, message, title="No code issued",
            buttons=[[portal_button(ctx)]],
            reply_to=reply_id(event), owner_id=telegram_id,
        )
        return

    remaining = issued.seconds_remaining
    if not remaining:
        expiry = parse_iso(issued.expires_at)
        remaining = int((expiry - utcnow()).total_seconds()) if expiry else 300

    lines = [
        f"Your code is {rich.code_block(issued.code)}",
        f"It works for the next {rich.humanize_seconds(remaining)}. "
        "Type it into the page that is waiting for it.",
    ]
    if issued.superseded_pushed_code:
        lines.append(
            "This is now the current code. Any earlier one no longer works."
        )
    lines.append(NEVER_ASKED)

    sent = await rich.send_rich_message(
        ctx.client,
        event.chat_id,
        "\n\n".join(lines),
        title="One time code",
        buttons=[[rich.Btn.copy("Copy the code", issued.code)]],
        reply_to=reply_id(event),
        owner_id=telegram_id,
        sensitive=True,
    )

    # Audit row only. The digits never reach SQLite or a log line.
    await ctx.db.log_mfa_event(telegram_id, "code_issued")

    if sent is not None:
        await ctx.scheduler.schedule(
            "mfa.expire_code_message",
            {"chat_id": sent.chat_id, "message_id": sent.id},
            delay_seconds=max(30, remaining + 5),
            max_attempts=2,
        )


# --- the portal's buttons --------------------------------------------------


async def handle_callback(event: Any, ctx: Ctx) -> bool:
    """Handle `mfa:a:<challenge_id>` and `mfa:d:<challenge_id>`.

    These are the deliberate exception to the token registry in callbacks.py,
    because the portal composed the message and this database has never seen
    it. A UUID fits inside the 64 byte callback_data limit, and the portal is
    authoritative about whether the challenge is still live.
    """
    raw = event.data or b""
    try:
        data = raw.decode("utf-8")
    except UnicodeDecodeError:
        return False
    if not data.startswith(PREFIX):
        return False

    _, _, rest = data.partition(PREFIX)
    verb, _, challenge_id = rest.partition(":")
    decision = {"a": "approve", "d": "deny"}.get(verb)
    if decision is None or not challenge_id:
        await event.answer(NO_LONGER_VALID, alert=True)
        return True

    telegram_id = event.sender_id

    try:
        # The portal verifies that this Telegram id owns the challenge. The
        # challenge id alone is never enough.
        result = await ctx.portal.mfa_resolve(challenge_id, telegram_id, decision)
    except PortalUnavailable:
        await event.answer(rich.PORTAL_DOWN, alert=True)
        return True
    except PortalError as exc:
        await event.answer(rich.esc(exc.message) or NO_LONGER_VALID, alert=True)
        await _strip_buttons(event, ctx, NO_LONGER_VALID)
        return True

    status = str(result.get("status") or decision)
    await ctx.db.log_mfa_event(
        telegram_id, "approved" if status == "approved" else "denied", challenge_id
    )

    if status == "approved":
        await event.answer("Approved.")
        await _strip_buttons(
            event,
            ctx,
            "Sign in approved. The page that was waiting will continue on its own.",
        )
    else:
        await event.answer("Stopped.")
        await _strip_buttons(
            event,
            ctx,
            (
                "That sign in was stopped and every session for the account has "
                "been signed out.\n\n"
                "Somebody had the password, so change it now on the portal."
            ),
        )
    return True


async def _strip_buttons(event: Any, ctx: Ctx, outcome: str) -> None:
    """Edit the prompt down to a plain outcome line with the buttons removed."""
    try:
        message = await event.get_message()
    except Exception:
        message = None
    if message is None:
        return
    try:
        await rich.send_rich_message(
            ctx.client, event.chat_id, outcome, edit=message, buttons=None
        )
    except Exception:
        log.warning("Could not edit the prompt after resolving it", exc_info=True)
