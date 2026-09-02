"""Who is allowed to change the directory from a chat.

Two questions, deliberately answered in two different places.

*Should this chat be offered the management commands?* Answered here, from the
portal when it answers and from the local mirror row when it does not. It is a
question about what to draw, so a slightly stale answer is acceptable and an
outage that hides every command is not.

*May this write happen?* Never answered here. Every create, update and delete
is decided by the portal, against the account the Telegram id is linked to, at
the moment of the write. Nothing in this module can grant anything: the worst a
stale role does is offer somebody a command the portal then refuses.

The role is cached for a few minutes so that rendering /start does not cost a
round trip on every message.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from ..context import Ctx
from .portal import PortalError, PortalUnavailable

log = logging.getLogger("uwu.permissions")

CACHE_PREFIX = "role:"
CACHE_TTL_SECONDS = 5 * 60


@dataclass(frozen=True)
class Role:
    """What the linked portal account is allowed to do."""

    portal_user_id: str
    username: str | None
    display_name: str | None
    is_admin: bool
    is_editor: bool
    is_approved: bool
    stale: bool = False

    @property
    def can_manage(self) -> bool:
        """Approved, and an editor or an admin. The portal's own rule."""
        return self.is_approved and (self.is_editor or self.is_admin)

    @property
    def can_delete(self) -> bool:
        """Deleting is an admin action on the portal, so it is one here too."""
        return self.is_approved and self.is_admin

    @property
    def label(self) -> str:
        if self.is_admin:
            return "admin"
        if self.is_editor:
            return "editor"
        if self.is_approved:
            return "viewer"
        return "awaiting approval"


async def role_for(ctx: Ctx, telegram_id: int, *, force: bool = False) -> Role | None:
    """The account linked to this chat, or None when there is no usable link."""
    async def fetch() -> dict[str, Any]:
        account = await ctx.portal.lookup_link(telegram_id)
        if account is None:
            return {"linked": False}
        return {
            "linked": True,
            "id": account.portal_user_id,
            "username": account.username,
            "name": account.display_name,
            "admin": account.is_admin,
            "editor": account.is_editor,
            "approved": account.is_approved,
        }

    hit = None
    try:
        hit = await ctx.cache.get_or_fetch(
            f"{CACHE_PREFIX}{telegram_id}", fetch, CACHE_TTL_SECONDS, force=force
        )
    except (PortalUnavailable, PortalError):
        log.warning("Could not refresh the role for telegram id %s", telegram_id)

    if hit is not None and isinstance(hit.value, dict):
        if not hit.value.get("linked"):
            return None
        role = Role(
            portal_user_id=str(hit.value.get("id") or ""),
            username=hit.value.get("username"),
            display_name=hit.value.get("name"),
            is_admin=bool(hit.value.get("admin")),
            is_editor=bool(hit.value.get("editor")),
            is_approved=bool(hit.value.get("approved")),
            stale=hit.stale,
        )
        if not hit.stale:
            await ctx.db.upsert_link(
                telegram_id,
                role.portal_user_id,
                role.username,
                role.display_name,
                role.is_admin,
                role.is_editor,
                role.is_approved,
            )
        return role

    # The portal never answered and nothing was cached. The mirror row is what
    # is left, and it is marked stale so the reply can say so.
    row = await ctx.db.get_link(telegram_id)
    if row is None:
        return None
    return Role(
        portal_user_id=str(row["portal_user_id"]),
        username=row["portal_username"],
        display_name=row["display_name"],
        is_admin=bool(row["is_admin"]),
        is_editor=bool(row["is_editor"]),
        is_approved=bool(row["is_approved"]),
        stale=True,
    )


async def invalidate(ctx: Ctx, telegram_id: int) -> None:
    """Drop the cached role, so a fresh link takes effect on the next command."""
    await ctx.cache.invalidate(f"{CACHE_PREFIX}{telegram_id}")
