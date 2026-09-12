"""The wire: raw TL requests that carry a Telegram Rich Message.

Telethon's high level `send_message` and `edit_message` do not expose the
`rich_message` field, so structured content (headings, tables, sections) goes
out through the raw requests here. The `message=` field on every request is
the plain fallback, which is what an old client shows and what gets sent when
the rich payload is rejected. No parse_mode anywhere in this module.

Only `rich.py` calls these. Handlers go through `rich.send_rich_message`,
which adds the button registry, the size guard and the flood handling.
"""

from __future__ import annotations

import logging
from typing import Any

from telethon import types
from telethon.errors import FloodWaitError, MessageNotModifiedError
from telethon.tl import functions

log = logging.getLogger("uwu.reply")


def _rich_markdown(rich: dict[str, str]) -> types.InputRichMessageMarkdown:
    return types.InputRichMessageMarkdown(markdown=rich["markdown"])


# Editing without reply_markup keeps the old keyboard; an empty inline
# keyboard is what actually removes it.
_NO_BUTTONS = types.ReplyInlineMarkup(rows=[])


def _reply_to(message_id: int | None) -> types.InputReplyToMessage | None:
    return types.InputReplyToMessage(reply_to_msg_id=message_id) if message_id else None


def sent_message_id(result: Any) -> int | None:
    """Id of the message a raw send created (a bot's sends come back as Updates)."""
    if isinstance(result, (types.Message, types.UpdateShortSentMessage)):
        return result.id
    for update in getattr(result, "updates", []):
        if isinstance(update, types.UpdateMessageID):
            return update.id
        if isinstance(update, (types.UpdateNewMessage, types.UpdateNewChannelMessage)):
            return update.message.id
    return None


async def send_rich_message(
    client: Any,
    entity: Any,
    rich: dict[str, str],
    buttons: Any = None,
    *,
    reply_to: int | None = None,
    link_preview: bool = False,
) -> Any:
    markup = client.build_reply_markup(buttons) if buttons else None
    try:
        return await client(functions.messages.SendMessageRequest(
            peer=entity, message=rich["fallback"],
            rich_message=_rich_markdown(rich), reply_markup=markup,
            reply_to=_reply_to(reply_to), no_webpage=not link_preview))
    except FloodWaitError:
        # Not a rejected payload. The caller sleeps or reschedules, and a plain
        # retry here would only run into the same wait.
        raise
    except Exception as err:
        log.warning("[send_rich_message] rich send failed, falling back: %s", err)
        return await client.send_message(
            entity, rich["fallback"], buttons=buttons,
            reply_to=reply_to, link_preview=link_preview,
        )


async def edit_rich_message_at(
    client: Any, peer: Any, msg_id: int, rich: dict[str, str], buttons: Any = None
) -> None:
    """Edit by chat + message id. No buttons => keyboard removed."""
    markup = client.build_reply_markup(buttons) if buttons else _NO_BUTTONS
    try:
        await client(functions.messages.EditMessageRequest(
            peer=peer, id=msg_id, message=rich["fallback"],
            rich_message=_rich_markdown(rich), reply_markup=markup))
    except MessageNotModifiedError:
        return
    except FloodWaitError:
        raise
    except Exception as err:
        log.warning("[edit_rich_message_at] rich edit failed, falling back: %s", err)
        # The built markup rather than `buttons`, so an edit meant to remove
        # the keyboard still removes it on the plain path.
        await client.edit_message(peer, msg_id, text=rich["fallback"], buttons=markup)


async def edit_rich_message(
    client: Any, event: Any, rich: dict[str, str], buttons: Any = None
) -> None:
    """Edit the message a CallbackQuery came from, regular chat or inline mode."""
    markup = client.build_reply_markup(buttons) if buttons else None
    is_inline = isinstance(event.query, types.UpdateInlineBotCallbackQuery)
    try:
        if is_inline:
            await client(functions.messages.EditInlineBotMessageRequest(
                id=event.query.msg_id, message=rich["fallback"],
                rich_message=_rich_markdown(rich), reply_markup=markup))
        else:
            await client(functions.messages.EditMessageRequest(
                peer=event.query.peer, id=event.query.msg_id, message=rich["fallback"],
                rich_message=_rich_markdown(rich), reply_markup=markup))
    except MessageNotModifiedError:
        return
    except FloodWaitError:
        raise
    except Exception as err:
        log.warning("[edit_rich_message] rich edit failed, falling back: %s", err)
        if is_inline:
            await client.edit_message(event.query.msg_id, text=rich["fallback"], buttons=buttons)
        else:
            await client.edit_message(event.query.peer, event.query.msg_id,
                                      text=rich["fallback"], buttons=buttons)
