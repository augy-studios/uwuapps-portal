"""The job type registry, plus the two jobs too small to deserve a module."""

from __future__ import annotations

import logging
from typing import Any

from .. import rich
from ..context import Ctx

log = logging.getLogger("uwu.jobs")

APPS_REFRESH_SECONDS = 15 * 60
ANNOUNCE_SECONDS = 15 * 60
CACHE_GC_SECONDS = 60 * 60
CALLBACK_GC_SECONDS = 24 * 60 * 60
LOG_GC_SECONDS = 24 * 60 * 60


async def expire_code_message(payload: dict[str, Any], ctx: Ctx) -> None:
    """Delete the bot's own /code reply once the code is dead.

    Only messages the bot sent. A code the portal pushed at login is cleared by
    the portal, which is the side that holds those message ids.
    """
    chat_id = payload.get("chat_id")
    message_id = payload.get("message_id")
    if not chat_id or not message_id:
        return
    try:
        await ctx.client.delete_messages(chat_id, [int(message_id)])
    except Exception as exc:
        # A user who already deleted it, or blocked the chat, is not an error.
        log.info("Could not delete an expired code message: %s", exc)


async def resend_message(payload: dict[str, Any], ctx: Ctx) -> None:
    """Deliver a send that a long flood wait pushed out of the moment.

    Only ever holds a message that carried no credential, see rich.py.
    """
    chat_id = payload.get("chat_id")
    text = payload.get("text")
    if not chat_id or not text:
        return
    buttons = rich.deserialize_buttons(payload.get("buttons") or [])
    await ctx.client.send_message(
        int(chat_id),
        text,
        parse_mode="html",
        buttons=buttons or None,
        link_preview=bool(payload.get("link_preview")),
    )


def register_all(scheduler: Any) -> None:
    from . import gc, new_apps

    scheduler.register("apps.refresh", new_apps.refresh)
    scheduler.register("apps.announce_new", new_apps.announce)
    scheduler.register("cache.gc", gc.cache_gc)
    scheduler.register("callbacks.gc", gc.callbacks_gc)
    scheduler.register("log.gc", gc.log_gc)
    scheduler.register("mfa.expire_code_message", expire_code_message)
    scheduler.register("message.resend", resend_message)


async def seed_recurring(scheduler: Any) -> None:
    await scheduler.ensure_recurring("apps.refresh", APPS_REFRESH_SECONDS)
    await scheduler.ensure_recurring("apps.announce_new", ANNOUNCE_SECONDS)
    await scheduler.ensure_recurring("cache.gc", CACHE_GC_SECONDS)
    await scheduler.ensure_recurring("callbacks.gc", CALLBACK_GC_SECONDS)
    await scheduler.ensure_recurring("log.gc", LOG_GC_SECONDS)
