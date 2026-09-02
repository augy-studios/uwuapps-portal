"""SQLite access, connection pragmas and the migration runner.

One connection for the whole process. SQLite in WAL mode with a busy timeout
handles the concurrency an async bot produces, and a single connection keeps
the transaction semantics in the scheduler easy to reason about.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import aiosqlite

log = logging.getLogger("uwu.db")

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

PRAGMAS = (
    "pragma journal_mode=WAL",
    "pragma synchronous=NORMAL",
    "pragma foreign_keys=ON",
    "pragma busy_timeout=5000",
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(moment: datetime | None = None) -> str:
    """ISO 8601 UTC, second precision, the only timestamp format in this database."""
    return (moment or utcnow()).astimezone(timezone.utc).replace(microsecond=0).isoformat()


def iso_in(seconds: float) -> str:
    return iso(utcnow() + timedelta(seconds=seconds))


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()

    # --- lifecycle ---------------------------------------------------------

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        for pragma in PRAGMAS:
            await self._conn.execute(pragma)
        await self._conn.commit()
        await self.migrate()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database.connect has not been awaited yet")
        return self._conn

    # --- migrations --------------------------------------------------------

    async def _current_version(self) -> int:
        cursor = await self.conn.execute(
            "select name from sqlite_master where type='table' and name='schema_version'"
        )
        if await cursor.fetchone() is None:
            return 0
        cursor = await self.conn.execute("select version from schema_version limit 1")
        row = await cursor.fetchone()
        return int(row[0]) if row else 0

    async def migrate(self) -> int:
        """Apply every migration above the recorded version, each in one transaction."""
        version = await self._current_version()
        files = sorted(MIGRATIONS_DIR.glob("*.sql"))
        applied = version

        for path in files:
            try:
                number = int(path.name.split("_", 1)[0])
            except ValueError:
                log.warning("Skipping migration with no leading number: %s", path.name)
                continue
            if number <= applied:
                continue

            sql = path.read_text(encoding="utf-8")
            log.info("Applying migration %s", path.name)
            try:
                await self.conn.execute("begin")
                await self.conn.executescript(sql)
                await self.conn.execute("delete from schema_version")
                await self.conn.execute(
                    "insert into schema_version (version) values (?)", (number,)
                )
                await self.conn.commit()
            except Exception:
                await self.conn.rollback()
                log.exception("Migration %s failed, database left at version %s", path.name, applied)
                raise
            applied = number

        if applied != version:
            log.info("Database schema is now at version %s", applied)
        return applied

    # --- query helpers -----------------------------------------------------

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        """Write, committed. Returns lastrowid."""
        async with self._write_lock:
            cursor = await self.conn.execute(sql, params)
            await self.conn.commit()
            return cursor.lastrowid or 0

    async def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        async with self._write_lock:
            await self.conn.executemany(sql, list(rows))
            await self.conn.commit()

    async def fetchone(self, sql: str, params: Sequence[Any] = ()) -> aiosqlite.Row | None:
        cursor = await self.conn.execute(sql, params)
        return await cursor.fetchone()

    async def fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[aiosqlite.Row]:
        cursor = await self.conn.execute(sql, params)
        return list(await cursor.fetchall())

    async def fetchval(self, sql: str, params: Sequence[Any] = (), default: Any = None) -> Any:
        row = await self.fetchone(sql, params)
        return row[0] if row is not None else default

    def transaction(self) -> "_Transaction":
        """Exclusive write transaction. Used by the scheduler to claim jobs."""
        return _Transaction(self)

    # --- tables the handlers touch constantly ------------------------------

    async def touch_user(
        self,
        telegram_id: int,
        username: str | None,
        first_name: str | None,
        language_code: str | None,
    ) -> None:
        now = iso()
        await self.execute(
            """
            insert into users (telegram_id, username, first_name, language_code,
                               first_seen_at, last_seen_at)
            values (?, ?, ?, ?, ?, ?)
            on conflict(telegram_id) do update set
                username      = excluded.username,
                first_name    = excluded.first_name,
                language_code = excluded.language_code,
                is_blocked    = 0,
                last_seen_at  = excluded.last_seen_at
            """,
            (telegram_id, username, first_name, language_code, now, now),
        )

    async def set_blocked(self, telegram_id: int, blocked: bool = True) -> None:
        await self.execute(
            "update users set is_blocked = ? where telegram_id = ?",
            (1 if blocked else 0, telegram_id),
        )

    async def get_link(self, telegram_id: int) -> aiosqlite.Row | None:
        return await self.fetchone(
            "select * from links where telegram_id = ?", (telegram_id,)
        )

    async def upsert_link(
        self,
        telegram_id: int,
        portal_user_id: str,
        portal_username: str | None,
        display_name: str | None,
        is_admin: bool,
        is_editor: bool = False,
        is_approved: bool = False,
    ) -> None:
        now = iso()
        await self.execute(
            """
            insert into links (telegram_id, portal_user_id, portal_username,
                               display_name, is_admin, is_editor, is_approved,
                               linked_at, last_synced_at)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(telegram_id) do update set
                portal_user_id  = excluded.portal_user_id,
                portal_username = excluded.portal_username,
                display_name    = excluded.display_name,
                is_admin        = excluded.is_admin,
                is_editor       = excluded.is_editor,
                is_approved     = excluded.is_approved,
                last_synced_at  = excluded.last_synced_at
            """,
            (telegram_id, portal_user_id, portal_username, display_name,
             1 if is_admin else 0, 1 if is_editor else 0, 1 if is_approved else 0,
             now, now),
        )

    async def log_command(
        self,
        telegram_id: int | None,
        command: str,
        succeeded: bool,
        duration_ms: int,
    ) -> None:
        await self.execute(
            """insert into command_log (telegram_id, command, ok, duration_ms, created_at)
               values (?, ?, ?, ?, ?)""",
            (telegram_id, command, 1 if succeeded else 0, duration_ms, iso()),
        )

    async def log_mfa_event(
        self, telegram_id: int, event: str, challenge_id: str | None = None
    ) -> None:
        """Audit only. The digits of a code never reach this table."""
        await self.execute(
            """insert into mfa_events (telegram_id, event, challenge_id, created_at)
               values (?, ?, ?, ?)""",
            (telegram_id, event, challenge_id, iso()),
        )

    async def record_link_attempt(self, telegram_id: int, succeeded: bool) -> None:
        await self.execute(
            """insert into link_attempts (telegram_id, succeeded, attempted_at)
               values (?, ?, ?)""",
            (telegram_id, 1 if succeeded else 0, iso()),
        )

    async def recent_failed_link_attempts(self, telegram_id: int, window_seconds: int) -> int:
        since = iso(utcnow() - timedelta(seconds=window_seconds))
        return int(
            await self.fetchval(
                """select count(*) from link_attempts
                   where telegram_id = ? and succeeded = 0 and attempted_at >= ?""",
                (telegram_id, since),
                default=0,
            )
        )

    async def is_subscribed(self, telegram_id: int, topic: str) -> bool:
        row = await self.fetchone(
            "select 1 from subscriptions where telegram_id = ? and topic = ?",
            (telegram_id, topic),
        )
        return row is not None

    async def subscribe(self, telegram_id: int, topic: str) -> None:
        await self.execute(
            """insert or ignore into subscriptions (telegram_id, topic, created_at)
               values (?, ?, ?)""",
            (telegram_id, topic, iso()),
        )

    async def unsubscribe(self, telegram_id: int, topic: str) -> None:
        await self.execute(
            "delete from subscriptions where telegram_id = ? and topic = ?",
            (telegram_id, topic),
        )

    # --- app drafts --------------------------------------------------------

    async def get_app_draft(self, telegram_id: int) -> aiosqlite.Row | None:
        return await self.fetchone(
            "select * from app_drafts where telegram_id = ?", (telegram_id,)
        )

    async def save_app_draft(
        self,
        telegram_id: int,
        *,
        app_id: str | None,
        fields: str,
        touched: str,
        awaiting: str | None,
        chat_id: int | None,
        message_id: int | None,
    ) -> None:
        """One draft per person, so writing one always replaces the last."""
        now = iso()
        await self.execute(
            """
            insert into app_drafts (telegram_id, app_id, fields, touched, awaiting,
                                    chat_id, message_id, created_at, updated_at)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(telegram_id) do update set
                app_id     = excluded.app_id,
                fields     = excluded.fields,
                touched    = excluded.touched,
                awaiting   = excluded.awaiting,
                chat_id    = excluded.chat_id,
                message_id = excluded.message_id,
                updated_at = excluded.updated_at
            """,
            (telegram_id, app_id, fields, touched, awaiting, chat_id, message_id, now, now),
        )

    async def delete_app_draft(self, telegram_id: int) -> None:
        await self.execute("delete from app_drafts where telegram_id = ?", (telegram_id,))

    async def subscribers(self, topic: str) -> list[int]:
        rows = await self.fetchall(
            """select s.telegram_id from subscriptions s
               join users u on u.telegram_id = s.telegram_id
               where s.topic = ? and u.is_blocked = 0""",
            (topic,),
        )
        return [int(row[0]) for row in rows]


class _Transaction:
    """`async with db.transaction() as conn:` around a claim or a multi row write."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def __aenter__(self) -> aiosqlite.Connection:
        await self._db._write_lock.acquire()
        await self._db.conn.execute("begin immediate")
        return self._db.conn

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        try:
            if exc_type is None:
                await self._db.conn.commit()
            else:
                await self._db.conn.rollback()
        finally:
            self._db._write_lock.release()
        return False
