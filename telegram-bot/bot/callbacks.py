"""Persistent inline buttons.

Telegram caps callback_data at 64 bytes, so the payload cannot live in the
button. Every button carries an opaque token instead, `cb:` plus 16 hex
characters, and the token is a primary key in the `callbacks` table. That is
what makes a button pressed a week after a redeploy still work.

Actions are registered under a stable string key, so renaming a Python function
never breaks a button that is already sitting in somebody's chat.
"""

from __future__ import annotations

import json
import logging
import secrets
from typing import Any, Awaitable, Callable

import aiosqlite

from .db import Database, iso, parse_iso, utcnow

log = logging.getLogger("uwu.callbacks")

PREFIX = "cb:"
TOKEN_BYTES = 8  # 16 hex characters

Handler = Callable[..., Awaitable[None]]

# Module level so a handler module can register its actions at import time,
# before any registry instance exists. There is one bot per process.
_ACTIONS: dict[str, Handler] = {}


def action(key: str) -> Callable[[Handler], Handler]:
    """Register a callback action under a stable string key.

    The key is what old buttons carry, so renaming the Python function is
    always safe and renaming the key never is.
    """

    def decorator(func: Handler) -> Handler:
        if key in _ACTIONS and _ACTIONS[key] is not func:
            raise ValueError(f"Callback action {key!r} is already registered")
        _ACTIONS[key] = func
        return func

    return decorator


class CallbackRegistry:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._actions = _ACTIONS

    def known_actions(self) -> list[str]:
        return sorted(self._actions)

    # --- token lifecycle ---------------------------------------------------

    async def register(
        self,
        action: str,
        payload: dict[str, Any] | None = None,
        *,
        owner_id: int | None = None,
        expires_at: str | None = None,
        max_uses: int | None = None,
        chat_id: int | None = None,
        message_id: int | None = None,
    ) -> str:
        """Write the row and return the callback_data string.

        Always called before the message is sent, so the row exists in SQLite
        before Telegram can deliver a press.
        """
        if action not in self._actions:
            raise ValueError(
                f"Callback action {action!r} has no handler. Register one before sending the button."
            )
        token = secrets.token_hex(TOKEN_BYTES)
        await self.db.execute(
            """
            insert into callbacks (id, action, payload, owner_id, chat_id, message_id,
                                   created_at, expires_at, use_count, max_uses)
            values (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
            """,
            (
                token,
                action,
                json.dumps(payload or {}, separators=(",", ":")),
                owner_id,
                chat_id,
                message_id,
                iso(),
                expires_at,
                max_uses,
            ),
        )
        return PREFIX + token

    async def bind_message(self, data_values: list[str], chat_id: int, message_id: int) -> None:
        """Attach freshly sent or edited message coordinates to the button rows."""
        tokens = [d[len(PREFIX):] for d in data_values if d.startswith(PREFIX)]
        if not tokens:
            return
        placeholders = ",".join("?" for _ in tokens)
        await self.db.execute(
            f"update callbacks set chat_id = ?, message_id = ? where id in ({placeholders})",
            (chat_id, message_id, *tokens),
        )

    async def release_message(self, chat_id: int, message_id: int) -> None:
        """Called before an edit, so buttons removed by the edit stop resolving."""
        await self.db.execute(
            "delete from callbacks where chat_id = ? and message_id = ?",
            (chat_id, message_id),
        )

    async def lookup(self, token: str) -> aiosqlite.Row | None:
        return await self.db.fetchone("select * from callbacks where id = ?", (token,))

    async def _consume(self, row: aiosqlite.Row) -> None:
        await self.db.execute(
            "update callbacks set use_count = use_count + 1 where id = ?", (row["id"],)
        )
        max_uses = row["max_uses"]
        if max_uses is not None and row["use_count"] + 1 >= max_uses:
            await self.db.execute("delete from callbacks where id = ?", (row["id"],))

    # --- dispatch ----------------------------------------------------------

    async def dispatch(self, event: Any, ctx: Any) -> bool:
        """Resolve a CallbackQuery to an action. Returns True when handled here."""
        raw = event.data or b""
        try:
            data = raw.decode("utf-8")
        except UnicodeDecodeError:
            await event.answer("That button is not one this chat understands.", alert=False)
            return True

        if not data.startswith(PREFIX):
            return False  # for example the mfa: buttons the portal sends

        token = data[len(PREFIX):]
        row = await self.lookup(token)
        if row is None:
            await event.answer(
                "That button has expired, please run the command again.", alert=True
            )
            return True

        expires_at = parse_iso(row["expires_at"])
        if expires_at is not None and expires_at < utcnow():
            await self.db.execute("delete from callbacks where id = ?", (token,))
            await event.answer(
                "That button has expired, please run the command again.", alert=True
            )
            return True

        owner_id = row["owner_id"]
        if owner_id is not None and event.sender_id != owner_id:
            await event.answer("That button belongs to somebody else.", alert=True)
            return True

        max_uses = row["max_uses"]
        if max_uses is not None and row["use_count"] >= max_uses:
            await self.db.execute("delete from callbacks where id = ?", (token,))
            await event.answer("That button has already been used.", alert=True)
            return True

        handler = self._actions.get(row["action"])
        if handler is None:
            log.error("No handler registered for callback action %s", row["action"])
            await event.answer("That button no longer does anything.", alert=True)
            return True

        try:
            payload = json.loads(row["payload"])
        except json.JSONDecodeError:
            payload = {}

        await self._consume(row)
        await handler(event, payload, ctx)
        return True

    # --- housekeeping ------------------------------------------------------

    async def gc(self) -> int:
        """Delete expired rows only. A button that is merely old still works."""
        cursor = await self.db.conn.execute(
            "delete from callbacks where expires_at is not null and expires_at < ?",
            (iso(),),
        )
        await self.db.conn.commit()
        return cursor.rowcount or 0
