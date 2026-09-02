"""The app directory: /apps, /new, and the free text search behind both.

One rendering path serves all three, so a search result page and a browse page
look the same and there is a single place to change the card layout.

Every string that came from the portal is escaped before it reaches a message,
because app titles and descriptions are user submitted.
"""

from __future__ import annotations

import logging
from typing import Any

from .. import callbacks, rich
from ..context import Ctx
from ..services.portal import PortalError, PortalUnavailable
from . import command, reply_id, web_app_button

log = logging.getLogger("uwu.handlers.apps")

CACHE_KEY = "apps:list"
DESC_LIMIT = 140
QUERY_LIMIT = 64
MIN_QUERY = 3

MODE_ALL = "all"
MODE_NEW = "new"
MODE_SEARCH = "search"


# --- data ------------------------------------------------------------------


async def fetch_apps(ctx: Ctx, *, force: bool = False) -> tuple[list[dict[str, Any]], bool]:
    """The published app list and whether it came from a stale cache."""
    hit = await ctx.cache.get_or_fetch(
        CACHE_KEY,
        ctx.portal.list_apps,
        ctx.config.apps_cache_ttl_seconds,
        force=force,
    )
    value = hit.value if isinstance(hit.value, list) else []
    return value, hit.stale


def _sort(apps: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    if mode == MODE_NEW:
        return sorted(
            apps,
            key=lambda a: (a.get("published_date") or a.get("created_at") or ""),
            reverse=True,
        )
    return sorted(
        apps,
        key=lambda a: (a.get("sort_order") if a.get("sort_order") is not None else 0,
                       (a.get("title") or "").lower()),
    )


def matches(app: dict[str, Any], needle: str) -> bool:
    """Case insensitive literal substring over title, description and tags."""
    haystack = " ".join(
        [
            str(app.get("title") or ""),
            str(app.get("description") or ""),
            " ".join(str(t) for t in (app.get("tags") or [])),
        ]
    ).lower()
    return needle in haystack


def select(apps: list[dict[str, Any]], mode: str, query: str) -> list[dict[str, Any]]:
    if mode == MODE_SEARCH and query:
        needle = query.lower()
        return _sort([a for a in apps if matches(a, needle)], MODE_ALL)
    return _sort(apps, mode)


def _find(apps: list[dict[str, Any]], app_id: str) -> dict[str, Any] | None:
    return next((a for a in apps if str(a.get("id")) == str(app_id)), None)


# --- rendering -------------------------------------------------------------


def trim(text: str, limit: int = DESC_LIMIT) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _title_for(mode: str, query: str) -> str:
    if mode == MODE_NEW:
        return "Recently published"
    if mode == MODE_SEARCH:
        return f"Results for {query}"
    return "The app directory"


def render_list(
    ctx: Ctx,
    selected: list[dict[str, Any]],
    *,
    mode: str,
    query: str,
    page: int,
    stale: bool,
    owner_id: int,
) -> tuple[str, str, list[list[rich.Btn]], str]:
    size = ctx.config.page_size
    pages = max(1, (len(selected) + size - 1) // size)
    page = max(0, min(page, pages - 1))
    window = selected[page * size : page * size + size]

    lines: list[str] = []
    for offset, app in enumerate(window, start=1):
        number = page * size + offset
        title = rich.esc(app.get("title") or "Untitled")
        entry = f"{number}. <b>{title}</b>"
        description = trim(app.get("description") or "")
        if description:
            entry += f"\n{rich.esc(description)}"
        tags = [str(t) for t in (app.get("tags") or [])]
        if tags:
            entry += f"\n<i>{rich.esc(', '.join(tags))}</i>"
        lines.append(entry)

    body = "\n\n".join(lines) if lines else "Nothing here yet."

    open_row = [
        rich.Btn.callback(
            str(page * size + offset),
            "apps.open",
            {"id": str(app.get("id")), "mode": mode, "q": query, "page": page},
            owner_id=owner_id,
        )
        for offset, app in enumerate(window, start=1)
    ]

    nav: list[rich.Btn] = []
    if page > 0:
        nav.append(
            rich.Btn.callback(
                "Previous", "apps.page",
                {"mode": mode, "q": query, "page": page - 1}, owner_id=owner_id,
            )
        )
    if page < pages - 1:
        nav.append(
            rich.Btn.callback(
                "Next", "apps.page",
                {"mode": mode, "q": query, "page": page + 1}, owner_id=owner_id,
            )
        )

    buttons = [row for row in (open_row, nav) if row]
    buttons.append([web_app_button(ctx)])

    footer_bits = [f"Page {page + 1} of {pages}, {len(selected)} app"
                   f"{'s' if len(selected) != 1 else ''}"]
    if stale:
        footer_bits.append(rich.STALE_DATA)
    footer = "\n".join(footer_bits)

    return _title_for(mode, query), body, buttons, footer


def render_card(
    ctx: Ctx,
    app: dict[str, Any],
    *,
    mode: str,
    query: str,
    page: int,
    owner_id: int,
) -> tuple[str, str, list[list[rich.Btn]]]:
    title = rich.esc(app.get("title") or "Untitled")
    parts = []
    description = str(app.get("description") or "").strip()
    if description:
        parts.append(rich.esc(trim(description, 900)))

    meta: list[str] = []
    tags = [str(t) for t in (app.get("tags") or [])]
    if tags:
        meta.append("Tags: " + rich.esc(", ".join(tags)))
    published = app.get("published_date") or ""
    if published:
        meta.append("Published " + rich.esc(str(published)[:10]))
    if meta:
        parts.append("\n".join(meta))

    body = "\n\n".join(parts) if parts else "No description yet."

    buttons: list[list[rich.Btn]] = []
    url = str(app.get("url") or "")
    if url.startswith(("http://", "https://")):
        buttons.append([rich.Btn.link("Open the app", url)])
    buttons.append(
        [
            rich.Btn.callback(
                "Back to the list", "apps.page",
                {"mode": mode, "q": query, "page": page}, owner_id=owner_id,
            )
        ]
    )
    return title, body, buttons


# --- shared entry point ----------------------------------------------------


async def show_page(
    event: Any,
    ctx: Ctx,
    *,
    mode: str,
    query: str = "",
    page: int = 0,
    edit: Any = None,
) -> None:
    owner_id = event.sender_id
    try:
        apps, stale = await fetch_apps(ctx)
    except (PortalUnavailable, PortalError):
        await rich.send_rich_message(
            ctx.client, event.chat_id, rich.PORTAL_DOWN,
            reply_to=reply_id(event), owner_id=owner_id, edit=edit,
        )
        return

    selected = select(apps, mode, query)

    if mode == MODE_SEARCH and not selected:
        await _no_matches(event, ctx, query, edit=edit)
        return

    # A single match skips the list and opens the card directly.
    if mode == MODE_SEARCH and len(selected) == 1:
        title, body, buttons = render_card(
            ctx, selected[0], mode=mode, query=query, page=0, owner_id=owner_id
        )
        await rich.send_rich_message(
            ctx.client, event.chat_id, body, title=title, buttons=buttons,
            reply_to=reply_id(event), owner_id=owner_id, edit=edit,
        )
        return

    title, body, buttons, footer = render_list(
        ctx, selected, mode=mode, query=query, page=page, stale=stale, owner_id=owner_id
    )
    await rich.send_rich_message(
        ctx.client, event.chat_id, body, title=title, footer=footer, buttons=buttons,
        reply_to=reply_id(event), owner_id=owner_id, edit=edit,
    )


async def _no_matches(event: Any, ctx: Ctx, query: str, edit: Any = None) -> None:
    owner_id = event.sender_id
    await rich.send_rich_message(
        ctx.client,
        event.chat_id,
        f"Nothing in the directory matches {rich.esc(query)}.",
        title="No matches",
        buttons=[
            [
                rich.Btn.callback(
                    "Browse everything", "apps.page",
                    {"mode": MODE_ALL, "q": "", "page": 0}, owner_id=owner_id,
                ),
                rich.Btn.callback(
                    "See what is new", "apps.page",
                    {"mode": MODE_NEW, "q": "", "page": 0}, owner_id=owner_id,
                ),
            ]
        ],
        reply_to=reply_id(event),
        owner_id=owner_id,
        edit=edit,
    )


async def search(event: Any, query: str, ctx: Ctx) -> None:
    """Free text in a private chat lands here. Never treated as a pattern."""
    cleaned = " ".join(query.split())[:QUERY_LIMIT]
    if len(cleaned) < MIN_QUERY:
        await rich.send_rich_message(
            ctx.client,
            event.chat_id,
            (
                "That is a little short to search on. Try three characters or "
                "more, or press the button to browse everything."
            ),
            title="A bit more to go on",
            buttons=[
                [
                    rich.Btn.callback(
                        "Browse everything", "apps.page",
                        {"mode": MODE_ALL, "q": "", "page": 0}, owner_id=event.sender_id,
                    )
                ]
            ],
            reply_to=reply_id(event),
            owner_id=event.sender_id,
        )
        return
    await show_page(event, ctx, mode=MODE_SEARCH, query=cleaned, page=0)


# --- commands --------------------------------------------------------------


@command("apps", "Browse the published apps", weight=30)
async def handle_apps(event: Any, args: str, ctx: Ctx) -> None:
    if args.strip():
        await search(event, args, ctx)
        return
    await show_page(event, ctx, mode=MODE_ALL, page=0)


@command("new", "See the most recently published apps", weight=40)
async def handle_new(event: Any, args: str, ctx: Ctx) -> None:
    await show_page(event, ctx, mode=MODE_NEW, page=0)


# --- callbacks -------------------------------------------------------------


@callbacks.action("apps.page")
async def _cb_page(event: Any, payload: dict[str, Any], ctx: Ctx) -> None:
    await event.answer()
    message = await event.get_message()
    await show_page(
        event,
        ctx,
        mode=str(payload.get("mode") or MODE_ALL),
        query=str(payload.get("q") or ""),
        page=int(payload.get("page") or 0),
        edit=message,
    )


@callbacks.action("apps.open")
async def _cb_open(event: Any, payload: dict[str, Any], ctx: Ctx) -> None:
    await event.answer()
    message = await event.get_message()
    try:
        apps, _ = await fetch_apps(ctx)
    except (PortalUnavailable, PortalError):
        await rich.send_rich_message(
            ctx.client, event.chat_id, rich.PORTAL_DOWN,
            owner_id=event.sender_id, edit=message,
        )
        return

    app = _find(apps, str(payload.get("id") or ""))
    if app is None:
        await rich.send_rich_message(
            ctx.client,
            event.chat_id,
            "That app is no longer in the directory.",
            title="Not found",
            buttons=[
                [
                    rich.Btn.callback(
                        "Back to the list", "apps.page",
                        {"mode": payload.get("mode") or MODE_ALL,
                         "q": payload.get("q") or "",
                         "page": int(payload.get("page") or 0)},
                        owner_id=event.sender_id,
                    )
                ]
            ],
            owner_id=event.sender_id,
            edit=message,
        )
        return

    title, body, buttons = render_card(
        ctx,
        app,
        mode=str(payload.get("mode") or MODE_ALL),
        query=str(payload.get("q") or ""),
        page=int(payload.get("page") or 0),
        owner_id=event.sender_id,
    )
    await rich.send_rich_message(
        ctx.client, event.chat_id, body, title=title, buttons=buttons,
        owner_id=event.sender_id, edit=message,
    )
