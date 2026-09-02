"""The one object every handler is handed.

Small deliberate addition to the layout in the specification. Without it every
handler would reach for module level globals to find the database or the portal
client, which is the thing that makes handlers untestable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover, import cycle avoidance only
    from telethon import TelegramClient

    from .callbacks import CallbackRegistry
    from .config import Config
    from .db import Database
    from .scheduler import Scheduler
    from .services.cache import Cache
    from .services.portal import Portal


@dataclass
class Ctx:
    client: "TelegramClient"
    db: "Database"
    config: "Config"
    portal: "Portal"
    cache: "Cache"
    callbacks: "CallbackRegistry"
    scheduler: "Scheduler"
    started_at: str = ""
    me: Any = None

    def is_admin(self, telegram_id: int | None) -> bool:
        return self.config.is_admin(telegram_id)
