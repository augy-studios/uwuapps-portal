"""Message composition, the one door every outgoing message goes through.

Direct calls to `client.send_message` are allowed in this module and in
`reply.py`, and nowhere else. Everything the style rules in the specification
ask for lives here, so a reviewer checks one file rather than remembering a rule.

Two kinds of outgoing message:

* A plain notice, passed as a `str`. One or two sentences, no heading, sent as
  ordinary text. "Nothing changed." and the portal is down line are these.
* A rich message, passed as the dict `message()` returns, with a `markdown`
  half in Telegram's Rich Markdown dialect (headings, bold, italic, bullet
  lists, pipe tables) and a plain `fallback` half that says the same thing.
  The fallback is what an old client shows, and what goes out if Telegram
  rejects the rich payload. `reply.py` puts both on the wire.

Style rules enforced or supported here:

1. No em dashes in user facing text. `sanitize` strips them and logs that it
   happened, and `tests/test_style.py` fails the build if one is committed.
2. The product is "the portal" or "UwU Suite". The bot is never named, and
   `tests/test_style.py` checks that too.
3. Long output is split on paragraph boundaries rather than truncated. A rich
   message that would not fit goes out as its plain fallback, in pieces.
4. A message carrying a one time code is sensitive: never logged, never queued
   into the jobs table, because the digits live in the copy button markup as
   well as in the body.
5. Every piece of dynamic data goes through `escape_md`, and every table cell
   through `escape_cell`. Markup authored here is never escaped.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from telethon import Button, utils
from telethon.errors import FloodWaitError, MessageNotModifiedError
from telethon.tl import types

from . import reply

log = logging.getLogger("uwu.rich")

TELEGRAM_LIMIT = 4096
# Leaves room for the title and footer that get glued on around a body chunk
CHUNK_TARGET = 3800

# Written as escapes on purpose, so the character itself appears in no source file
_EM_DASH_RE = re.compile("\\s*[\u2014\u2015]\\s*")

# One feature check for the whole codebase. Telethon gained KeyboardButtonCopy
# in a recent layer, and a client that does not offer it still gets a working
# message, because the code span is already tap to copy everywhere.
HAS_COPY_BUTTON = hasattr(types, "KeyboardButtonCopy")

_registry: Any = None
_scheduler: Any = None


def configure(registry: Any, scheduler: Any = None) -> None:
    """Wire the callback registry and the scheduler in once, at startup."""
    global _registry, _scheduler
    _registry = registry
    _scheduler = scheduler


# --- escaping --------------------------------------------------------------

_MD_SPECIAL = re.compile(r"([\\*_~`|\[\]#>=])")


def escape_md(text: Any) -> str:
    """Escape user/data text for Telegram's Rich Markdown dialect."""
    return _MD_SPECIAL.sub(r"\\\1", str(text if text is not None else ""))


def escape_cell(text: Any) -> str:
    """Escape for a GFM table cell; also flattens newlines so the row stays intact."""
    return escape_md(str(text if text is not None else "").replace("\n", " "))


def bold(value: Any) -> str:
    return f"**{escape_md(value)}**"


def italic(value: Any) -> str:
    return f"*{escape_md(value)}*"


def code(value: Any) -> str:
    """An inline code span. Backslash escapes do not work inside one, so a
    backtick in the value is replaced rather than escaped."""
    return "`" + str(value if value is not None else "").replace("`", "'") + "`"


def lines(*parts: str) -> str:
    """Join lines inside one block with hard breaks, so they stay on their own
    lines whether or not the renderer treats a bare newline as one."""
    return "  \n".join(p for p in parts if p)


