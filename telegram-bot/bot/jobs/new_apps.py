"""Refresh the cached app list, and tell subscribers about anything new."""

from __future__ import annotations

import logging
from typing import Any

from .. import rich
from ..context import Ctx
from ..db import iso
from ..handlers import apps as apps_handler
from ..handlers.misc import TOPIC_NEW_APPS

log = logging.getLogger("uwu.jobs.new_apps")

# Paced well under the Telegram limit, and slow enough that a large
# announcement never starves the message handlers.
SEND_INTERVAL_SECONDS = 0.05
ANNOUNCE_CAP = 5


async def refresh(payload: dict[str, Any], ctx: Ctx) -> None:
    """Warm the cache before it expires, so no user pays for the round trip."""
    apps, _ = await apps_handler.fetch_apps(ctx, force=True)
    log.info("Refreshed the app list, %d published apps", len(apps))


def gallery(app: dict[str, Any]) -> list[str]:
    """The listing's images, the thumbnail first and the rest in gallery order.

    Reads the columns the way the site does: the gallery when there is one,
    otherwise the lone thumbnail an older row may still carry.
    """
    urls = [str(u) for u in (app.get("gallery_urls") or []) if u]
    if not urls and app.get("thumbnail_url"):
        urls = [str(app["thumbnail_url"])]
    if not urls:
        return []
    try:
        start = int(app.get("thumbnail_index") or 0)
    except (TypeError, ValueError):
        start = 0
    start = max(0, min(start, len(urls) - 1))
    ordered = [urls[start], *urls[:start], *urls[start + 1:]]
    return list(dict.fromkeys(u for u in ordered if u.startswith(("http://", "https://"))))


def build_announcement(app: dict[str, Any]) -> tuple[dict[str, str], list[list[rich.Btn]]]:
    title = app.get("title") or "Untitled"
    description = apps_handler.trim(app.get("description") or "")
    body = rich.lines(
        rich.bold(title),
        rich.escape_md(description) if description else "",
    )
    buttons: list[list[rich.Btn]] = []
    url = str(app.get("url") or "")
    if url.startswith(("http://", "https://")):
        buttons.append([rich.Btn.link("Open the app", url)])

    def compose(images: list[str]) -> dict[str, str]:
        return rich.message(
            "\n\n".join([body, *images]),
            title="New in the directory",
            footer="Turn these off any time with /notify.",
        )

    # Images go in one at a time while the message still fits, because one
    # that does not fit goes out as plain text and would lose them all.
    images: list[str] = []
    view = compose(images)
    for number, image_url in enumerate(gallery(app), start=1):
        candidate = [*images, rich.image(image_url, f"{title}, image {number}")]
        composed = compose(candidate)
        if len(composed["markdown"]) > rich.TELEGRAM_LIMIT:
            break
        images, view = candidate, composed
    return view, buttons


async def announce(payload: dict[str, Any], ctx: Ctx) -> None:
    """Diff against seen_apps and notify the new_apps subscribers."""
    apps, stale = await apps_handler.fetch_apps(ctx)
    if stale:
        # An outage must not look like every app being new when it clears.
        log.info("Skipping the announcement pass, the app list is stale")
        return

    known = {
        row["app_id"]
        for row in await ctx.db.fetchall("select app_id from seen_apps")
    }
    fresh = [a for a in apps if str(a.get("id")) not in known]

    if not fresh:
        return

    now = iso()
    await ctx.db.executemany(
        """insert or ignore into seen_apps (app_id, title, first_seen_at)
           values (?, ?, ?)""",
        [(str(a.get("id")), a.get("title"), now) for a in fresh],
    )

    if not known:
        # First run after a cold start. Everything looks new, and announcing
        # the whole directory would be a poor introduction.
        log.info("Seeded seen_apps with %d apps, nothing announced", len(fresh))
        return

    subscribers = await ctx.db.subscribers(TOPIC_NEW_APPS)
    if not subscribers:
        return

    for app in fresh[:ANNOUNCE_CAP]:
        view, buttons = build_announcement(app)
        for telegram_id in subscribers:
            try:
                await rich.send_rich_message(
                    ctx.client, telegram_id, view, buttons=buttons or None,
                    owner_id=telegram_id,
                )
            except Exception as exc:
                if _is_blocked(exc):
                    await ctx.db.set_blocked(telegram_id, True)
                    log.info("Telegram id %s has blocked the chat, skipping", telegram_id)
                else:
                    log.warning("Could not announce to %s: %s", telegram_id, exc)
            await _pace()

    log.info("Announced %d new app(s) to %d subscriber(s)", len(fresh[:ANNOUNCE_CAP]), len(subscribers))


def _is_blocked(exc: Exception) -> bool:
    name = type(exc).__name__
    return "Blocked" in name or "UserIsBlocked" in name or "InputUserDeactivated" in name


async def _pace() -> None:
    import asyncio

    await asyncio.sleep(SEND_INTERVAL_SECONDS)
