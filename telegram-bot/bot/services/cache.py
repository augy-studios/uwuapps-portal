"""Read through cache over the `api_cache` table.

A burst of /apps calls or free text searches hits SQLite rather than the
portal. When the portal is unreachable the stale copy is served with a line
saying so, which is what keeps every command answering usefully during an
outage.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from ..db import Database, iso, iso_in, parse_iso, utcnow

log = logging.getLogger("uwu.cache")


@dataclass
class CacheHit:
    value: Any
    stale: bool
    fetched_at: str | None = None


class Cache:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def read(self, key: str, *, allow_stale: bool = False) -> CacheHit | None:
        row = await self.db.fetchone(
            "select body, fetched_at, expires_at from api_cache where cache_key = ?", (key,)
        )
        if row is None:
            return None
        expires_at = parse_iso(row["expires_at"])
        stale = expires_at is None or expires_at < utcnow()
        if stale and not allow_stale:
            return None
        try:
            value = json.loads(row["body"])
        except json.JSONDecodeError:
            return None
        return CacheHit(value=value, stale=stale, fetched_at=row["fetched_at"])

    async def write(self, key: str, value: Any, ttl_seconds: int) -> None:
        await self.db.execute(
            """
            insert into api_cache (cache_key, body, fetched_at, expires_at)
            values (?, ?, ?, ?)
            on conflict(cache_key) do update set
                body = excluded.body,
                fetched_at = excluded.fetched_at,
                expires_at = excluded.expires_at
            """,
            (
                key,
                json.dumps(value, separators=(",", ":"), ensure_ascii=False),
                iso(),
                iso_in(ttl_seconds),
            ),
        )

    async def invalidate(self, key: str) -> None:
        await self.db.execute("delete from api_cache where cache_key = ?", (key,))

    async def gc(self) -> int:
        cursor = await self.db.conn.execute(
            "delete from api_cache where expires_at < ?", (iso(),)
        )
        await self.db.conn.commit()
        return cursor.rowcount or 0

    async def get_or_fetch(
        self,
        key: str,
        fetcher: Callable[[], Awaitable[Any]],
        ttl_seconds: int,
        *,
        force: bool = False,
    ) -> CacheHit:
        """Fresh cache, else the portal, else the stale copy marked as such."""
        if not force:
            hit = await self.read(key)
            if hit is not None:
                return hit

        try:
            value = await fetcher()
        except Exception as exc:
            stale = await self.read(key, allow_stale=True)
            if stale is not None:
                log.warning("Portal call for %s failed (%s), serving the cached copy", key, exc)
                return CacheHit(value=stale.value, stale=True, fetched_at=stale.fetched_at)
            raise

        await self.write(key, value, ttl_seconds)
        return CacheHit(value=value, stale=False, fetched_at=iso())
