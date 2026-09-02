"""Unknown commands, and free text routed into the directory search."""

from __future__ import annotations

import logging
from typing import Any

from .. import rich
from ..context import Ctx
from . import reply_id

log = logging.getLogger("uwu.handlers.fallback")


async def unknown_command(event: Any, ctx: Ctx) -> None:
    """Short, and points at the one place the command list lives."""
    await rich.send_rich_message(
        ctx.client,
        event.chat_id,
        "I do not know that one. Run /start to see everything this chat can do.",
        reply_to=reply_id(event),
        owner_id=event.sender_id,
    )


async def free_text(event: Any, text: str, ctx: Ctx) -> None:
    """Anything that is not a command searches the directory.

    Group chats never reach here: privacy mode means ordinary group messages
    are not delivered at all, and the dispatcher drops the rest, so searching
    on every message is a private chat behaviour only.
    """
    if not event.is_private:
        return
    from . import apps

    await apps.search(event, text, ctx)
