"""/stats, admin only.

Gated on ADMIN_TELEGRAM_IDS. The command is registered hidden, so it never
reaches the BotFather list, and a non admin who guesses the name gets the same
unknown command reply as for any other typo rather than a permission denied
message that confirms the command exists.

/broadcast is deliberately absent from this build.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from .. import rich
from ..context import Ctx
from ..db import iso, utcnow
from . import command, reply_id

log = logging.getLogger("uwu.handlers.admin")

WINDOW_DAYS = 7


@command("stats", "Usage numbers for the last seven days", admin_only=True, weight=90)
async def handle_stats(event: Any, args: str, ctx: Ctx) -> None:
    since = iso(utcnow() - timedelta(days=WINDOW_DAYS))

    users = await ctx.db.fetchval("select count(*) from users", default=0)
    blocked = await ctx.db.fetchval(
        "select count(*) from users where is_blocked = 1", default=0
    )
    links = await ctx.db.fetchval("select count(*) from links", default=0)
    subscribers = await ctx.db.fetchval(
        "select count(*) from subscriptions where topic = 'new_apps'", default=0
    )
    active = await ctx.db.fetchval(
        "select count(distinct telegram_id) from command_log where created_at >= ?",
        (since,),
        default=0,
    )
    failures = await ctx.db.fetchval(
        "select count(*) from command_log where created_at >= ? and ok = 0",
        (since,),
        default=0,
    )
    codes = await ctx.db.fetchval(
        "select count(*) from mfa_events where created_at >= ? and event = 'code_issued'",
        (since,),
        default=0,
    )
    approvals = await ctx.db.fetchval(
        "select count(*) from mfa_events where created_at >= ? and event = 'approved'",
        (since,),
        default=0,
    )
    denials = await ctx.db.fetchval(
        "select count(*) from mfa_events where created_at >= ? and event = 'denied'",
        (since,),
        default=0,
    )
    stuck = await ctx.db.fetchval(
        "select count(*) from scheduled_jobs where status = 'failed'", default=0
    )

    rows = await ctx.db.fetchall(
        """select command, count(*) as uses from command_log
           where created_at >= ?
           group by command order by uses desc limit 10""",
        (since,),
    )

    sections = [
        rich.table(
            ["Count"],
            [
                ("Known users", users),
                ("Of which blocked the chat", blocked),
                ("Linked accounts", links),
                ("Announcement subscribers", subscribers),
                ("Jobs marked failed", stuck),
            ],
        ),
        f"## Last {WINDOW_DAYS} days",
        rich.table(
            ["Count"],
            [
                ("Active users", active),
                ("Failed replies", failures),
                ("Codes issued", codes),
                ("Approvals", approvals),
                ("Refusals", denials),
            ],
        ),
        "## Commands",
        rich.table(["Uses"], [(row["command"], row["uses"]) for row in rows], corner="Command")
        if rows
        else "Nothing yet.",
    ]

    await rich.send_rich_message(
        ctx.client, event.chat_id, rich.message("\n\n".join(sections), title="Stats"),
        reply_to=reply_id(event), owner_id=event.sender_id,
    )
