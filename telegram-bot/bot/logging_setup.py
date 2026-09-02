"""Rotating file log plus stdout, so the tmux pane stays useful.

Nothing in here ever sees a secret. The rule that keeps that true lives at the
call sites: one time codes, link codes, the bot token and the
shared secret are never passed to a logger, not in a message body and not in
button markup, which carries the same digits.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

_FORMAT = "%(asctime)s %(levelname)-7s %(name)-22s %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

MAX_BYTES = 10 * 1024 * 1024
BACKUP_COUNT = 5


def setup(log_dir: Path, level: str = "INFO") -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(getattr(logging, level, logging.INFO))

    # Idempotent, so a reload in tests does not stack handlers
    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = logging.Formatter(_FORMAT, datefmt=_DATEFMT)

    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "bot.log",
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)

    # Telethon is chatty at DEBUG and says nothing useful at INFO
    logging.getLogger("telethon").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    return logging.getLogger("uwu")
