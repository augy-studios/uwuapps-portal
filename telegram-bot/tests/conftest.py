"""Test fixtures.

Everything here runs without a Telegram connection and without the portal. The
client and the portal are stood in for, which is what lets the tests assert on
what would have been sent.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# config.py validates at import time, so the required keys have to exist before
# anything under bot/ is imported.
os.environ.setdefault("TELEGRAM_API_ID", "1234567")
os.environ.setdefault("TELEGRAM_API_HASH", "0" * 32)
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "1234567890:TEST")
os.environ.setdefault("PORTAL_BASE_URL", "https://portal.test")
os.environ.setdefault("PORTAL_WEB_APP_URL", "https://portal.test")
os.environ.setdefault("DONATION_URL", "https://donate.test/abc")
os.environ.setdefault("TELEGRAM_BOT_SHARED_SECRET", "a" * 64)
os.environ.setdefault("ADMIN_TELEGRAM_IDS", "999")

from bot import callbacks as callbacks_module  # noqa: E402
from bot import config as config_module  # noqa: E402
from bot import rich  # noqa: E402
from bot.context import Ctx  # noqa: E402
from bot.db import Database  # noqa: E402
from bot.scheduler import Scheduler  # noqa: E402
from bot.services.cache import Cache  # noqa: E402


class SentMessage:
    """Stands in for a Telethon Message well enough for the code under test."""

    _next_id = 1000

    def __init__(self, chat_id: int, text: str, buttons: Any, **kwargs: Any) -> None:
        SentMessage._next_id += 1
        self.id = SentMessage._next_id
        self.chat_id = chat_id
        self.text = text
        self.buttons = buttons
        self.kwargs = kwargs


class FakeClient:
    def __init__(self) -> None:
        self.sent: list[SentMessage] = []
        self.edits: list[tuple[int, int, str, Any]] = []
        self.deleted: list[tuple[int, list[int]]] = []

    async def send_message(self, entity, text, buttons=None, **kwargs):
        message = SentMessage(int(entity), text, buttons, **kwargs)
        self.sent.append(message)
        return message

    async def edit_message(self, chat_id, message_id, text, buttons=None, **kwargs):
        self.edits.append((int(chat_id), int(message_id), text, buttons))
        message = SentMessage(int(chat_id), text, buttons, **kwargs)
        message.id = int(message_id)
        return message

    async def delete_messages(self, chat_id, ids):
        self.deleted.append((int(chat_id), [int(i) for i in ids]))


class FakeSender:
    def __init__(self, telegram_id: int, username: str | None = "tester") -> None:
        self.id = telegram_id
        self.username = username
        self.first_name = "Test"
        self.lang_code = "en"
        self.bot = False


class FakeMessage:
    def __init__(self, text: str, message_id: int = 1) -> None:
        self.text = text
        self.id = message_id


class FakeEvent:
    """A NewMessage event, reduced to what the handlers actually touch."""

    def __init__(self, text: str, sender_id: int = 42, chat_id: int | None = None) -> None:
        self.message = FakeMessage(text)
        self.sender_id = sender_id
        self.chat_id = chat_id if chat_id is not None else sender_id
        self.is_private = True
        self._sender = FakeSender(sender_id)

    async def get_sender(self):
        return self._sender


class FakeCallbackEvent:
    """A CallbackQuery event. Deliberately has no `.message` attribute."""

    def __init__(self, data: str, sender_id: int = 42, chat_id: int | None = None,
                 message: Any = None) -> None:
        self.data = data.encode()
        self.sender_id = sender_id
        self.chat_id = chat_id if chat_id is not None else sender_id
        self.is_private = True
        self.answers: list[tuple[str | None, bool]] = []
        self._message = message

    async def answer(self, text: str | None = None, alert: bool = False):
        self.answers.append((text, alert))

    async def get_message(self):
        return self._message

    async def get_sender(self):
        return FakeSender(self.sender_id)


class FakePortal:
    """Answers the way the portal would, or raises the way it would."""

    def __init__(self) -> None:
        self.apps: list[dict[str, Any]] = []
        self.last_success_at = None
        self.raises: Exception | None = None
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def list_apps(self):
        self.calls.append(("list_apps", ()))
        if self.raises:
            raise self.raises
        return list(self.apps)

    async def lookup_link(self, telegram_id):
        self.calls.append(("lookup_link", (telegram_id,)))
        if self.raises:
            raise self.raises
        return None

    async def redeem_link_code(self, code, telegram_id, username):
        self.calls.append(("redeem_link_code", (code, telegram_id, username)))
        if self.raises:
            raise self.raises
        from bot.services.portal import LinkedAccount

        return LinkedAccount(
            portal_user_id="user-1",
            username="tester",
            display_name="Tester",
            is_admin=False,
            is_editor=True,
            is_approved=True,
            linked_at="2026-01-01T00:00:00+00:00",
            mfa_enabled=False,
        )

    async def mfa_issue_code(self, telegram_id):
        self.calls.append(("mfa_issue_code", (telegram_id,)))
        if self.raises:
            raise self.raises
        from bot.services.portal import IssuedCode

        return IssuedCode(
            code="123456",
            expires_at="2026-01-01T00:05:00+00:00",
            seconds_remaining=300,
            superseded_pushed_code=False,
        )

    async def mfa_resolve(self, challenge_id, telegram_id, decision):
        self.calls.append(("mfa_resolve", (challenge_id, telegram_id, decision)))
        if self.raises:
            raise self.raises
        return {"status": "approved" if decision == "approve" else "denied"}


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "test.db")
    await database.connect()
    yield database
    await database.close()


@pytest.fixture
async def ctx(db, tmp_path):
    config = config_module.load()
    object.__setattr__(config, "db_path", db.path)
    client = FakeClient()
    registry = callbacks_module.CallbackRegistry(db)
    scheduler = Scheduler(db, tick_seconds=1)
    rich.configure(registry, scheduler)

    context = Ctx(
        client=client,
        db=db,
        config=config,
        portal=FakePortal(),
        cache=Cache(db),
        callbacks=registry,
        scheduler=scheduler,
        started_at="2026-01-01T00:00:00+00:00",
    )
    scheduler.ctx = context

    # Import the handler modules so their commands and callback actions register
    from bot.handlers import admin, apps, fallback, link, mfa, misc, start  # noqa: F401

    return context