def table(headers: Sequence[str], rows: Iterable[Sequence[Any]], *, corner: str = "") -> str:
    """A pipe table. The first column is a row label under a blank header,
    which `corner` can name. Every cell is escaped and flattened here."""
    out = [
        "| " + " | ".join([escape_cell(corner), *(escape_cell(h) for h in headers)]) + " |",
        "| " + " | ".join(["---"] * (len(headers) + 1)) + " |",
    ]
    for row in rows:
        out.append("| " + " | ".join(escape_cell(v) for v in row) + " |")
    return "\n".join(out)


# --- the plain half --------------------------------------------------------

_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_CELL_SPLIT = re.compile(r"(?<!\\)\|")
_SEPARATOR = re.compile(r"^:?-{3,}:?$")
_HEADING = re.compile(r"^#{1,6}\s+")
_QUOTE = re.compile(r"^>\s?")


def _unmark(text: str) -> str:
    """Drop the inline markup this module authors and undo `escape_md`."""
    out: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            out.append(text[i + 1])
            i += 2
            continue
        if ch not in "*_`~":
            out.append(ch)
        i += 1
    return "".join(out)


def _table_to_lines(block: list[str]) -> list[str]:
    """One line per data row: the label, then the values. Headers are dropped,
    the label already says what the row is."""
    rows: list[list[str]] = []
    for line in block:
        cells = [c.strip() for c in _CELL_SPLIT.split(line.strip().strip("|"))]
        if cells and all(_SEPARATOR.match(c) for c in cells if c) and any(cells):
            continue
        rows.append([_unmark(c) for c in cells])
    out: list[str] = []
    for cells in rows[1:]:
        label = cells[0] if cells else ""
        values = [v for v in cells[1:] if v]
        out.append(f"{label}: {', '.join(values)}" if values else label)
    return out


def plain(markdown: str) -> str:
    """The plain text reading of markdown written in this module's dialect.

    Exact rather than approximate, because the only markup in the input is the
    markup authored here, and every piece of data went through `escape_md`.
    """
    out: list[str] = []
    block: list[str] = []

    def flush() -> None:
        if block:
            out.extend(_table_to_lines(block))
            block.clear()

    for raw in markdown.splitlines():
        line = raw.rstrip()
        if _TABLE_ROW.match(line):
            block.append(line)
            continue
        flush()
        line = _HEADING.sub("", line)
        line = _QUOTE.sub("", line)
        out.append(_unmark(line))
    flush()
    return "\n".join(out).strip()


# --- text helpers ----------------------------------------------------------


def sanitize(text: str) -> str:
    """Strip em dashes. They are not allowed in user facing text."""
    cleaned, count = _EM_DASH_RE.subn(", ", text)
    if count:
        # Deliberately does not log the text, which may carry a one time code.
        log.warning("Stripped %d em dash(es) from an outgoing message", count)
    return cleaned


def message(
    body: str,
    *,
    title: str | None = None,
    footer: str | None = None,
    fallback: str | None = None,
) -> dict[str, str]:
    """The `{"markdown", "fallback"}` pair for one view.

    `title` becomes the heading. `body`, `footer` and `title` are markdown.
    The fallback is read off the markdown unless a builder hands one in, and
    it is never empty, because the request's `message=` field is required.
    """
    parts = [f"# {title.strip()}" if title else "", body.strip(), (footer or "").strip()]
    markdown = sanitize("\n\n".join(p for p in parts if p)).strip()
    text = sanitize(fallback).strip() if fallback is not None else plain(markdown)
    return {"markdown": markdown, "fallback": text or markdown}


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


def _prepare(body: str | dict[str, str]) -> tuple[dict[str, str] | None, list[str]]:
    """Either one rich message, or the plain chunks to send instead."""
    if isinstance(body, dict):
        if len(body["markdown"]) <= TELEGRAM_LIMIT and len(body["fallback"]) <= TELEGRAM_LIMIT:
            return body, []
        log.warning("A rich message is too long for one message, sending its plain text in pieces")
        return None, split_body(body["fallback"])
    return None, split_body(sanitize(body).strip())


# --- the send --------------------------------------------------------------


