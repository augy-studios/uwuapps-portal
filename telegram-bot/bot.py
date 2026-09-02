#!/usr/bin/env python3
"""Entrypoint. Wires everything together and blocks until the process is stopped.

Run it through run.sh so signals reach Python cleanly:

    tmux new -s uwubot
    cd ~/uwuapps-portal/telegram-bot
    ./run.sh

`python3 bot.py --botfather` prints the exact block for BotFather's Edit
Commands, generated from the same registry that renders /start, so the two can
never disagree.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from telethon import TelegramClient
from telethon.sessions import SQLiteSession

from bot import callbacks as callbacks_module
from bot import config as config_module
from bot import handlers, jobs, logging_setup, rich
from bot.context import Ctx
from bot.db import Database, iso
from bot.scheduler import Scheduler
from bot.services.cache import Cache
from bot.services.portal import Portal

log = logging.getLogger("uwu.main")


def print_botfather_block() -> int:
    """Needs no credentials, so it works before .env is filled in."""
    from bot.handlers import botfather_block
    from bot.handlers import (  # noqa: F401
        admin, apps, fallback, link, manage, mfa, misc, start,
    )

    print(botfather_block())
    return 0


async def amain() -> int:
    config = config_module.load_or_exit()
    logging_setup.setup(config.log_dir, config.log_level)
    log.info("Starting up, portal at %s", config.portal_base_url)

    db = Database(config.db_path)
    await db.connect()

    portal = Portal(config)
    await portal.start()

    cache = Cache(db)
    registry = callbacks_module.CallbackRegistry(db)
    scheduler = Scheduler(
        db,
        tick_seconds=config.scheduler_tick_seconds,
        stale_job_seconds=config.stale_job_seconds,
        flood_sleep_threshold=config.flood_sleep_threshold,
    )
    rich.configure(registry, scheduler)

    # The session file is a live credential, which is why .gitignore covers it.
    client = TelegramClient(
        SQLiteSession(str(config.db_path.parent / "bot.session")),
        config.api_id,
        config.api_hash,
        flood_sleep_threshold=config.flood_sleep_threshold,
    )

    ctx = Ctx(
        client=client,
        db=db,
        config=config,
        portal=portal,
        cache=cache,
        callbacks=registry,
        scheduler=scheduler,
        started_at=iso(),
    )
    scheduler.ctx = ctx

    jobs.register_all(scheduler)

    await client.start(bot_token=config.bot_token)
    ctx.me = await client.get_me()
    log.info("Signed in as the bot account, id %s", getattr(ctx.me, "id", "unknown"))

    handlers.register_all(ctx)
    await jobs.seed_recurring(scheduler)
    scheduler.start()

    # A warm cache means the first /apps of the day is not the slow one.
    try:
        from bot.handlers import apps as apps_handler

        published, _ = await apps_handler.fetch_apps(ctx)
        log.info("App list ready, %d published apps", len(published))
    except Exception as exc:
        log.warning("Could not warm the app cache at startup: %s", exc)

    log.info("Ready. %d commands registered.", len(handlers.all_commands()))

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            # Windows, where Ctrl-c raises KeyboardInterrupt instead.
            pass

    try:
        await stop.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        log.info("Shutting down")
        await scheduler.stop()
        await client.disconnect()
        await portal.close()
        await db.close()
        log.info("Stopped cleanly")

    return 0


def main() -> int:
    if "--botfather" in sys.argv:
        return print_botfather_block()
    try:
        return asyncio.run(amain())
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
