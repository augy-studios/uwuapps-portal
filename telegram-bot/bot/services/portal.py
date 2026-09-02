"""Typed wrappers over the portal API.

The bot talks to the portal over HTTPS and never directly to Supabase, so the
database credentials stay on Vercel and the VPS holds one shared secret that
opens a small, deliberately chosen set of actions.

Authentication:

* Read endpoints are plain GETs and carry nothing.
* /api/telegram takes the shared secret instead, since minting a session for
  the bot would defeat the point.

Retry rules, uniform for every call: 5 second connect and read timeout, one
retry on a connection error or a 5xx, and no retry on a 4xx.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from . import signing

log = logging.getLogger("uwu.portal")

APPS_PATH = "/api/apps"
TELEGRAM_PATH = "/api/telegram"


class PortalError(Exception):
    """The portal answered, and said no."""

    def __init__(self, message: str, status: int = 0, code: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class PortalUnavailable(Exception):
    """The portal did not answer at all, or answered with a 5xx twice."""


@dataclass
class LinkedAccount:
    portal_user_id: str
    username: str | None
    display_name: str | None
    is_admin: bool
    is_editor: bool
    is_approved: bool
    linked_at: str | None
    mfa_enabled: bool

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> "LinkedAccount":
        user = data.get("user") or {}
        return cls(
            portal_user_id=str(user.get("id") or ""),
            username=user.get("username"),
            display_name=user.get("displayName") or user.get("username"),
            is_admin=bool(user.get("isAdmin")),
            is_editor=bool(user.get("isEditor")),
            is_approved=bool(user.get("isApproved")),
            linked_at=data.get("linkedAt"),
            mfa_enabled=bool(data.get("mfaEnabled")),
        )


@dataclass
class IssuedCode:
    code: str
    expires_at: str
    seconds_remaining: int
    superseded_pushed_code: bool


class Portal:
    def __init__(self, config: Any) -> None:
        self.config = config
        self._client: httpx.AsyncClient | None = None
        self.last_success_at: str | None = None

    async def start(self) -> None:
        timeout = httpx.Timeout(
            connect=self.config.http_timeout_seconds,
            read=self.config.http_timeout_seconds,
            write=self.config.http_timeout_seconds,
            pool=self.config.http_timeout_seconds,
        )
        self._client = httpx.AsyncClient(
            base_url=self.config.portal_base_url,
            timeout=timeout,
            headers={"User-Agent": "uwu-suite-telegram/1.0"},
            follow_redirects=False,
        )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("Portal.start has not been awaited yet")
        return self._client

    # --- low level ---------------------------------------------------------

    async def _get(self, path: str, attempt: int = 0) -> dict[str, Any]:
        try:
            response = await self.client.get(path)
        except httpx.HTTPError as exc:
            if attempt == 0:
                log.warning("GET %s failed (%s), retrying once", path, exc)
                return await self._get(path, attempt + 1)
            raise PortalUnavailable(f"Could not reach the portal: {exc}") from exc

        if response.status_code >= 500:
            if attempt == 0:
                log.warning("GET %s returned %s, retrying once", path, response.status_code)
                return await self._get(path, attempt + 1)
            raise PortalUnavailable(f"The portal returned {response.status_code}")

        payload = _json_or_error(response)
        if response.status_code >= 400 or not payload.get("ok"):
            raise PortalError(
                payload.get("error") or f"The portal returned {response.status_code}",
                status=response.status_code,
            )

        self.last_success_at = _now_iso()
        return payload

    async def _bot_post(self, action: str, payload: dict[str, Any], attempt: int = 0) -> dict[str, Any]:
        # Compact separators and no key that looks like an array index, so the
        # portal's JSON.stringify of the parsed body reproduces this byte for byte.
        body = json.dumps({"action": action, **payload}, separators=(",", ":"), ensure_ascii=False)
        headers = signing.bot_headers(self.config.shared_secret, body)

        try:
            response = await self.client.post(TELEGRAM_PATH, content=body, headers=headers)
        except httpx.HTTPError as exc:
            if attempt == 0:
                log.warning("POST %s (%s) failed (%s), retrying once", TELEGRAM_PATH, action, exc)
                return await self._bot_post(action, payload, attempt + 1)
            raise PortalUnavailable(f"Could not reach the portal: {exc}") from exc

        if response.status_code >= 500:
            if attempt == 0:
                log.warning(
                    "POST %s (%s) returned %s, retrying once",
                    TELEGRAM_PATH, action, response.status_code,
                )
                return await self._bot_post(action, payload, attempt + 1)
            raise PortalUnavailable(f"The portal returned {response.status_code}")

        data = _json_or_error(response)
        if response.status_code >= 400 or not data.get("ok"):
            raise PortalError(
                data.get("error") or f"The portal returned {response.status_code}",
                status=response.status_code,
                code=data.get("code"),
            )

        self.last_success_at = _now_iso()
        return data

    # --- read endpoints ----------------------------------------------------

    async def list_apps(self) -> list[dict[str, Any]]:
        data = await self._get(APPS_PATH)
        apps = data.get("apps") or []
        # An unauthenticated read sees published rows only, but be explicit about it.
        return [app for app in apps if app.get("published")]

    # --- the directory, as a signed in account ------------------------------
    #
    # These four carry a Telegram id, and the portal turns it into the account
    # that is acting. The shared secret proves where the call came from and
    # never who is typing, so the role check happens there, not here.

    async def list_all_apps(self, telegram_id: int) -> list[dict[str, Any]]:
        """Everything the account may see, drafts included."""
        data = await self._bot_post("app_list", {"telegramId": telegram_id})
        return list(data.get("apps") or [])

    async def create_app(self, telegram_id: int, app: dict[str, Any]) -> dict[str, Any]:
        data = await self._bot_post("app_create", {"telegramId": telegram_id, "app": app})
        return dict(data.get("app") or {})

    async def update_app(
        self, telegram_id: int, app_id: str, app: dict[str, Any]
    ) -> dict[str, Any]:
        data = await self._bot_post(
            "app_update", {"telegramId": telegram_id, "id": app_id, "app": app}
        )
        return dict(data.get("app") or {})

    async def delete_app(self, telegram_id: int, app_id: str) -> str:
        data = await self._bot_post("app_delete", {"telegramId": telegram_id, "id": app_id})
        return str(data.get("title") or "")

    # --- linking -----------------------------------------------------------

    async def redeem_link_code(
        self, code: str, telegram_id: int, telegram_username: str | None
    ) -> LinkedAccount:
        data = await self._bot_post(
            "redeem",
            {
                "code": code,
                "telegramId": telegram_id,
                "telegramUsername": telegram_username or "",
            },
        )
        return LinkedAccount.from_payload(data)

    async def lookup_link(self, telegram_id: int) -> LinkedAccount | None:
        data = await self._bot_post("lookup", {"telegramId": telegram_id})
        if not data.get("linked"):
            return None
        return LinkedAccount.from_payload(data)

    # --- two factor authentication -----------------------------------------

    async def mfa_resolve(self, challenge_id: str, telegram_id: int, decision: str) -> dict[str, Any]:
        """Record an approval or a denial. The portal is authoritative about both."""
        return await self._bot_post(
            "mfa_resolve",
            {"challengeId": challenge_id, "decision": decision, "telegramId": telegram_id},
        )

    async def mfa_issue_code(self, telegram_id: int) -> IssuedCode:
        data = await self._bot_post("mfa_issue_code", {"telegramId": telegram_id})
        return IssuedCode(
            code=str(data["code"]),
            expires_at=str(data["expiresAt"]),
            seconds_remaining=int(data.get("secondsRemaining") or 0),
            superseded_pushed_code=bool(data.get("supersededPushedCode")),
        )

    async def mfa_recent_events(self, telegram_id: int, limit: int = 5) -> list[dict[str, Any]]:
        data = await self._bot_post(
            "mfa_recent", {"telegramId": telegram_id, "limit": limit}
        )
        return list(data.get("events") or [])


def _json_or_error(response: httpx.Response) -> dict[str, Any]:
    try:
        parsed = response.json()
    except ValueError:
        return {"ok": False, "error": f"The portal returned {response.status_code}"}
    return parsed if isinstance(parsed, dict) else {"ok": False, "error": "Unexpected response"}


def _now_iso() -> str:
    from ..db import iso

    return iso()
