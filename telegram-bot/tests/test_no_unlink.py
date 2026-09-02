"""Acceptance criterion 17.

No command, callback, or code path in the bot can delete a link. This drives
/unlink and every callback action the bot is able to register, then asserts the
link row is still there.

It is a structural test as much as a behavioural one: the second half asserts
that no source file contains a statement that could delete from `links`, so a
future handler cannot quietly acquire the ability.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from bot import callbacks as callbacks_module
from bot.db import iso
from bot.handlers import link as link_handler

from .conftest import FakeCallbackEvent, FakeEvent, SentMessage

ROOT = Path(__file__).resolve().parent.parent


async def _make_link(ctx, telegram_id: int = 42) -> None:
    await ctx.db.touch_user(telegram_id, "tester", "Test", "en")
    await ctx.db.upsert_link(telegram_id, "user-1", "tester", "Tester", False)


async def test_unlink_command_refuses_and_keeps_the_link(ctx):
    await _make_link(ctx)
    event = FakeEvent("/unlink")

    await link_handler.handle_unlink(event, "", ctx)

    assert await ctx.db.get_link(42) is not None
    assert ctx.client.sent, "The refusal must still say something useful"
    body = ctx.client.sent[-1].text
    assert "portal" in body.lower()


async def test_unlink_offers_no_confirmation_button(ctx):
    await _make_link(ctx)
    await link_handler.handle_unlink(FakeEvent("/unlink"), "", ctx)

    sent = ctx.client.sent[-1]
    rows = sent.buttons or []
    for row in rows:
        for button in row:
            # A URL button cannot call back into the bot at all
            assert type(button).__name__ == "KeyboardButtonUrl", button


async def test_no_registered_callback_action_can_delete_a_link(ctx):
    await _make_link(ctx)
    message = SentMessage(42, "existing", None)

    for action in ctx.callbacks.known_actions():
        data = await ctx.callbacks.register(action, _payload_for(action), owner_id=42)
        event = FakeCallbackEvent(data, sender_id=42, message=message)
        try:
            await ctx.callbacks.dispatch(event, ctx)
        except Exception:
            # A handler that fails for an unrelated reason is fine here. What
            # matters is that no path through it removed the link.
            pass
        assert await ctx.db.get_link(42) is not None, f"{action} removed the link"


def _payload_for(action: str) -> dict:
    if action.startswith("apps."):
        return {"mode": "all", "q": "", "page": 0, "id": "missing"}
    if action == "notify.toggle":
        return {"on": True}
    return {}


async def test_a_link_survives_every_command_the_bot_exposes(ctx):
    """Includes /link with nonsense arguments, which is the closest thing to a reset."""
    from bot.handlers import all_commands

    await _make_link(ctx)
    for command in all_commands():
        for args in ("", "ZZZZZZZZ", "off"):
            try:
                await command.handler(FakeEvent(f"/{command.name} {args}"), args, ctx)
            except Exception:
                pass
            assert await ctx.db.get_link(42) is not None, f"/{command.name} removed the link"


def test_no_source_file_deletes_from_the_links_table():
    pattern = re.compile(r"delete\s+from\s+links|drop\s+table\s+links", re.IGNORECASE)
    offenders = []
    for path in (ROOT / "bot").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if pattern.search(path.read_text(encoding="utf-8")):
            offenders.append(path.name)
    assert offenders == [], f"A link deleting statement exists in {offenders}"


def test_the_portal_client_exposes_no_unlink_action():
    """The shared secret does not open unlinking, and there is no wrapper for it."""
    from bot.services import portal

    source = Path(portal.__file__).read_text(encoding="utf-8")
    assert "unlink" not in source.lower()
