"""Message composition, the one door every outgoing message goes through.

Direct calls to `client.send_message` are allowed in this module and nowhere
else. Everything the style rules in the specification ask for lives here, so a
reviewer checks one file rather than remembering a rule.

Style rules enforced or supported here:

1. No em dashes in user facing text. `sanitize` strips them and logs that it
   happened, and `tests/test_style.py` fails the build if one is committed.
2. The product is "the portal" or "UwU Suite". The bot is never named, and
   `tests/test_style.py` checks that too.
3. Long output is split on paragraph boundaries rather than truncated.
4. A message carrying a one time code is sensitive: never logged, never queued
   into the jobs table, because the digits live in the copy button markup as
   well as in the body.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from telethon import Button
from telethon.errors import FloodWaitError, MessageNotModifiedError
from telethon.tl import types

log = logging.getLogger("uwu.rich")

TELEGRAM_LIMIT = 4096
# Leaves room for the title and footer that get glued on around a body chunk
CHUNK_TARGET = 3800

# Written as escapes on purpose, so the character itself appears in no source file
_EM_DASH_RE = re.compile("\\s*[\u2014\u2015]\\s*")

# One feature check for the whole codebase. Telethon gained KeyboardButtonCopy
# in a recent layer, and a client that does not offer it still gets a working
# message, because the <code> block is already tap to copy everywhere.
HAS_COPY_BUTTON = hasattr(types, "KeyboardButtonCopy")

_registry: Any = None
_scheduler: Any = None


def configure(registry: Any, scheduler: Any = None) -> None:
    """Wire the callback registry and the scheduler in once, at startup."""
    global _registry, _scheduler
    _registry = registry
    _scheduler = scheduler


# --- text helpers ----------------------------------------------------------


def esc(value: Any) -> str:
    """Escape anything that came from the portal or from a user."""
    return html.escape(str(value if value is not None else ""), quote=False)


def sanitize(text: str) -> str:
    """Strip em dashes. They are not allowed in user facing text."""
    cleaned, count = _EM_DASH_RE.subn(", ", text)
    if count:
        # Deliberately does not log the text, which may carry a one time code.
        log.warning("Stripped %d em dash(es) from an outgoing message", count)
    return cleaned


def code_block(value: str) -> str:
    return f"<code>{esc(value)}</code>"


def bold(value: str) -> str:
    return f"<b>{esc(value)}</b>"


def humanize_seconds(seconds: float) -> str:
    seconds = int(max(0, seconds))
    if seconds < 60:
        return f"{seconds} second{'s' if seconds != 1 else ''}"
    minutes, rest = divmod(seconds, 60)
    if minutes < 60:
        if rest:
            return f"{minutes} minute{'s' if minutes != 1 else ''} and {rest} seconds"
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} hour{'s' if hours != 1 else ''} and {minutes} minutes"


# --- buttons ---------------------------------------------------------------


@dataclass(frozen=True)
class Btn:
    """A button in one of three shapes.

    url       opens a link, no round trip to the bot
    callback  carries an opaque token resolved through callbacks.py
    copy      puts a string on the clipboard, sends nothing back, has no token
              and therefore no row to expire
    """

    text: str
    kind: str  # 'url' | 'callback' | 'copy'
    target: str | None = None          # url
    action: str | None = None          # callback dispatch key
    payload: dict[str, Any] | None = None
    value: str | None = None           # copy
    owner_id: int | None = None
    expires_at: str | None = None
    max_uses: int | None = None

    @classmethod
    def link(cls, text: str, target: str) -> "Btn":
        return cls(text=text, kind="url", target=target)

    @classmethod
    def callback(
        cls,
        text: str,
        action: str,
        payload: dict[str, Any] | None = None,
        *,
        owner_id: int | None = None,
        expires_at: str | None = None,
        max_uses: int | None = None,
    ) -> "Btn":
        return cls(
            text=text,
            kind="callback",
            action=action,
            payload=payload or {},
            owner_id=owner_id,
            expires_at=expires_at,
            max_uses=max_uses,
        )

    @classmethod
    def copy(cls, text: str, value: str) -> "Btn":
        return cls(text=text, kind="copy", value=value)


Rows = Sequence[Sequence[Btn]]


def _has_copy(buttons: Rows | None) -> bool:
    return bool(buttons) and any(b.kind == "copy" for row in buttons for b in row)


async def _build_markup(
    buttons: Rows,
    default_owner: int | None,
) -> tuple[list[list[Any]], list[str]]:
    """Register callback rows, then build the Telethon button objects.

    The registry write happens before the send, so the token exists in SQLite
    before Telegram can possibly deliver a press.
    """
    rows: list[list[Any]] = []
    data_values: list[str] = []

    for row in buttons:
        built: list[Any] = []
        for btn in row:
            if btn.kind == "url":
                built.append(Button.url(btn.text, btn.target or ""))
            elif btn.kind == "copy":
                if HAS_COPY_BUTTON:
                    built.append(
                        types.KeyboardButtonCopy(text=btn.text, copy_text=btn.value or "")
                    )
                # Otherwise the button is simply omitted, per the feature check.
            elif btn.kind == "callback":
                if _registry is None:
                    raise RuntimeError("rich.configure has not been called")
                data = await _registry.register(
                    btn.action or "",
                    btn.payload,
                    owner_id=btn.owner_id if btn.owner_id is not None else default_owner,
                    expires_at=btn.expires_at,
                    max_uses=btn.max_uses,
                )
                data_values.append(data)
                built.append(Button.inline(btn.text, data=data.encode()))
            else:
                raise ValueError(f"Unknown button kind {btn.kind!r}")
        if built:
            rows.append(built)

    return rows, data_values


def _serialize_buttons(rows: list[list[Any]]) -> list[list[dict[str, str]]]:
    """For the deferred send job. Only ever called on a non sensitive message."""
    out: list[list[dict[str, str]]] = []
    for row in rows:
        serialized: list[dict[str, str]] = []
        for button in row:
            if isinstance(button, types.KeyboardButtonUrl):
                serialized.append({"kind": "url", "text": button.text, "target": button.url})
            elif isinstance(button, types.KeyboardButtonCallback):
                serialized.append(
                    {"kind": "data", "text": button.text, "data": button.data.decode()}
                )
        if serialized:
            out.append(serialized)
    return out


def deserialize_buttons(rows: Iterable[Iterable[dict[str, str]]]) -> list[list[Any]]:
    out: list[list[Any]] = []
    for row in rows:
        built = [
            Button.url(b["text"], b["target"])
            if b.get("kind") == "url"
            else Button.inline(b["text"], data=b["data"].encode())
            for b in row
        ]
        if built:
            out.append(built)
    return out


# --- chunking --------------------------------------------------------------


def split_body(text: str, limit: int = CHUNK_TARGET) -> list[str]:
    """Split on paragraph boundaries, then lines, then hard, never silently."""
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""
    for paragraph in text.split("\n\n"):
        candidate = paragraph if not current else f"{current}\n\n{paragraph}"
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        while len(paragraph) > limit:
            cut = paragraph.rfind("\n", 0, limit)
            if cut <= 0:
                cut = limit
            chunks.append(paragraph[:cut].rstrip())
            paragraph = paragraph[cut:].lstrip("\n")
        current = paragraph
    if current:
        chunks.append(current)
    return chunks


def compose(body: str, title: str | None = None, footer: str | None = None) -> list[str]:
    """Title, body, footer, joined by blank lines and split to fit."""
    body = sanitize(body).strip()
    parts = split_body(body)

    out: list[str] = []
    for index, part in enumerate(parts):
        sections: list[str] = []
        if title and index == 0:
            sections.append(f"<b>{sanitize(title).strip()}</b>")
        sections.append(part)
        if footer and index == len(parts) - 1:
            sections.append(sanitize(footer).strip())
        out.append("\n\n".join(s for s in sections if s))
    return out


# --- the send --------------------------------------------------------------


async def send_rich_message(
    client: Any,
    entity: Any,
    body: str,
    *,
    title: str | None = None,
    footer: str | None = None,
    buttons: Rows | None = None,
    reply_to: int | None = None,
    link_preview: bool = False,
    edit: Any = None,
    owner_id: int | None = None,
    sensitive: bool = False,
) -> Any:
    """Send or edit one rich message and return the Message that carries the buttons.

    `sensitive` marks a message that must never be written to the jobs table or
    to a log. A copy button forces it on, because the string to copy lives in
    the button markup and is as sensitive as the body.
    """
    sensitive = sensitive or _has_copy(buttons)
    chunks = compose(body, title=title, footer=footer)

    markup: list[list[Any]] | None = None
    data_values: list[str] = []
    if buttons:
        markup, data_values = await _build_markup(buttons, owner_id)
        if not markup:
            markup = None

    if edit is not None:
        return await _edit(
            client, edit, chunks, markup, data_values, link_preview, sensitive
        )

    message = None
    for index, chunk in enumerate(chunks):
        is_last = index == len(chunks) - 1
        message = await _deliver(
            client,
            entity,
            chunk,
            buttons=markup if is_last else None,
            reply_to=reply_to if index == 0 else None,
            link_preview=link_preview,
            sensitive=sensitive,
        )

    if message is not None and data_values and _registry is not None:
        await _registry.bind_message(data_values, message.chat_id, message.id)
    return message


async def _edit(
    client: Any,
    target: Any,
    chunks: list[str],
    markup: list[list[Any]] | None,
    data_values: list[str],
    link_preview: bool,
    sensitive: bool,
) -> Any:
    """Edit in place and rebind the callback rows to the same message.

    The old rows for this message are dropped first, so a button the edit
    removed stops resolving instead of lingering as a live token. That is what
    makes a superseded code message safe: the copy button goes with it.
    """
    chat_id = target.chat_id
    message_id = target.id
    if _registry is not None:
        await _registry.release_message(chat_id, message_id)
        if data_values:
            await _registry.bind_message(data_values, chat_id, message_id)

    try:
        edited = await client.edit_message(
            chat_id,
            message_id,
            chunks[0],
            parse_mode="html",
            buttons=markup,
            link_preview=link_preview,
        )
    except MessageNotModifiedError:
        edited = target
    except FloodWaitError as exc:
        await _handle_flood(exc, sensitive)
        edited = await client.edit_message(
            chat_id,
            message_id,
            chunks[0],
            parse_mode="html",
            buttons=markup,
            link_preview=link_preview,
        )

    # An edit that grew past the limit spills into follow up messages
    for chunk in chunks[1:]:
        await _deliver(
            client, chat_id, chunk, buttons=None, reply_to=None,
            link_preview=link_preview, sensitive=sensitive,
        )
    return edited


async def _deliver(
    client: Any,
    entity: Any,
    text: str,
    *,
    buttons: list[list[Any]] | None,
    reply_to: int | None,
    link_preview: bool,
    sensitive: bool,
) -> Any:
    try:
        return await client.send_message(
            entity,
            text,
            parse_mode="html",
            buttons=buttons,
            reply_to=reply_to,
            link_preview=link_preview,
        )
    except FloodWaitError as exc:
        rescheduled = await _handle_flood(exc, sensitive, entity, text, buttons, link_preview)
        if rescheduled:
            return None
        return await client.send_message(
            entity,
            text,
            parse_mode="html",
            buttons=buttons,
            reply_to=reply_to,
            link_preview=link_preview,
        )


async def _handle_flood(
    exc: FloodWaitError,
    sensitive: bool,
    entity: Any = None,
    text: str | None = None,
    buttons: list[list[Any]] | None = None,
    link_preview: bool = False,
) -> bool:
    """Sleep a short wait, reschedule a long one. Returns True when rescheduled."""
    threshold = 60
    if _scheduler is not None:
        threshold = getattr(_scheduler, "flood_sleep_threshold", 60)

    if exc.seconds <= threshold:
        log.warning("Flood wait of %ss, sleeping", exc.seconds)
        await asyncio.sleep(exc.seconds + 1)
        return False

    if sensitive or text is None or _scheduler is None or not isinstance(entity, int):
        # A message carrying a credential is never written to the jobs table.
        log.error(
            "Flood wait of %ss is too long to sleep and the message cannot be queued, dropping it",
            exc.seconds,
        )
        return True

    log.warning("Flood wait of %ss, rescheduling the send", exc.seconds)
    await _scheduler.schedule(
        "message.resend",
        {
            "chat_id": entity,
            "text": text,
            "buttons": _serialize_buttons(buttons or []),
            "link_preview": link_preview,
        },
        delay_seconds=exc.seconds + 5,
    )
    return True


# --- shared failure copy ---------------------------------------------------

PORTAL_DOWN = (
    "The portal is not responding right now, please try again in a minute."
)
STALE_DATA = "This may be slightly out of date, the portal did not answer just now."
GENERIC_ERROR = (
    "Something went wrong on my side, sorry. Please try again in a moment."
)


def incident_note(incident_id: str) -> str:
    return f"{GENERIC_ERROR}\n\nReference: {esc(incident_id)}"


def dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
