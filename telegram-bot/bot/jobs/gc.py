"""Housekeeping. Deletes what has genuinely expired, and nothing else."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from ..context import Ctx
from ..db import iso, utcnow

log = logging.getLogger("uwu.jobs.gc")

FINISHED_JOB_RETENTION_DAYS = 7


async def cache_gc(payload: dict[str, Any], ctx: Ctx) -> None:
    removed = await ctx.cache.gc()
    if removed:
        log.info("Removed %d expired cache row(s)", removed)


async def callbacks_gc(payload: dict[str, Any], ctx: Ctx) -> None:
    """Expired rows only.

    A navigation button has no expiry and must keep working forever, including
    across restarts and redeploys, so age alone is never a reason to delete.
    """
    removed = await ctx.callbacks.gc()
    if removed:
        log.info("Removed %d expired callback row(s)", removed)


async def log_gc(payload: dict[str, Any], ctx: Ctx) -> None:
    cutoff = iso(utcnow() - timedelta(days=ctx.config.log_retention_days))
    for table in ("command_log", "mfa_events", "link_attempts"):
        column = "attempted_at" if table == "link_attempts" else "created_at"
        cursor = await ctx.db.conn.execute(
            f"delete from {table} where {column} < ?", (cutoff,)
        )
        if cursor.rowcount:
            log.info("Trimmed %d row(s) from %s", cursor.rowcount, table)
    await ctx.db.conn.commit()

    finished = iso(utcnow() - timedelta(days=FINISHED_JOB_RETENTION_DAYS))
    cursor = await ctx.db.conn.execute(
        "delete from scheduled_jobs where status = 'done' and updated_at < ?", (finished,)
    )
    await ctx.db.conn.commit()
    if cursor.rowcount:
        log.info("Trimmed %d finished job row(s)", cursor.rowcount)
