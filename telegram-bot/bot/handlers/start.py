"""/start, the one reference surface.

There is no /help, so everything a help command would carry lives here: what
the portal is, the full command list, the note that typing a name searches the
directory, and the button row. The command list is rendered from the registry
in this package, never hand written, so it cannot drift.
"""

from __future__ import annotations

import logging
from typing import Any

from .. import rich
from ..context import Ctx
from . import (
    account_button,
    command,
    command_list_html,
    donate_button,
    web_app_button,
)

log = logging.getLogger("uwu.handlers.start")

INTRO = (
    "UwU Suite is a small directory of web apps, games and tools by UwU Apps. "
    "This chat is a front door to it. Browse what is published, hear about new "
    "arrivals, and use it as the second step when you sign in to the portal."
)

SEARCH_HINT = (
    "You can also just type a name. Anything that is not a command searches the "
    "directory, so typing <i>wordle</i> finds the app."
)


async def start_body(ctx: Ctx, telegram_id: int) -> str:
    return "\n\n".join(
        [
            INTRO,
            "<b>Commands</b>\n" + command_list_html(include_admin=ctx.is_admin(telegram_id)),
            SEARCH_HINT,
        ]
    )


async def start_buttons(ctx: Ctx, telegram_id: int) -> list[list[rich.Btn]]:
    return [
        [web_app_button(ctx), donate_button(ctx)],
        [await account_button(ctx, telegram_id)],
    ]


@command("start", "See what this is and how to begin", weight=0)
async def handle_start(event: Any, args: str, ctx: Ctx) -> None:
    # The deep link payload from the Settings tab on the portal arrives here as
    # /start link_<code>, because pressing Start on a t.me link with a start
    # payload sends exactly that.
    if args.startswith("link_"):
        from . import link

        await link.redeem(event, args[len("link_"):], ctx, greeting=True)
        return

    telegram_id = event.sender_id
    await rich.send_rich_message(
        ctx.client,
        event.chat_id,
        await start_body(ctx, telegram_id),
        title="UwU Suite",
        buttons=await start_buttons(ctx, telegram_id),
        reply_to=event.message.id,
        owner_id=telegram_id,
    )


async def show_start(event: Any, ctx: Ctx) -> None:
    """Used by the fallback reply, so there is one intro rather than two."""
    telegram_id = event.sender_id
    await rich.send_rich_message(
        ctx.client,
        event.chat_id,
        await start_body(ctx, telegram_id),
        title="UwU Suite",
        buttons=await start_buttons(ctx, telegram_id),
        owner_id=telegram_id,
    )
