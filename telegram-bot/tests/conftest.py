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
from telethon.client.buttons import ButtonMethods
from telethon.tl import functions, types

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
    """Stands in for a Telethon Message well enough for the code under test.

    `text` is what an old client would show: the plain text of a notice, or
    the fallback half of a rich message. `markdown` is the rich half, and is
    None for a plain notice.
    """

    _next_id = 1000

    def __init__(
        self, chat_id: int, text: str, buttons: Any, markdown: str | None = None, **kwargs: Any
    ) -> None:
        SentMessage._next_id += 1
        self.id = SentMessage._next_id
        self.chat_id = chat_id
        self.text = text
        self.markdown = markdown
        self.buttons = buttons
        self.kwargs = kwargs


def _rows(markup: Any) -> Any:
    """The button rows inside a reply markup, or None for no keyboard at all.

    An empty inline keyboard is how a keyboard is removed, and to the tests
    that reads the same as no buttons.
    """
    rows = getattr(markup, "rows", None)
    if not rows:
        return None
    return [list(row.buttons) for row in rows]


class FakeClient:
    """The two high level calls plain notices use, plus the raw request path
    rich messages take. Both land in `sent` and `edits`, so a test reads one
    list whichever way the message went."""

    # The real thing is a staticmethod, so the markup a test sees is the
    # markup Telethon would build.
    build_reply_markup = staticmethod(ButtonMethods.build_reply_markup)

    def __init__(self) -> None:
        self.sent: list[SentMessage] = []
        self.edits: list[tuple[int, int, str, Any]] = []
        self.deleted: list[tuple[int, list[int]]] = []
        self.requests: list[Any] = []
        # Set to an exception to make every raw request fail, which is how the
        # plain fallback path is exercised.
        self.raw_raises: Exception | None = None

    async def __call__(self, request):
        self.requests.append(request)
        if self.raw_raises is not None:
            raise self.raw_raises
        markdown = getattr(request.rich_message, "markdown", None)
        if isinstance(request, functions.messages.SendMessageRequest):
            reply_to = getattr(request.reply_to, "reply_to_msg_id", None)
            message = SentMessage(
                int(request.peer), request.message, _rows(request.reply_markup),
                markdown=markdown, reply_to=reply_to, link_preview=not request.no_webpage,
            )
            self.sent.append(message)
            return types.UpdateShortSentMessage(id=message.id, pts=0, pts_count=0, date=None)
        if isinstance(request, functions.messages.EditMessageRequest):
            rows = _rows(request.reply_markup)
            self.edits.append((int(request.peer), int(request.id), request.message, rows))
            message = SentMessage(int(request.peer), request.message, rows, markdown=markdown)
            message.id = int(request.id)
            return message
        raise TypeError(f"The fake client does not handle {type(request).__name__}")

    async def send_message(self, entity, text, buttons=None, **kwargs):
        message = SentMessage(int(entity), text, buttons, **kwargs)
        self.sent.append(message)
        return message

    async def edit_message(self, chat_id, message_id, text, buttons=None, **kwargs):
        # A ReplyMarkup arrives here on the plain path; unwrap it like the raw one
        if hasattr(buttons, "rows"):
            buttons = _rows(buttons)
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
        # The management surface. `account` is what a lookup answers with, which
        # is what decides whether a chat may manage anything at all.
        self.account: Any = None
        self.all_apps: list[dict[str, Any]] = []
        self.writes_raise: Exception | None = None

    async def list_apps(self):
        self.calls.append(("list_apps", ()))
        if self.raises:
            raise self.raises
        return list(self.apps)

    async def lookup_link(self, telegram_id):
        self.calls.append(("lookup_link", (telegram_id,)))
        if self.raises:
            raise self.raises
        return self.account

    async def list_all_apps(self, telegram_id):
        self.calls.append(("list_all_apps", (telegram_id,)))
        if self.writes_raise:
            raise self.writes_raise
        return list(self.all_apps)

    async def create_app(self, telegram_id, app):
        self.calls.append(("create_app", (telegram_id, app)))
        if self.writes_raise:
            raise self.writes_raise
        created = {"id": "new-1", **app}
        created.setdefault("published", False)
        return created

    async def update_app(self, telegram_id, app_id, app):
        self.calls.append(("update_app", (telegram_id, app_id, app)))
        if self.writes_raise:
            raise self.writes_raise
        existing = next(
            (a for a in self.all_apps if str(a.get("id")) == str(app_id)), {"id": app_id}
        )
        return {**existing, **app}

    async def delete_app(self, telegram_id, app_id):
        self.calls.append(("delete_app", (telegram_id, app_id)))
        if self.writes_raise:
            raise self.writes_raise
        return "Removed app"

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
    from bot.handlers import (  # noqa: F401
        admin, apps, fallback, link, manage, mfa, misc, start,
    )

    return context


def account(**overrides: Any):
    """A portal account as a lookup would answer with. An editor by default."""
    from bot.services.portal import LinkedAccount

    fields = {
        "portal_user_id": "user-1",
        "username": "tester",
        "display_name": "Tester",
        "is_admin": False,
        "is_editor": True,
        "is_approved": True,
        "linked_at": "2026-01-01T00:00:00+00:00",
        "mfa_enabled": False,
    }
    fields.update(overrides)
    return LinkedAccount(**fields)
