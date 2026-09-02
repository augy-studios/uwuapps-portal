"""The command registry, and the single dispatcher every message goes through.

`/start` is the only reference surface, so the command list must not be a hand
written block that drifts away from what is actually registered. It is built
from this registry, and so is the BotFather block, from the same source. A
command registered without a description raises at import time, which is the
build failing rather than a command quietly going undocumented.
"""

from __future__ import annotations

import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from telethon import events

from .. import rich
from ..context import Ctx

log = logging.getLogger("uwu.handlers")

CommandHandler = Callable[[Any, str, Ctx], Awaitable[None]]


@dataclass(frozen=True)
class Command:
    name: str
    description: str
    handler: CommandHandler
    visible: bool = True       # shown in /start and in the BotFather list
    admin_only: bool = False   # gated on ADMIN_TELEGRAM_IDS
    manager_only: bool = False # gated on the linked account's portal role
    weight: int = 50           # display order, not registration order
    order: int = 0


_COMMANDS: dict[str, Command] = {}
_ORDER = 0


def command(
    name: str,
    description: str,
    *,
    visible: bool = True,
    admin_only: bool = False,
    manager_only: bool = False,
    weight: int = 50,
) -> Callable[[CommandHandler], CommandHandler]:
    """Register a command. The description is mandatory, deliberately.

    `weight` fixes the order the lists come out in, so it does not silently
    depend on the order the handler modules happen to be imported.

    `admin_only` and `manager_only` are both enforced in one place, the
    dispatcher below, and both answer a refusal with the ordinary unknown
    command reply rather than a denial that confirms the command exists.
    """

    def decorator(func: CommandHandler) -> CommandHandler:
        global _ORDER
        clean = name.lstrip("/").lower()
        if not description or not description.strip():
            raise ValueError(f"Command /{clean} needs a one line description")
        if clean in _COMMANDS:
            raise ValueError(f"Command /{clean} is already registered")
        _ORDER += 1
        _COMMANDS[clean] = Command(
            name=clean,
            description=description.strip(),
            handler=func,
            visible=visible,
            admin_only=admin_only,
            manager_only=manager_only,
            weight=weight,
            order=_ORDER,
        )
        return func

    return decorator


def all_commands() -> list[Command]:
    return sorted(_COMMANDS.values(), key=lambda c: (c.weight, c.order))


def get(name: str) -> Command | None:
    return _COMMANDS.get(name.lstrip("/").lower())


def visible_commands(
    *, include_admin: bool = False, include_manager: bool = False
) -> list[Command]:
    return [
        c for c in all_commands()
        if c.visible
        and (include_admin or not c.admin_only)
        and (include_manager or not c.manager_only)
    ]


def command_list_html(
    *, include_admin: bool = False, include_manager: bool = False
) -> str:
    """The block `/start` renders. One line each, no bot name anywhere."""
    lines = [
        f"/{c.name} {rich.esc('- ' + c.description)}"
        for c in visible_commands(include_admin=include_admin, include_manager=include_manager)
    ]
    return "\n".join(lines)


def botfather_block() -> str:
    """Exactly what goes into Edit Commands. Same source as the /start list.

    Admin and management commands are left out. The list is public, and
    advertising a command that answers almost nobody is noise.
    """
    return "\n".join(
        f"{c.name} - {c.description}" for c in visible_commands()
    )


def reply_id(event: Any) -> int | None:
    """The message to reply to, or None when the event is a button press.

    A CallbackQuery has no `.message`, so every handler that can be reached
    from both a command and a button goes through this.
    """
    message = getattr(event, "message", None)
    return getattr(message, "id", None)


# --- button rows shared across handlers ------------------------------------


def web_app_button(ctx: Ctx) -> rich.Btn:
    return rich.Btn.link("Open the web app", ctx.config.portal_web_app_url)


def donate_button(ctx: Ctx) -> rich.Btn:
    return rich.Btn.link("Support the project", ctx.config.donation_url)


def portal_button(ctx: Ctx, text: str = "Open the portal") -> rich.Btn:
    return rich.Btn.link(text, ctx.config.portal_web_app_url)


async def account_button(ctx: Ctx, telegram_id: int) -> rich.Btn:
    """Link my account until there is a link, My account afterwards."""
    linked = await ctx.db.get_link(telegram_id)
    if linked is None:
        return rich.Btn.callback("Link my account", "link.start")
    return rich.Btn.callback("My account", "account.show")