@dataclass(frozen=True)
class Sent:
    """Where a message landed. Enough to bind buttons to it and to edit it later."""

    chat_id: int
    id: int


def _as_sent(result: Any, entity: Any) -> Sent | None:
    if result is None:
        return None
    message_id = reply.sent_message_id(result)
    if message_id is None:
        message_id = getattr(result, "id", None)
    if message_id is None:
        return None
    chat_id = getattr(result, "chat_id", None)
    if chat_id is None:
        for update in getattr(result, "updates", []):
            peer = getattr(getattr(update, "message", None), "peer_id", None)
            if peer is not None:
                chat_id = utils.get_peer_id(peer)
                break
    if chat_id is None and isinstance(entity, int):
        chat_id = entity
    if chat_id is None:
        return None
    return Sent(chat_id=int(chat_id), id=int(message_id))


async def send_rich_message(
    client: Any,
    entity: Any,
    body: str | dict[str, str],
    *,
    buttons: Rows | None = None,
    reply_to: int | None = None,
    link_preview: bool = False,
    edit: Any = None,
    owner_id: int | None = None,
    sensitive: bool = False,
) -> Sent | None:
    """Send or edit one message and return where it landed.

    `body` is a plain notice when it is a string, and a rich message when it
    is the dict `message()` returns. Everything else about the send is the
    same either way: the buttons are registered first, a flood wait is slept
    or rescheduled, and the message the buttons ended up on is recorded.

    `sensitive` marks a message that must never be written to the jobs table or
    to a log. A copy button forces it on, because the string to copy lives in
    the button markup and is as sensitive as the body.
    """
    sensitive = sensitive or _has_copy(buttons)
    rich, chunks = _prepare(body)

    markup: list[list[Any]] | None = None
    data_values: list[str] = []
    if buttons:
        markup, data_values = await _build_markup(buttons, owner_id)
        if not markup:
            markup = None

    if edit is not None:
        return await _edit(
            client, edit, rich, chunks, markup, data_values, link_preview, sensitive
        )

    sent: Sent | None = None
    if rich is not None:
        sent = await _deliver_rich(
            client, entity, rich, markup, reply_to=reply_to,
            link_preview=link_preview, sensitive=sensitive,
        )
    else:
        for index, chunk in enumerate(chunks):
            is_last = index == len(chunks) - 1
            sent = await _deliver(
                client,
                entity,
                chunk,
                buttons=markup if is_last else None,
                reply_to=reply_to if index == 0 else None,
                link_preview=link_preview,
                sensitive=sensitive,
            )

    if sent is not None and data_values and _registry is not None:
        await _registry.bind_message(data_values, sent.chat_id, sent.id)
    return sent


async def _edit(
    client: Any,
    target: Any,
    rich: dict[str, str] | None,
    chunks: list[str],
    markup: list[list[Any]] | None,
    data_values: list[str],
    link_preview: bool,
    sensitive: bool,
) -> Sent:
    """Edit in place and rebind the callback rows to the same message.

    The old rows for this message are dropped first, so a button the edit
    removed stops resolving instead of lingering as a live token. That is what
    makes a superseded code message safe: the copy button goes with it.

    An edit with no buttons removes the keyboard, on both paths.
    """
    chat_id = target.chat_id
    message_id = target.id
    if _registry is not None:
        await _registry.release_message(chat_id, message_id)
        if data_values:
            await _registry.bind_message(data_values, chat_id, message_id)

    if rich is not None:
        try:
            await reply.edit_rich_message_at(client, chat_id, message_id, rich, markup)
        except FloodWaitError as exc:
            if not await _handle_flood(exc, sensitive):
                await reply.edit_rich_message_at(client, chat_id, message_id, rich, markup)
        return Sent(chat_id=chat_id, id=message_id)

    buttons = markup if markup else reply._NO_BUTTONS
    try:
        await client.edit_message(
            chat_id, message_id, chunks[0], buttons=buttons, link_preview=link_preview
        )
    except MessageNotModifiedError:
        pass
    except FloodWaitError as exc:
        if not await _handle_flood(exc, sensitive):
            await client.edit_message(
                chat_id, message_id, chunks[0], buttons=buttons, link_preview=link_preview
            )

    # An edit that grew past the limit spills into follow up messages
    for chunk in chunks[1:]:
        await _deliver(
            client, chat_id, chunk, buttons=None, reply_to=None,
            link_preview=link_preview, sensitive=sensitive,
        )
    return Sent(chat_id=chat_id, id=message_id)


