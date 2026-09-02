"""Managing the directory from a chat: /manage, /add, /edit, /publish, /delete.

Who gets in. Two gates, and they are not the same gate.

* The commands are offered to an operator listed in ADMIN_TELEGRAM_IDS, and to
  any chat linked to a portal account that is approved and holds the editor or
  admin role. Everybody else gets the ordinary unknown command reply, so the
  commands do not advertise themselves to people who cannot use them.
* Every write is authorised again by the portal, against the linked account, at
  the moment it happens. Nothing here can grant anything. The local check
  decides what to draw, the portal decides what is true, and an editor who was
  demoted a minute ago is refused by the portal even while this chat still
  offers the button.

Why a form rather than arguments. `/add Title | https://... | tags` is one typo
away from a wrong row and gives no way to review before saving. The draft is a
message with one button per field: press a field, type the value, watch the
form update. It is stored in SQLite, so a restart in the middle costs nothing,
and one draft per person makes resuming unambiguous.

Deliberately absent: uploading images. A cover image is set by URL here, and
the gallery an app already has is left alone unless that field is touched.
Picture management belongs in the Admin Panel, where the upload endpoint and a
preview are.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field as dataclass_field
from typing import Any

from .. import callbacks, rich
from ..context import Ctx
from ..db import iso, utcnow
from ..services import permissions
from ..services.permissions import Role
from ..services.portal import PortalError, PortalUnavailable
from . import command, portal_button, reply_id

log = logging.getLogger("uwu.handlers.manage")

# The tag vocabulary the portal accepts. Kept in step with ALLOWED_TAGS in
# main-site/lib/uwu-apps.js: a tag missing from that list is refused on save,
# so offering one here that is not there would be a button that always fails.
ALLOWED_TAGS = ("tools", "games", "bots", "singapore")

PICK_PAGE_SIZE = 6
TITLE_LIMIT = 120
DESCRIPTION_LIMIT = 600
URL_LIMIT = 400

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

NOT_A_MANAGER = (
    "Managing the directory needs a portal account with the editor or admin "
    "role, linked to this chat.\n\n"
    "Run /link if you have an account, or ask an admin for the role."
)

NEEDS_A_LINK = (
    "This chat is not linked to a portal account yet, and the directory is "
    "changed as an account, never as an anonymous caller.\n\n"
    "Run /link and it walks you through it."
)


# --- the fields of an app --------------------------------------------------


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    kind: str                 # text | url | tags | date | number
    required: bool = False
    limit: int = 0
    hint: str = ""


FIELDS: tuple[Field, ...] = (
    Field("title", "Title", "text", required=True, limit=TITLE_LIMIT,
          hint="The name people will see in the directory."),
    Field("url", "Link", "url", required=True, limit=URL_LIMIT,
          hint="The address the app opens at, starting with https://"),
    Field("description", "Description", "text", limit=DESCRIPTION_LIMIT,
          hint="A sentence or two about what it does."),
    Field("tags", "Tags", "tags",
          hint="Press a tag to turn it on or off."),
    Field("image", "Cover image", "url", limit=URL_LIMIT,
          hint="A link to the picture shown on the card."),
    Field("published_date", "Published date", "date",
          hint="Four digit year, month, day, for example 2026-09-02. Send today for today."),
    Field("sort_order", "Sort order", "number",
          hint="A whole number. Lower comes first in the list."),
)

BY_KEY = {f.key: f for f in FIELDS}


@dataclass
class Draft:
    """One unfinished app. `touched` is what makes an edit a patch."""

    telegram_id: int
    app_id: str | None = None
    fields: dict[str, Any] = dataclass_field(default_factory=dict)
    touched: set[str] = dataclass_field(default_factory=set)
    awaiting: str | None = None
    chat_id: int | None = None
    message_id: int | None = None

    @property
    def is_new(self) -> bool:
        return self.app_id is None

    def missing(self) -> list[Field]:
        return [f for f in FIELDS if f.required and not self.fields.get(f.key)]

    def set(self, key: str, value: Any) -> None:
        self.fields[key] = value
        self.touched.add(key)

    def payload(self) -> dict[str, Any]:
        """What goes to the portal.

        A new app sends everything. An edit sends only the fields somebody
        actually changed, so an untouched gallery, or a column this chat does
        not model at all, is left exactly as it is.
        """
        keys = set(self.fields) if self.is_new else set(self.touched)
        out: dict[str, Any] = {}
        if "title" in keys:
            out["title"] = self.fields.get("title") or ""
        if "url" in keys:
            out["url"] = self.fields.get("url") or ""
        if "description" in keys:
            out["description"] = self.fields.get("description") or ""
        if "tags" in keys:
            out["tags"] = list(self.fields.get("tags") or [])
        if "published_date" in keys:
            out["publishedDate"] = self.fields.get("published_date") or ""
        if "sort_order" in keys:
            out["sortOrder"] = int(self.fields.get("sort_order") or 0)
        if "published" in keys or self.is_new:
            out["published"] = bool(self.fields.get("published"))
        if "image" in keys:
            gallery = list(self.fields.get("gallery") or [])
            image = self.fields.get("image") or ""
            if image:
                gallery = [image] + [g for g in gallery if g != image]
            else:
                gallery = []
            out["galleryUrls"] = gallery
            out["thumbnailIndex"] = 0
        return out


# --- draft storage ---------------------------------------------------------


async def load_draft(ctx: Ctx, telegram_id: int) -> Draft | None:
    row = await ctx.db.get_app_draft(telegram_id)
    if row is None:
        return None
    try:
        fields = json.loads(row["fields"])
        touched = json.loads(row["touched"])
    except json.JSONDecodeError:
        fields, touched = {}, []
    return Draft(
        telegram_id=telegram_id,
        app_id=row["app_id"],
        fields=fields if isinstance(fields, dict) else {},
        touched=set(touched) if isinstance(touched, list) else set(),
        awaiting=row["awaiting"],
        chat_id=row["chat_id"],
        message_id=row["message_id"],
    )


async def save_draft(ctx: Ctx, draft: Draft) -> None:
    await ctx.db.save_app_draft(
        draft.telegram_id,
        app_id=draft.app_id,
        fields=rich.dumps(draft.fields),
        touched=rich.dumps(sorted(draft.touched)),
        awaiting=draft.awaiting,
        chat_id=draft.chat_id,
        message_id=draft.message_id,
    )


@dataclass
class _MessageRef:
    """Enough of a Message for rich.send_rich_message to edit it in place."""

    chat_id: int
    id: int


def _form_target(draft: Draft) -> _MessageRef | None:
    if draft.chat_id is None or draft.message_id is None:
        return None
    return _MessageRef(chat_id=draft.chat_id, id=draft.message_id)


# --- the gate --------------------------------------------------------------


async def may_manage(ctx: Ctx, telegram_id: int) -> bool:
    """Whether to offer the commands at all. The portal decides the rest.

    Asked on every /start, so a chat with no mirror row is answered without a
    round trip. The mirror is written the moment a link is made, so no row means
    no link, and a stranger running /start costs nothing.
    """
    if ctx.is_admin(telegram_id):
        return True
    if await ctx.db.get_link(telegram_id) is None:
        return False
    role = await permissions.role_for(ctx, telegram_id)
    return role is not None and role.can_manage


async def _require_role(event: Any, ctx: Ctx, *, edit: Any = None) -> Role | None:
    """The account this chat acts as, or a reply explaining why there is none."""
    telegram_id = event.sender_id
    role = await permissions.role_for(ctx, telegram_id)
    if role is not None and role.can_manage:
        return role

    body = NEEDS_A_LINK if role is None else NOT_A_MANAGER
    await rich.send_rich_message(
        ctx.client,
        event.chat_id,
        body,
        title="Not available here",
        buttons=[[portal_button(ctx)]],
        reply_to=reply_id(event),
        owner_id=telegram_id,
        edit=edit,
    )
    return None


# --- rendering the form ----------------------------------------------------


def _shown(draft: Draft, f: Field) -> str:
    value = draft.fields.get(f.key)
    if f.kind == "tags":
        tags = list(value or [])
        return ", ".join(tags) if tags else "none"
    if value in (None, ""):
        return "required" if f.required else "not set"
    if f.kind == "text" and len(str(value)) > 80:
        return str(value)[:79].rstrip() + "…"
    return str(value)


def render_form(draft: Draft, *, note: str = "") -> tuple[str, str, list[list[rich.Btn]]]:
    owner = draft.telegram_id
    lines = [
        f"{rich.esc(f.label)}: {rich.esc(_shown(draft, f))}" for f in FIELDS
    ]
    state = "Published" if draft.fields.get("published") else "Kept as a draft"
    lines.append(f"Visibility: {rich.esc(state)}")

    body = "\n".join(lines)
    if note:
        body += f"\n\n{note}"

    missing = draft.missing()
    if missing:
        names = ", ".join(f.label.lower() for f in missing)
        body += f"\n\nStill needed before this can be saved: {rich.esc(names)}."

    rows: list[list[rich.Btn]] = []
    pairs = [FIELDS[i:i + 2] for i in range(0, len(FIELDS), 2)]
    for pair in pairs:
        rows.append(
            [
                rich.Btn.callback(f.label, "manage.field", {"f": f.key}, owner_id=owner)
                for f in pair
            ]
        )

    toggle = "Keep as a draft" if draft.fields.get("published") else "Publish it"
    rows.append([rich.Btn.callback(toggle, "manage.visibility", {}, owner_id=owner)])

    save = "Save the changes" if not draft.is_new else "Save it"
    rows.append(
        [
            rich.Btn.callback(save, "manage.save", {}, owner_id=owner),
            rich.Btn.callback("Discard", "manage.discard", {}, owner_id=owner),
        ]
    )

    title = "New app" if draft.is_new else f"Editing {draft.fields.get('title') or 'an app'}"
    return title, body, rows


async def _paint(
    event: Any,
    ctx: Ctx,
    draft: Draft,
    body: str,
    *,
    title: str,
    rows: list[list[rich.Btn]],
    target: Any = None,
) -> None:
    """Draw a step of the flow onto the one message the draft owns.

    The form, the prompt for a single field and the tag picker are all the same
    message edited in place, so a long draft leaves one message in the chat
    rather than a column of them. The coordinates are stored, which is what lets
    a typed reply land back on the same message after a restart.
    """
    where = target if target is not None else _form_target(draft)
    try:
        sent = await rich.send_rich_message(
            ctx.client, event.chat_id, body, title=title, buttons=rows,
            owner_id=draft.telegram_id, edit=where,
        )
    except Exception:
        # The message was deleted, or is too old to edit. Start a new one.
        log.warning("Could not edit the open form for telegram id %s", draft.telegram_id)
        sent = await rich.send_rich_message(
            ctx.client, event.chat_id, body, title=title, buttons=rows,
            owner_id=draft.telegram_id,
        )

    if sent is not None:
        draft.chat_id = sent.chat_id
        draft.message_id = sent.id
    await save_draft(ctx, draft)


async def show_form(
    event: Any, ctx: Ctx, draft: Draft, *, note: str = "", edit: Any = None
) -> None:
    title, body, rows = render_form(draft, note=note)
    await _paint(event, ctx, draft, body, title=title, rows=rows, target=edit)


# --- collecting one field --------------------------------------------------


async def _ask_for(event: Any, ctx: Ctx, draft: Draft, f: Field, edit: Any = None) -> None:
    owner = draft.telegram_id
    if f.kind == "tags":
        await _ask_for_tags(event, ctx, draft, edit=edit)
        return

    draft.awaiting = f.key
    current = _shown(draft, f)
    body = f"{rich.esc(f.hint)}\n\nCurrently: {rich.esc(current)}"
    rows = [[rich.Btn.callback("Back to the form", "manage.form", {}, owner_id=owner)]]
    if not f.required:
        rows[0].insert(
            0, rich.Btn.callback("Leave it empty", "manage.clear", {"f": f.key}, owner_id=owner)
        )

    await _paint(
        event, ctx, draft, body,
        title=f"Send the {f.label.lower()}", rows=rows, target=edit,
    )


async def _ask_for_tags(event: Any, ctx: Ctx, draft: Draft, edit: Any = None) -> None:
    owner = draft.telegram_id
    draft.awaiting = None
    chosen = set(draft.fields.get("tags") or [])
    rows = [
        [
            rich.Btn.callback(
                f"{tag} is on" if tag in chosen else f"{tag} is off",
                "manage.tag",
                {"t": tag},
                owner_id=owner,
            )
        ]
        for tag in ALLOWED_TAGS
    ]
    rows.append([rich.Btn.callback("Back to the form", "manage.form", {}, owner_id=owner)])

    await _paint(
        event,
        ctx,
        draft,
        "Press a tag to turn it on or off. An app can carry as many as suit it.",
        title="Tags",
        rows=rows,
        target=edit,
    )


def parse_value(f: Field, raw: str) -> tuple[Any, str | None]:
    """The typed value, or a reason it was not accepted."""
    text = " ".join(str(raw or "").split())
    if not text:
        return None, "That came through empty. Send something, or press the button to leave it out."

    if f.kind == "url":
        if not text.startswith(("http://", "https://")):
            return None, "An address has to start with http:// or https://"
        if len(text) > f.limit:
            return None, f"That is longer than {f.limit} characters."
        return text, None

    if f.kind == "date":
        if text.lower() == "today":
            return iso(utcnow())[:10], None
        if not DATE_RE.match(text):
            return None, "Send a date as four digit year, month, day, for example 2026-09-02."
        return text, None

    if f.kind == "number":
        try:
            return int(text), None
        except ValueError:
            return None, "Send a whole number, for example 10."

    if f.limit and len(text) > f.limit:
        return None, f"That is longer than {f.limit} characters."
    return text, None


async def consume_pending(event: Any, text: str, ctx: Ctx) -> bool:
    """Free text belongs to a draft when one is waiting on a field.

    Checked after the linking flow and before the directory search, so typing a
    title never turns into a search for it.
    """
    if not event.is_private:
        return False

    draft = await load_draft(ctx, event.sender_id)
    if draft is None or not draft.awaiting:
        return False

    f = BY_KEY.get(draft.awaiting)
    if f is None:
        draft.awaiting = None
        await save_draft(ctx, draft)
        return False

    value, problem = parse_value(f, text)
    if problem is not None:
        await rich.send_rich_message(
            ctx.client,
            event.chat_id,
            f"{rich.esc(problem)}\n\n{rich.esc(f.hint)}",
            title=f"That is not a {f.label.lower()} yet",
            buttons=[
                [rich.Btn.callback("Back to the form", "manage.form", {}, owner_id=draft.telegram_id)]
            ],
            reply_to=reply_id(event),
            owner_id=draft.telegram_id,
        )
        return True

    draft.set(f.key, value)
    draft.awaiting = None
    await show_form(event, ctx, draft, note=f"Saved the {rich.esc(f.label.lower())}.")
    return True


# --- commands --------------------------------------------------------------


@command("manage", "Add and edit apps in the directory", manager_only=True, weight=25)
async def handle_manage(event: Any, args: str, ctx: Ctx) -> None:
    role = await _require_role(event, ctx)
    if role is None:
        return
    await _show_hub(event, ctx, role)


async def _show_hub(event: Any, ctx: Ctx, role: Role, edit: Any = None) -> None:
    owner = event.sender_id
    draft = await load_draft(ctx, owner)

    lines = [
        f"Acting as {rich.esc(role.display_name or role.username or 'your portal account')}, "
        f"role {rich.esc(role.label)}.",
        "Editors may change the apps they created. Admins may change any of them, "
        "and are the only ones who can delete.",
    ]
    if draft is not None:
        what = draft.fields.get("title") or "an app with no title yet"
        lines.append(f"There is an unfinished draft here: {rich.esc(what)}.")
    if role.stale:
        lines.append(rich.STALE_DATA)

    rows: list[list[rich.Btn]] = []
    if draft is not None:
        rows.append([rich.Btn.callback("Carry on with the draft", "manage.form", {}, owner_id=owner)])
    rows.append(
        [
            rich.Btn.callback("Add an app", "manage.new", {}, owner_id=owner),
            rich.Btn.callback("Edit an app", "manage.list", {"next": "edit", "page": 0}, owner_id=owner),
        ]
    )
    third = [
        rich.Btn.callback(
            "Unpublished apps", "manage.list", {"next": "edit", "page": 0, "only": "drafts"},
            owner_id=owner,
        )
    ]
    if role.can_delete:
        third.append(
            rich.Btn.callback(
                "Remove an app", "manage.list", {"next": "delete", "page": 0}, owner_id=owner
            )
        )
    rows.append(third)
    rows.append([portal_button(ctx)])

    await rich.send_rich_message(
        ctx.client,
        event.chat_id,
        "\n\n".join(lines),
        title="Managing the directory",
        buttons=rows,
        reply_to=reply_id(event),
        owner_id=owner,
        edit=edit,
    )


@command("add", "Add an app to the directory", manager_only=True, weight=26)
async def handle_add(event: Any, args: str, ctx: Ctx) -> None:
    role = await _require_role(event, ctx)
    if role is None:
        return

    existing = await load_draft(ctx, event.sender_id)
    if existing is not None:
        await _offer_the_draft(event, ctx, existing)
        return

    draft = Draft(telegram_id=event.sender_id)
    if args.strip():
        value, problem = parse_value(BY_KEY["title"], args)
        if problem is None:
            draft.set("title", value)
    await show_form(event, ctx, draft)


async def _offer_the_draft(event: Any, ctx: Ctx, draft: Draft, *, instead: str = "") -> None:
    """One draft at a time, so a second one has to say what happens to the first.

    `instead` is the app that was being opened when the old draft got in the
    way, so throwing the old one away lands on that app rather than on a blank
    form somebody then has to find their way back from.
    """
    what = draft.fields.get("title") or "an app with no title yet"
    kind = "a new app" if draft.is_new else "an app that is already in the directory"
    await rich.send_rich_message(
        ctx.client,
        event.chat_id,
        (
            f"There is already a draft waiting here, {rich.esc(kind)}: "
            f"{rich.esc(what)}.\n\n"
            "Carry on with it, or throw it away and start on the other one. Only "
            "one draft is kept at a time."
        ),
        title="A draft is already open",
        buttons=[
            [
                rich.Btn.callback("Carry on", "manage.form", {}, owner_id=draft.telegram_id),
                rich.Btn.callback(
                    "Throw it away", "manage.restart", {"id": instead},
                    owner_id=draft.telegram_id,
                ),
            ]
        ],
        reply_to=reply_id(event),
        owner_id=draft.telegram_id,
    )


@command("edit", "Change an app that is already listed", manager_only=True, weight=27)
async def handle_edit(event: Any, args: str, ctx: Ctx) -> None:
    role = await _require_role(event, ctx)
    if role is None:
        return
    await _show_picker(event, ctx, role, next_step="edit", query=args.strip(), page=0)


@command("publish", "Publish an app, or take one back to a draft",
         manager_only=True, weight=28)
async def handle_publish(event: Any, args: str, ctx: Ctx) -> None:
    role = await _require_role(event, ctx)
    if role is None:
        return
    await _show_picker(event, ctx, role, next_step="publish", query=args.strip(), page=0)


@command("delete", "Remove an app from the directory", manager_only=True, weight=29)
async def handle_delete(event: Any, args: str, ctx: Ctx) -> None:
    role = await _require_role(event, ctx)
    if role is None:
        return
    if not role.can_delete:
        await rich.send_rich_message(
            ctx.client,
            event.chat_id,
            (
                "Deleting an app is an admin action on the portal, so it is one "
                "here too. An editor can take an app back to a draft with "
                "/publish, which hides it from the directory without losing it."
            ),
            title="Admins only",
            buttons=[[portal_button(ctx)]],
            reply_to=reply_id(event),
            owner_id=event.sender_id,
        )
        return
    await _show_picker(event, ctx, role, next_step="delete", query=args.strip(), page=0)


# --- picking an app --------------------------------------------------------


async def _fetch_manageable(ctx: Ctx, role: Role, telegram_id: int) -> list[dict[str, Any]]:
    """Everything this account may work on, drafts included.

    An editor sees the apps they created, because the portal refuses the rest,
    and offering a row that always ends in a refusal is worse than not offering
    it at all.
    """
    apps = await ctx.portal.list_all_apps(telegram_id)
    if role.is_admin:
        return apps
    return [a for a in apps if str(a.get("created_by") or "") == role.portal_user_id]


def _matches(app: dict[str, Any], needle: str) -> bool:
    from .apps import matches

    return matches(app, needle)


PICK_TITLES = {
    "edit": "Which app to edit",
    "publish": "Which app to publish or hide",
    "delete": "Which app to remove",
}


async def _show_picker(
    event: Any,
    ctx: Ctx,
    role: Role,
    *,
    next_step: str,
    query: str = "",
    page: int = 0,
    only: str = "",
    edit: Any = None,
) -> None:
    owner = event.sender_id
    try:
        apps = await _fetch_manageable(ctx, role, owner)
    except PortalUnavailable:
        await rich.send_rich_message(
            ctx.client, event.chat_id, rich.PORTAL_DOWN,
            reply_to=reply_id(event), owner_id=owner, edit=edit,
        )
        return
    except PortalError as exc:
        await _portal_refusal(event, ctx, exc, edit=edit)
        return

    if only == "drafts":
        apps = [a for a in apps if not a.get("published")]
    if query:
        apps = [a for a in apps if _matches(a, query.lower())]

    apps = sorted(apps, key=lambda a: (a.get("sort_order") or 0, str(a.get("title") or "").lower()))

    if not apps:
        await rich.send_rich_message(
            ctx.client,
            event.chat_id,
            (
                "Nothing here matches that."
                if query
                else "There is nothing here you can work on yet."
            ),
            title="Nothing to show",
            buttons=[
                [rich.Btn.callback("Add an app", "manage.new", {}, owner_id=owner)]
            ],
            reply_to=reply_id(event),
            owner_id=owner,
            edit=edit,
        )
        return

    pages = max(1, (len(apps) + PICK_PAGE_SIZE - 1) // PICK_PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    window = apps[page * PICK_PAGE_SIZE: page * PICK_PAGE_SIZE + PICK_PAGE_SIZE]

    lines = []
    rows: list[list[rich.Btn]] = []
    for offset, app in enumerate(window, start=1):
        number = page * PICK_PAGE_SIZE + offset
        state = "published" if app.get("published") else "draft"
        lines.append(
            f"{number}. <b>{rich.esc(app.get('title') or 'Untitled')}</b> "
            f"<i>{rich.esc(state)}</i>"
        )
        rows.append(
            [
                rich.Btn.callback(
                    f"{number}. {str(app.get('title') or 'Untitled')[:24]}",
                    "manage.pick",
                    {"id": str(app.get("id")), "next": next_step},
                    owner_id=owner,
                )
            ]
        )

    nav: list[rich.Btn] = []
    payload = {"next": next_step, "q": query, "only": only}
    if page > 0:
        nav.append(rich.Btn.callback("Previous", "manage.list", {**payload, "page": page - 1}, owner_id=owner))
    if page < pages - 1:
        nav.append(rich.Btn.callback("Next", "manage.list", {**payload, "page": page + 1}, owner_id=owner))
    if nav:
        rows.append(nav)

    await rich.send_rich_message(
        ctx.client,
        event.chat_id,
        "\n".join(lines),
        title=PICK_TITLES.get(next_step, "Pick an app"),
        footer=f"Page {page + 1} of {pages}, {len(apps)} app{'s' if len(apps) != 1 else ''}",
        buttons=rows,
        reply_to=reply_id(event),
        owner_id=owner,
        edit=edit,
    )


async def _find_app(ctx: Ctx, role: Role, telegram_id: int, app_id: str) -> dict[str, Any] | None:
    apps = await _fetch_manageable(ctx, role, telegram_id)
    return next((a for a in apps if str(a.get("id")) == str(app_id)), None)


def draft_from_app(telegram_id: int, app: dict[str, Any]) -> Draft:
    gallery = [str(g) for g in (app.get("gallery_urls") or [])]
    return Draft(
        telegram_id=telegram_id,
        app_id=str(app.get("id")),
        fields={
            "title": app.get("title") or "",
            "url": app.get("url") or "",
            "description": app.get("description") or "",
            "tags": [str(t) for t in (app.get("tags") or [])],
            "image": app.get("thumbnail_url") or (gallery[0] if gallery else ""),
            "gallery": gallery,
            "published_date": str(app.get("published_date") or "")[:10],
            "sort_order": app.get("sort_order") if app.get("sort_order") is not None else 0,
            "published": bool(app.get("published")),
        },
    )


# --- saving ----------------------------------------------------------------


async def _portal_refusal(event: Any, ctx: Ctx, exc: PortalError, edit: Any = None) -> None:
    """The portal said no. Its reason is the honest one, so it is the one shown."""
    explanations = {
        "not_linked": NEEDS_A_LINK,
        "not_allowed": NOT_A_MANAGER,
        "not_admin": "That one is admin only on the portal, so it is admin only here.",
        "not_yours": "That app was added by somebody else, so an admin has to make the change.",
        "not_found": "That app is not in the directory any more.",
    }
    body = explanations.get(exc.code or "", rich.esc(exc.message))
    await rich.send_rich_message(
        ctx.client, event.chat_id, body, title="Not done",
        buttons=[[portal_button(ctx)]],
        reply_to=reply_id(event), owner_id=event.sender_id, edit=edit,
    )


async def _save(event: Any, ctx: Ctx, draft: Draft, edit: Any = None) -> None:
    telegram_id = draft.telegram_id
    missing = draft.missing()
    if missing:
        await show_form(
            event,
            ctx,
            draft,
            note="Nothing was sent to the portal yet.",
            edit=edit,
        )
        return

    payload = draft.payload()
    if not draft.is_new and not payload:
        await show_form(event, ctx, draft, note="Nothing has changed yet.", edit=edit)
        return

    try:
        if draft.is_new:
            app = await ctx.portal.create_app(telegram_id, payload)
        else:
            app = await ctx.portal.update_app(telegram_id, str(draft.app_id), payload)
    except PortalUnavailable:
        await rich.send_rich_message(
            ctx.client,
            event.chat_id,
            f"{rich.PORTAL_DOWN}\n\nThe draft is still here, so try saving it again in a minute.",
            owner_id=telegram_id,
            edit=edit,
        )
        return
    except PortalError as exc:
        await _portal_refusal(event, ctx, exc, edit=edit)
        return

    await ctx.db.delete_app_draft(telegram_id)
    # The directory list is cached for a quarter of an hour, and somebody who
    # just saved an app should see it in /apps straight away.
    await ctx.cache.invalidate("apps:list")

    state = "published" if app.get("published") else "saved as a draft"
    what = "Added" if draft.is_new else "Updated"
    body = (
        f"{what} {rich.esc(app.get('title') or 'the app')}, {rich.esc(state)}.\n\n"
        + (
            "It is in the directory now."
            if app.get("published")
            else "It stays out of the directory until it is published."
        )
    )

    rows: list[list[rich.Btn]] = []
    url = str(app.get("url") or "")
    if url.startswith(("http://", "https://")):
        rows.append([rich.Btn.link("Open the app", url)])
    rows.append(
        [
            rich.Btn.callback(
                "Edit it again", "manage.pick",
                {"id": str(app.get("id")), "next": "edit"}, owner_id=telegram_id,
            )
        ]
    )

    await rich.send_rich_message(
        ctx.client, event.chat_id, body, title="Saved", buttons=rows,
        owner_id=telegram_id, edit=edit,
    )


# --- callbacks -------------------------------------------------------------


async def _draft_or_complain(event: Any, ctx: Ctx) -> Draft | None:
    draft = await load_draft(ctx, event.sender_id)
    if draft is None:
        await event.answer("That draft is gone. Run /add to start another.", alert=True)
    return draft


@callbacks.action("manage.new")
async def _cb_new(event: Any, payload: dict[str, Any], ctx: Ctx) -> None:
    await event.answer()
    role = await _require_role(event, ctx)
    if role is None:
        return
    existing = await load_draft(ctx, event.sender_id)
    if existing is not None:
        await _offer_the_draft(event, ctx, existing)
        return
    await show_form(
        event, ctx, Draft(telegram_id=event.sender_id), edit=await event.get_message()
    )


@callbacks.action("manage.restart")
async def _cb_restart(event: Any, payload: dict[str, Any], ctx: Ctx) -> None:
    await event.answer("Thrown away.")
    await ctx.db.delete_app_draft(event.sender_id)
    message = await event.get_message()

    app_id = str(payload.get("id") or "")
    if not app_id:
        await show_form(event, ctx, Draft(telegram_id=event.sender_id), edit=message)
        return

    # The old draft was in the way of opening this app, so land on it.
    role = await _require_role(event, ctx)
    if role is None:
        return
    try:
        app = await _find_app(ctx, role, event.sender_id, app_id)
    except PortalUnavailable:
        await rich.send_rich_message(
            ctx.client, event.chat_id, rich.PORTAL_DOWN, owner_id=event.sender_id, edit=message
        )
        return
    except PortalError as exc:
        await _portal_refusal(event, ctx, exc, edit=message)
        return

    if app is None:
        await show_form(event, ctx, Draft(telegram_id=event.sender_id), edit=message)
        return
    await show_form(event, ctx, draft_from_app(event.sender_id, app), edit=message)


@callbacks.action("manage.form")
async def _cb_form(event: Any, payload: dict[str, Any], ctx: Ctx) -> None:
    await event.answer()
    draft = await _draft_or_complain(event, ctx)
    if draft is None:
        return
    draft.awaiting = None
    await show_form(event, ctx, draft, edit=await event.get_message())


@callbacks.action("manage.field")
async def _cb_field(event: Any, payload: dict[str, Any], ctx: Ctx) -> None:
    draft = await _draft_or_complain(event, ctx)
    if draft is None:
        return
    f = BY_KEY.get(str(payload.get("f") or ""))
    if f is None:
        await event.answer("That field is not one this form has.", alert=True)
        return
    await event.answer()
    await _ask_for(event, ctx, draft, f, edit=await event.get_message())


@callbacks.action("manage.clear")
async def _cb_clear(event: Any, payload: dict[str, Any], ctx: Ctx) -> None:
    draft = await _draft_or_complain(event, ctx)
    if draft is None:
        return
    f = BY_KEY.get(str(payload.get("f") or ""))
    if f is None or f.required:
        await event.answer("That one cannot be left empty.", alert=True)
        return
    await event.answer("Left empty.")
    draft.set(f.key, [] if f.kind == "tags" else "")
    draft.awaiting = None
    await show_form(
        event, ctx, draft,
        note=f"Left the {rich.esc(f.label.lower())} empty.",
        edit=await event.get_message(),
    )


@callbacks.action("manage.tag")
async def _cb_tag(event: Any, payload: dict[str, Any], ctx: Ctx) -> None:
    draft = await _draft_or_complain(event, ctx)
    if draft is None:
        return
    tag = str(payload.get("t") or "")
    if tag not in ALLOWED_TAGS:
        await event.answer("That tag is not one the directory uses.", alert=True)
        return

    chosen = [str(t) for t in (draft.fields.get("tags") or [])]
    if tag in chosen:
        chosen.remove(tag)
        await event.answer(f"{tag} is off.")
    else:
        chosen.append(tag)
        await event.answer(f"{tag} is on.")
    draft.set("tags", chosen)
    await _ask_for_tags(event, ctx, draft, edit=await event.get_message())


@callbacks.action("manage.visibility")
async def _cb_visibility(event: Any, payload: dict[str, Any], ctx: Ctx) -> None:
    draft = await _draft_or_complain(event, ctx)
    if draft is None:
        return
    now_published = not draft.fields.get("published")
    draft.set("published", now_published)
    await event.answer("It will be published." if now_published else "It will stay a draft.")
    await show_form(event, ctx, draft, edit=await event.get_message())


@callbacks.action("manage.discard")
async def _cb_discard(event: Any, payload: dict[str, Any], ctx: Ctx) -> None:
    await event.answer("Thrown away.")
    await ctx.db.delete_app_draft(event.sender_id)
    await rich.send_rich_message(
        ctx.client,
        event.chat_id,
        "That draft is gone. Nothing in the directory changed.",
        title="Discarded",
        buttons=[
            [rich.Btn.callback("Add an app", "manage.new", {}, owner_id=event.sender_id)]
        ],
        owner_id=event.sender_id,
        edit=await event.get_message(),
    )


@callbacks.action("manage.save")
async def _cb_save(event: Any, payload: dict[str, Any], ctx: Ctx) -> None:
    await event.answer()
    draft = await _draft_or_complain(event, ctx)
    if draft is None:
        return
    role = await _require_role(event, ctx)
    if role is None:
        return
    await _save(event, ctx, draft, edit=await event.get_message())


@callbacks.action("manage.list")
async def _cb_list(event: Any, payload: dict[str, Any], ctx: Ctx) -> None:
    await event.answer()
    role = await _require_role(event, ctx)
    if role is None:
        return
    await _show_picker(
        event,
        ctx,
        role,
        next_step=str(payload.get("next") or "edit"),
        query=str(payload.get("q") or ""),
        page=int(payload.get("page") or 0),
        only=str(payload.get("only") or ""),
        edit=await event.get_message(),
    )


@callbacks.action("manage.pick")
async def _cb_pick(event: Any, payload: dict[str, Any], ctx: Ctx) -> None:
    await event.answer()
    role = await _require_role(event, ctx)
    if role is None:
        return

    message = await event.get_message()
    app_id = str(payload.get("id") or "")
    try:
        app = await _find_app(ctx, role, event.sender_id, app_id)
    except PortalUnavailable:
        await rich.send_rich_message(
            ctx.client, event.chat_id, rich.PORTAL_DOWN, owner_id=event.sender_id, edit=message
        )
        return
    except PortalError as exc:
        await _portal_refusal(event, ctx, exc, edit=message)
        return

    if app is None:
        await rich.send_rich_message(
            ctx.client,
            event.chat_id,
            "That app is not in the directory any more, or is not yours to change.",
            title="Not found",
            owner_id=event.sender_id,
            edit=message,
        )
        return

    step = str(payload.get("next") or "edit")
    if step == "publish":
        await _confirm_publish(event, ctx, app, edit=message)
        return
    if step == "delete":
        await _confirm_delete(event, ctx, app, edit=message)
        return

    existing = await load_draft(ctx, event.sender_id)
    if existing is not None and existing.app_id != app_id:
        await _offer_the_draft(event, ctx, existing, instead=app_id)
        return

    draft = existing if existing is not None else draft_from_app(event.sender_id, app)
    await show_form(event, ctx, draft, edit=message)


# --- publishing and removing ------------------------------------------------


async def _confirm_publish(event: Any, ctx: Ctx, app: dict[str, Any], edit: Any = None) -> None:
    owner = event.sender_id
    published = bool(app.get("published"))
    title = rich.esc(app.get("title") or "that app")
    body = (
        f"{title} is published and visible in the directory."
        if published
        else f"{title} is a draft, so nobody else can see it yet."
    )
    label = "Take it back to a draft" if published else "Publish it now"
    await rich.send_rich_message(
        ctx.client,
        event.chat_id,
        body,
        title="Visibility",
        buttons=[
            [
                rich.Btn.callback(
                    label, "manage.visibility_set",
                    {"id": str(app.get("id")), "on": not published}, owner_id=owner,
                )
            ],
            [rich.Btn.callback("Leave it as it is", "manage.nothing", {}, owner_id=owner)],
        ],
        owner_id=owner,
        edit=edit,
    )


@callbacks.action("manage.visibility_set")
async def _cb_visibility_set(event: Any, payload: dict[str, Any], ctx: Ctx) -> None:
    await event.answer()
    role = await _require_role(event, ctx)
    if role is None:
        return

    message = await event.get_message()
    app_id = str(payload.get("id") or "")
    on = bool(payload.get("on"))
    try:
        app = await ctx.portal.update_app(event.sender_id, app_id, {"published": on})
    except PortalUnavailable:
        await rich.send_rich_message(
            ctx.client, event.chat_id, rich.PORTAL_DOWN, owner_id=event.sender_id, edit=message
        )
        return
    except PortalError as exc:
        await _portal_refusal(event, ctx, exc, edit=message)
        return

    await ctx.cache.invalidate("apps:list")
    title = rich.esc(app.get("title") or "That app")
    body = (
        f"{title} is published and in the directory now."
        if on
        else f"{title} is back to a draft and out of the directory."
    )
    await rich.send_rich_message(
        ctx.client, event.chat_id, body, title="Done",
        owner_id=event.sender_id, edit=message,
    )


async def _confirm_delete(event: Any, ctx: Ctx, app: dict[str, Any], edit: Any = None) -> None:
    owner = event.sender_id
    title = rich.esc(app.get("title") or "that app")
    await rich.send_rich_message(
        ctx.client,
        event.chat_id,
        (
            f"Remove {title} from the directory?\n\n"
            "This cannot be undone from here. Taking it back to a draft hides it "
            "just as well and keeps the row, so prefer that unless the app is "
            "really finished with."
        ),
        title="Remove an app",
        buttons=[
            [
                rich.Btn.callback(
                    "Yes, remove it", "manage.delete_confirmed",
                    {"id": str(app.get("id"))}, owner_id=owner, max_uses=1,
                )
            ],
            [
                rich.Btn.callback(
                    "Take it back to a draft", "manage.visibility_set",
                    {"id": str(app.get("id")), "on": False}, owner_id=owner,
                ),
                rich.Btn.callback("Keep it", "manage.nothing", {}, owner_id=owner),
            ],
        ],
        owner_id=owner,
        edit=edit,
    )


@callbacks.action("manage.delete_confirmed")
async def _cb_delete(event: Any, payload: dict[str, Any], ctx: Ctx) -> None:
    await event.answer()
    role = await _require_role(event, ctx)
    if role is None:
        return

    message = await event.get_message()
    try:
        title = await ctx.portal.delete_app(event.sender_id, str(payload.get("id") or ""))
    except PortalUnavailable:
        await rich.send_rich_message(
            ctx.client, event.chat_id, rich.PORTAL_DOWN, owner_id=event.sender_id, edit=message
        )
        return
    except PortalError as exc:
        await _portal_refusal(event, ctx, exc, edit=message)
        return

    await ctx.cache.invalidate("apps:list")
    await rich.send_rich_message(
        ctx.client,
        event.chat_id,
        f"{rich.esc(title or 'That app')} is no longer in the directory.",
        title="Removed",
        owner_id=event.sender_id,
        edit=message,
    )


@callbacks.action("manage.nothing")
async def _cb_nothing(event: Any, payload: dict[str, Any], ctx: Ctx) -> None:
    await event.answer("Left as it is.")
    await rich.send_rich_message(
        ctx.client,
        event.chat_id,
        "Nothing changed.",
        owner_id=event.sender_id,
        edit=await event.get_message(),
    )