# --- dispatch --------------------------------------------------------------


def _parse(text: str, username: str | None) -> tuple[str | None, str]:
    """'/link ABC @thebot' becomes ('link', 'ABC'). Not a command becomes (None, text)."""
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None, stripped
    head, _, rest = stripped.partition(" ")
    name = head[1:]
    if "@" in name:
        name, _, mention = name.partition("@")
        if username and mention.lower() != username.lower():
            return None, stripped  # addressed at a different bot
    return name.lower(), rest.strip()


def register_all(ctx: Ctx) -> None:
    """Import the handler modules, then wire the two Telethon entry points."""
    from . import admin, apps, fallback, link, manage, mfa, misc, start  # noqa: F401

    client = ctx.client

    @client.on(events.NewMessage(incoming=True))
    async def _on_message(event: Any) -> None:  # pragma: no cover, needs a live client
        await handle_message(event, ctx)

    @client.on(events.CallbackQuery())
    async def _on_callback(event: Any) -> None:  # pragma: no cover, needs a live client
        await handle_callback(event, ctx)

    log.info(
        "Registered %d commands and %d callback actions",
        len(_COMMANDS),
        len(ctx.callbacks.known_actions()),
    )


async def handle_message(event: Any, ctx: Ctx) -> None:
    # Privacy mode is on, so ordinary group messages never arrive. Free text
    # search is a private chat feature, and a command in a group still works.
    if not event.is_private and not (event.message.text or "").startswith("/"):
        return

    sender = await event.get_sender()
    telegram_id = event.sender_id
    if sender is not None and getattr(sender, "bot", False):
        return

    await ctx.db.touch_user(
        telegram_id,
        getattr(sender, "username", None),
        getattr(sender, "first_name", None),
        getattr(sender, "lang_code", None),
    )

    text = event.message.text or ""
    username = getattr(ctx.me, "username", None)
    name, args = _parse(text, username)

    from . import fallback, link, manage

    started = time.monotonic()
    label = f"/{name}" if name else "text"
    succeeded = False
    try:
        if name is None:
            # Three cases take priority over search, in this order. A half
            # finished linking code beats a half finished app, because a link
            # code expires in ten minutes and a draft waits indefinitely.
            if await link.consume_pending(event, text, ctx):
                label = "/link"
            elif await manage.consume_pending(event, text, ctx):
                label = "/manage"
            else:
                await fallback.free_text(event, text, ctx)
        else:
            cmd = get(name)
            if cmd is None:
                await fallback.unknown_command(event, ctx)
            elif cmd.admin_only and not ctx.is_admin(telegram_id):
                await fallback.unknown_command(event, ctx)
            elif cmd.manager_only and not await manage.may_manage(ctx, telegram_id):
                await fallback.unknown_command(event, ctx)
            else:
                await cmd.handler(event, args, ctx)
        succeeded = True
    except Exception:
        await _apologise(event, ctx)
    finally:
        await ctx.db.log_command(
            telegram_id, label, succeeded, int((time.monotonic() - started) * 1000)
        )


async def handle_callback(event: Any, ctx: Ctx) -> None:
    from . import mfa

    try:
        # The mfa: buttons are the deliberate exception to the token registry,
        # because the portal composed those messages and this database has
        # never seen them.
        if await mfa.handle_callback(event, ctx):
            return
        await ctx.callbacks.dispatch(event, ctx)
    except Exception:
        log.exception("Callback handler raised")
        try:
            await event.answer(rich.GENERIC_ERROR, alert=True)
        except Exception:
            pass


async def _apologise(event: Any, ctx: Ctx) -> None:
    incident = secrets.token_hex(4)
    log.exception("Handler raised, incident %s", incident)
    try:
        await rich.send_rich_message(
            ctx.client, event.chat_id, rich.incident_note(incident), reply_to=event.message.id
        )
    except Exception:
        log.exception("Could not deliver the apology for incident %s", incident)
    if ctx.config.admin_chat_id:
        try:
            await rich.send_rich_message(
                ctx.client,
                ctx.config.admin_chat_id,
                f"A handler raised. Incident {rich.esc(incident)}, see the log file.",
                title="Error",
            )
        except Exception:
            log.exception("Could not alert the admin chat about incident %s", incident)