async def _deliver_rich(
    client: Any,
    entity: Any,
    rich: dict[str, str],
    buttons: list[list[Any]] | None,
    *,
    reply_to: int | None,
    link_preview: bool,
    sensitive: bool,
) -> Sent | None:
    try:
        result = await reply.send_rich_message(
            client, entity, rich, buttons, reply_to=reply_to, link_preview=link_preview
        )
    except FloodWaitError as exc:
        rescheduled = await _handle_flood(
            exc, sensitive, entity, rich=rich, buttons=buttons, link_preview=link_preview
        )
        if rescheduled:
            return None
        result = await reply.send_rich_message(
            client, entity, rich, buttons, reply_to=reply_to, link_preview=link_preview
        )
    return _as_sent(result, entity)


async def _deliver(
    client: Any,
    entity: Any,
    text: str,
    *,
    buttons: list[list[Any]] | None,
    reply_to: int | None,
    link_preview: bool,
    sensitive: bool,
) -> Sent | None:
    """A plain notice. The client's parse mode is off, see bot.py, so the text
    goes out exactly as written."""
    try:
        result = await client.send_message(
            entity, text, buttons=buttons, reply_to=reply_to, link_preview=link_preview
        )
    except FloodWaitError as exc:
        rescheduled = await _handle_flood(
            exc, sensitive, entity, text=text, buttons=buttons, link_preview=link_preview
        )
        if rescheduled:
            return None
        result = await client.send_message(
            entity, text, buttons=buttons, reply_to=reply_to, link_preview=link_preview
        )
    return _as_sent(result, entity)


async def _handle_flood(
    exc: FloodWaitError,
    sensitive: bool,
    entity: Any = None,
    *,
    text: str | None = None,
    rich: dict[str, str] | None = None,
    buttons: list[list[Any]] | None = None,
    link_preview: bool = False,
) -> bool:
    """Sleep a short wait, reschedule a long one. Returns True when the send
    will not happen now, because it was rescheduled or had to be dropped."""
    threshold = 60
    if _scheduler is not None:
        threshold = getattr(_scheduler, "flood_sleep_threshold", 60)

    if exc.seconds <= threshold:
        log.warning("Flood wait of %ss, sleeping", exc.seconds)
        await asyncio.sleep(exc.seconds + 1)
        return False

    nothing_to_queue = text is None and rich is None
    if sensitive or nothing_to_queue or _scheduler is None or not isinstance(entity, int):
        # A message carrying a credential is never written to the jobs table.
        log.error(
            "Flood wait of %ss is too long to sleep and the message cannot be queued, dropping it",
            exc.seconds,
        )
        return True

    log.warning("Flood wait of %ss, rescheduling the send", exc.seconds)
    payload: dict[str, Any] = {
        "chat_id": entity,
        "buttons": _serialize_buttons(buttons or []),
        "link_preview": link_preview,
    }
    if rich is not None:
        payload["markdown"] = rich["markdown"]
        payload["fallback"] = rich["fallback"]
    else:
        payload["text"] = text
    await _scheduler.schedule("message.resend", payload, delay_seconds=exc.seconds + 5)
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
    return f"{GENERIC_ERROR}\n\nReference: {incident_id}"


def dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
