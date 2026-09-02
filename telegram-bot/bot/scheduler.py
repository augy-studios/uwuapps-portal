"""A SQLite backed job loop. No cron, no APScheduler, no Redis.

One async worker ticks on an interval, claims due rows inside a single
transaction so a second process could never take the same row, and puts the
result back. Recurring jobs reschedule themselves, one shot jobs finish.

The property that matters after a hard kill: a startup sweep releases rows left
in `running` with a stale lock, so nothing is stuck forever.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
from datetime import timedelta
from typing import Any, Awaitable, Callable

from .db import Database, iso, iso_in, utcnow

log = logging.getLogger("uwu.scheduler")

JobHandler = Callable[[dict[str, Any], Any], Awaitable[None]]

BACKOFF_BASE_SECONDS = 30
BACKOFF_CAP_SECONDS = 30 * 60
CLAIM_BATCH = 5


class Scheduler:
    def __init__(
        self,
        db: Database,
        *,
        tick_seconds: int = 15,
        stale_job_seconds: int = 300,
        flood_sleep_threshold: int = 60,
    ) -> None:
        self.db = db
        self.tick_seconds = tick_seconds
        self.stale_job_seconds = stale_job_seconds
        self.flood_sleep_threshold = flood_sleep_threshold
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}"
        self.ctx: Any = None
        self._handlers: dict[str, JobHandler] = {}
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    # --- registry ----------------------------------------------------------

    def register(self, job_type: str, handler: JobHandler) -> None:
        if job_type in self._handlers:
            raise ValueError(f"Job type {job_type!r} is already registered")
        self._handlers[job_type] = handler

    def known_types(self) -> list[str]:
        return sorted(self._handlers)

    # --- scheduling --------------------------------------------------------

    async def schedule(
        self,
        job_type: str,
        payload: dict[str, Any] | None = None,
        *,
        delay_seconds: float = 0,
        run_at: str | None = None,
        interval_secs: int | None = None,
        max_attempts: int = 5,
    ) -> int:
        now = iso()
        return await self.db.execute(
            """
            insert into scheduled_jobs (job_type, payload, run_at, interval_secs,
                                        status, max_attempts, created_at, updated_at)
            values (?, ?, ?, ?, 'pending', ?, ?, ?)
            """,
            (
                job_type,
                json.dumps(payload or {}, separators=(",", ":")),
                run_at or iso_in(delay_seconds),
                interval_secs,
                max_attempts,
                now,
                now,
            ),
        )

    async def ensure_recurring(self, job_type: str, interval_secs: int) -> None:
        """Seed a recurring job once. Restarting the bot must not duplicate it."""
        existing = await self.db.fetchone(
            """select id from scheduled_jobs
               where job_type = ? and interval_secs is not null
                 and status in ('pending', 'running')
               limit 1""",
            (job_type,),
        )
        if existing is not None:
            return
        await self.schedule(job_type, {}, delay_seconds=5, interval_secs=interval_secs)
        log.info("Seeded recurring job %s every %ss", job_type, interval_secs)

    async def pending_count(self) -> int:
        return int(
            await self.db.fetchval(
                "select count(*) from scheduled_jobs where status = 'pending'", default=0
            )
        )

    # --- the loop ----------------------------------------------------------

    async def sweep_stale(self) -> int:
        """Release rows a hard kill left locked in `running`."""
        cutoff_iso = iso(utcnow() - timedelta(seconds=self.stale_job_seconds))
        cursor = await self.db.conn.execute(
            """update scheduled_jobs
               set status = 'pending', locked_by = null, locked_at = null, updated_at = ?
               where status = 'running' and (locked_at is null or locked_at < ?)""",
            (iso(), cutoff_iso),
        )
        await self.db.conn.commit()
        released = cursor.rowcount or 0
        if released:
            log.warning("Released %d job(s) left running by an earlier process", released)
        return released

    async def _claim(self) -> list[dict[str, Any]]:
        now = iso()
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                """select id, job_type, payload, interval_secs, attempts, max_attempts
                   from scheduled_jobs
                   where status = 'pending' and run_at <= ?
                   order by run_at limit ?""",
                (now, CLAIM_BATCH),
            )
            rows = [dict(row) for row in await cursor.fetchall()]
            if rows:
                ids = [row["id"] for row in rows]
                placeholders = ",".join("?" for _ in ids)
                await conn.execute(
                    f"""update scheduled_jobs
                        set status = 'running', locked_by = ?, locked_at = ?, updated_at = ?
                        where id in ({placeholders})""",
                    (self.worker_id, now, now, *ids),
                )
        return rows

    async def _finish(self, job: dict[str, Any]) -> None:
        now = iso()
        if job["interval_secs"]:
            await self.db.execute(
                """update scheduled_jobs
                   set status = 'pending', attempts = 0, last_error = null,
                       locked_by = null, locked_at = null, run_at = ?, updated_at = ?
                   where id = ?""",
                (iso_in(job["interval_secs"]), now, job["id"]),
            )
        else:
            await self.db.execute(
                """update scheduled_jobs
                   set status = 'done', locked_by = null, locked_at = null, updated_at = ?
                   where id = ?""",
                (now, job["id"]),
            )

    async def _fail(self, job: dict[str, Any], error: str) -> None:
        attempts = job["attempts"] + 1
        now = iso()
        error = error[:500]
        if attempts >= job["max_attempts"]:
            await self.db.execute(
                """update scheduled_jobs
                   set status = 'failed', attempts = ?, last_error = ?,
                       locked_by = null, locked_at = null, updated_at = ?
                   where id = ?""",
                (attempts, error, now, job["id"]),
            )
            log.error("Job %s (%s) failed permanently: %s", job["id"], job["job_type"], error)
            return

        backoff = min(BACKOFF_BASE_SECONDS * (2 ** (attempts - 1)), BACKOFF_CAP_SECONDS)
        await self.db.execute(
            """update scheduled_jobs
               set status = 'pending', attempts = ?, last_error = ?, run_at = ?,
                   locked_by = null, locked_at = null, updated_at = ?
               where id = ?""",
            (attempts, error, iso_in(backoff), now, job["id"]),
        )
        log.warning(
            "Job %s (%s) failed, retry %s of %s in %ss: %s",
            job["id"], job["job_type"], attempts, job["max_attempts"], backoff, error,
        )

    async def run_once(self) -> int:
        """One tick. Returns how many jobs were run, handy in tests."""
        jobs = await self._claim()
        for job in jobs:
            handler = self._handlers.get(job["job_type"])
            if handler is None:
                await self._fail(job, f"No handler registered for {job['job_type']}")
                continue
            try:
                payload = json.loads(job["payload"] or "{}")
            except json.JSONDecodeError:
                payload = {}
            try:
                await handler(payload, self.ctx)
            except Exception as exc:  # one bad job must not stop the loop
                log.exception("Job %s (%s) raised", job["id"], job["job_type"])
                await self._fail(job, f"{type(exc).__name__}: {exc}")
            else:
                await self._finish(job)
        return len(jobs)

    async def _loop(self) -> None:
        await self.sweep_stale()
        log.info("Scheduler started, ticking every %ss", self.tick_seconds)
        while not self._stopping.is_set():
            try:
                await self.run_once()
            except Exception:
                log.exception("Scheduler tick raised, continuing")
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self.tick_seconds)
            except asyncio.TimeoutError:
                pass
        log.info("Scheduler stopped")

    def start(self) -> None:
        if self._task is None:
            self._stopping.clear()
            self._task = asyncio.create_task(self._loop(), name="scheduler")

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
            self._task = None
