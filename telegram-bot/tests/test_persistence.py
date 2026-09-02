"""Acceptance criteria 5 and 6, the two things a restart must not break.

5. Killing the process and restarting it leaves every previously sent button
   working.
6. Killing the process mid job leaves no job permanently stuck in `running`.
"""

from __future__ import annotations

import pytest

from bot import callbacks as callbacks_module
from bot import rich
from bot.db import Database, iso, iso_in, utcnow
from bot.scheduler import Scheduler

from .conftest import FakeCallbackEvent


async def test_a_button_still_works_after_a_restart(tmp_path):
    path = tmp_path / "restart.db"
    pressed: list[dict] = []

    @callbacks_module.action("test.navigate")
    async def _handler(event, payload, ctx):
        pressed.append(payload)
        await event.answer("ok")

    first = Database(path)
    await first.connect()
    registry = callbacks_module.CallbackRegistry(first)
    data = await registry.register("test.navigate", {"page": 3}, owner_id=42)
    await first.close()

    # The process dies here. Everything in memory is gone.
    second = Database(path)
    await second.connect()
    reborn = callbacks_module.CallbackRegistry(second)
    event = FakeCallbackEvent(data, sender_id=42)
    handled = await reborn.dispatch(event, None)
    await second.close()

    assert handled
    assert pressed == [{"page": 3}]


async def test_an_unknown_token_gets_a_polite_answer(ctx):
    event = FakeCallbackEvent("cb:deadbeefdeadbeef", sender_id=42)
    assert await ctx.callbacks.dispatch(event, ctx)
    text, alert = event.answers[-1]
    assert "expired" in text.lower()
    assert alert is True


async def test_a_button_belongs_to_the_user_who_triggered_it(ctx):
    @callbacks_module.action("test.owned")
    async def _handler(event, payload, ctx_):
        raise AssertionError("must not run for a different user")

    data = await ctx.callbacks.register("test.owned", {}, owner_id=42)
    event = FakeCallbackEvent(data, sender_id=99)
    await ctx.callbacks.dispatch(event, ctx)
    assert "somebody else" in event.answers[-1][0]


async def test_navigation_buttons_never_expire(ctx):
    data = await ctx.callbacks.register("apps.page", {"mode": "all", "q": "", "page": 0})
    row = await ctx.callbacks.lookup(data.removeprefix("cb:"))
    assert row["expires_at"] is None
    assert row["max_uses"] is None


async def test_garbage_collection_spares_rows_that_are_merely_old(ctx):
    keep = await ctx.callbacks.register("apps.page", {"mode": "all"})
    drop = await ctx.callbacks.register(
        "apps.page", {"mode": "all"}, expires_at=iso_in(-60)
    )
    await ctx.db.execute(
        "update callbacks set created_at = ? where id = ?",
        ("2020-01-01T00:00:00+00:00", keep.removeprefix("cb:")),
    )

    removed = await ctx.callbacks.gc()

    assert removed == 1
    assert await ctx.callbacks.lookup(keep.removeprefix("cb:")) is not None
    assert await ctx.callbacks.lookup(drop.removeprefix("cb:")) is None


async def test_a_registered_row_exists_before_the_message_is_sent(ctx):
    """Telegram can deliver a press the instant a message lands."""
    await rich.send_rich_message(
        ctx.client,
        42,
        "body",
        buttons=[[rich.Btn.callback("Next", "apps.page", {"page": 1})]],
        owner_id=42,
    )
    rows = await ctx.db.fetchall("select * from callbacks")
    assert len(rows) == 1
    sent = ctx.client.sent[-1]
    assert rows[0]["message_id"] == sent.id


# --- the scheduler ---------------------------------------------------------


async def test_a_hard_kill_does_not_strand_a_job(db):
    scheduler = Scheduler(db, tick_seconds=1, stale_job_seconds=60)
    job_id = await scheduler.schedule("apps.refresh", {})

    # Simulate the state a hard kill leaves behind: claimed, never finished.
    await db.execute(
        "update scheduled_jobs set status = 'running', locked_by = 'dead', locked_at = ? where id = ?",
        (iso(utcnow().replace(year=utcnow().year - 1)), job_id),
    )

    released = await scheduler.sweep_stale()

    assert released == 1
    row = await db.fetchone("select status, locked_by from scheduled_jobs where id = ?", (job_id,))
    assert row["status"] == "pending"
    assert row["locked_by"] is None


async def test_a_recurring_job_reschedules_itself(db):
    scheduler = Scheduler(db, tick_seconds=1)
    runs: list[int] = []

    async def handler(payload, ctx):
        runs.append(1)

    scheduler.register("test.tick", handler)
    await scheduler.schedule("test.tick", {}, interval_secs=900, delay_seconds=-1)

    assert await scheduler.run_once() == 1
    row = await db.fetchone("select status, run_at from scheduled_jobs limit 1")
    assert row["status"] == "pending"
    assert row["run_at"] > iso()
    assert runs == [1]


async def test_a_failing_job_backs_off_and_eventually_gives_up(db):
    scheduler = Scheduler(db, tick_seconds=1)

    async def handler(payload, ctx):
        raise RuntimeError("nope")

    scheduler.register("test.fail", handler)
    job_id = await scheduler.schedule("test.fail", {}, max_attempts=2, delay_seconds=-1)

    await scheduler.run_once()
    row = await db.fetchone("select status, attempts from scheduled_jobs where id = ?", (job_id,))
    assert row["status"] == "pending"
    assert row["attempts"] == 1

    await db.execute("update scheduled_jobs set run_at = ? where id = ?", (iso(), job_id))
    await scheduler.run_once()
    row = await db.fetchone(
        "select status, last_error from scheduled_jobs where id = ?", (job_id,)
    )
    assert row["status"] == "failed"
    assert "nope" in row["last_error"]


async def test_seeding_a_recurring_job_twice_leaves_one_row(db):
    scheduler = Scheduler(db, tick_seconds=1)

    async def handler(payload, ctx):
        return None

    scheduler.register("apps.refresh", handler)
    await scheduler.ensure_recurring("apps.refresh", 900)
    await scheduler.ensure_recurring("apps.refresh", 900)

    count = await db.fetchval("select count(*) from scheduled_jobs", default=0)
    assert count == 1


async def test_migrations_are_idempotent(tmp_path):
    path = tmp_path / "migrate.db"
    first = Database(path)
    await first.connect()
    version = await first.migrate()
    await first.close()

    second = Database(path)
    await second.connect()
    assert await second.migrate() == version
    rows = await second.fetchall("select version from schema_version")
    await second.close()
    assert len(rows) == 1
