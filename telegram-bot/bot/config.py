"""Environment parsing and validation.

Validated at import time so a missing key kills the process at startup with a
readable message, rather than surfacing as a mystery failure inside a handler
three hours later.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# telegram-bot/ , the directory holding bot.py, .env and data/
ROOT = Path(__file__).resolve().parent.parent

load_dotenv(ROOT / ".env")


class ConfigError(RuntimeError):
    """Raised when the environment cannot produce a usable configuration."""


def _require(key: str) -> str:
    value = (os.getenv(key) or "").strip()
    if not value:
        raise ConfigError(
            f"{key} is required but missing or empty. "
            f"Copy .env.example to .env and fill it in."
        )
    return value


def _optional(key: str, default: str = "") -> str:
    return (os.getenv(key) or default).strip()


def _int(key: str, default: int) -> int:
    raw = _optional(key)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be a whole number, got {raw!r}") from exc


def _id_list(key: str) -> tuple[int, ...]:
    raw = _optional(key)
    if not raw:
        return ()
    out: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError as exc:
            raise ConfigError(
                f"{key} must be comma separated Telegram ids, got {part!r}"
            ) from exc
    return tuple(out)


def _url(key: str, required: bool = True, default: str = "") -> str:
    value = _require(key) if required else _optional(key, default)
    if value and not value.startswith(("http://", "https://")):
        raise ConfigError(f"{key} must start with http:// or https://, got {value!r}")
    return value.rstrip("/") if value else value


@dataclass(frozen=True)
class Config:
    # Telegram
    api_id: int
    api_hash: str
    bot_token: str

    # Portal
    portal_base_url: str
    portal_web_app_url: str
    donation_url: str
    shared_secret: str

    # Operations
    admin_ids: tuple[int, ...] = ()
    admin_chat_id: int | None = None
    db_path: Path = ROOT / "data" / "bot.db"
    log_dir: Path = ROOT / "logs"
    log_level: str = "INFO"
    scheduler_tick_seconds: int = 15

    # Tuning, not exposed in .env because nobody has needed to change them
    http_timeout_seconds: float = 5.0
    flood_sleep_threshold: int = 60
    stale_job_seconds: int = 300
    apps_cache_ttl_seconds: int = 15 * 60
    log_retention_days: int = 30
    page_size: int = 5

    def is_admin(self, telegram_id: int | None) -> bool:
        return telegram_id is not None and telegram_id in self.admin_ids

    def api(self, path: str) -> str:
        """Absolute portal URL for an API path such as '/api/apps'."""
        return f"{self.portal_base_url}{path}"


def load() -> Config:
    api_id_raw = _require("TELEGRAM_API_ID")
    try:
        api_id = int(api_id_raw)
    except ValueError as exc:
        raise ConfigError(
            f"TELEGRAM_API_ID must be the numeric id from my.telegram.org, got {api_id_raw!r}"
        ) from exc

    admin_chat_raw = _optional("ADMIN_CHAT_ID")
    admin_chat_id: int | None = None
    if admin_chat_raw:
        try:
            admin_chat_id = int(admin_chat_raw)
        except ValueError as exc:
            raise ConfigError(
                f"ADMIN_CHAT_ID must be a numeric chat id, got {admin_chat_raw!r}"
            ) from exc

    db_raw = _optional("DB_PATH", "./data/bot.db")
    db_path = Path(db_raw)
    if not db_path.is_absolute():
        db_path = ROOT / db_path

    tick = _int("SCHEDULER_TICK_SECONDS", 15)
    if tick < 1:
        raise ConfigError("SCHEDULER_TICK_SECONDS must be at least 1")

    return Config(
        api_id=api_id,
        api_hash=_require("TELEGRAM_API_HASH"),
        bot_token=_require("TELEGRAM_BOT_TOKEN"),
        portal_base_url=_url("PORTAL_BASE_URL"),
        portal_web_app_url=_url("PORTAL_WEB_APP_URL"),
        donation_url=_url("DONATION_URL"),
        shared_secret=_require("TELEGRAM_BOT_SHARED_SECRET"),
        admin_ids=_id_list("ADMIN_TELEGRAM_IDS"),
        admin_chat_id=admin_chat_id,
        db_path=db_path,
        log_dir=ROOT / "logs",
        log_level=_optional("LOG_LEVEL", "INFO").upper(),
        scheduler_tick_seconds=tick,
    )


def load_or_exit() -> Config:
    """Entry point helper. Prints the problem and exits rather than tracebacking."""
    try:
        return load()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
