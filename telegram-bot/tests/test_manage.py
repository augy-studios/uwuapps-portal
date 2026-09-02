"""Managing the directory from a chat.

Two properties matter more than the rest, and most of this file is about them.

1. The commands exist only for an operator or for a linked account that holds
   the editor or admin role. Everybody else gets the ordinary unknown command
   reply, so the surface does not announce itself.
2. Nothing local ever authorises a write. Every save reaches the portal, and a
   refusal from the portal is what the person sees.
"""

from __future__ import annotations

import pytest

from bot import handlers
from bot.handlers import manage as manage_handler
from bot.handlers import start as start_handler
from bot.services.portal import PortalError, PortalUnavailable

from .conftest import FakeCallbackEvent, FakeEvent, SentMessage, account

EDITOR = 42
OPERATOR = 999  # in ADMIN_TELEGRAM_IDS, see conftest

APPS = [
    {
        "id": "a1",
        "title": "Wordle",
        "description": "Guess the word",
        "url": "https://wordle.test",
        "tags": ["games"],
        "gallery_urls": ["https://img.test/1.png", "https://img.test/2.png"],
        "thumbnail_url": "https://img.test/1.png",
        "published": True,
        "sort_order": 1,
        "created_by": "user-1",
    },
    {
        "id": "a2",
        "title": "Invoice Maker",
        "url": "https://invoice.test",
        "tags": ["tools"],
        "published": False,
        "sort_order": 2,
        "created_by": "user-2",
    },
]


async def _as(ctx, telegram_id=EDITOR, **role):
    """Link the chat and say what the portal thinks of the account."""
    await ctx.db.touch_user(telegram_id, "tester", "Test", "en")
    await ctx.db.upsert_link(telegram_id, "user-1", "tester", "Tester", False)
    ctx.portal.account = account(**role)
    await ctx.cache.invalidate(f"role:{telegram_id}")


def _labels(sent) -> list[str]:
    return [getattr(b, "text", "") for row in (sent.buttons or []) for b in row]


def _last(ctx):
    return ctx.client.sent[-1] if ctx.client.sent else None


# --- who gets in -----------------------------------------------------------


async def test_add_answers_like_an_unknown_command_when_nothing_is_linked(ctx):
    await handlers.handle_message(FakeEvent("/add"), ctx)

    assert "do not know that one" in _last(ctx).text
    # A chat with no link costs no portal call, which is what keeps /start cheap
    assert ctx.portal.calls == []


async def test_add_answers_like_an_unknown_command_for_a_viewer(ctx):
    """Linked, approved, but neither an editor nor an admin."""
    await _as(ctx, is_editor=False, is_admin=False)

    await handlers.handle_message(FakeEvent("/add"), ctx)

    assert "do not know that one" in _last(ctx).text


async def test_add_answers_like_an_unknown_command_for_an_unapproved_account(ctx):
    await _as(ctx, is_editor=True, is_approved=False)

    await handlers.handle_message(FakeEvent("/add"), ctx)

    assert "do not know that one" in _last(ctx).text


async def test_an_editor_gets_the_form(ctx):
    await _as(ctx)

    await handlers.handle_message(FakeEvent("/add"), ctx)

    sent = _last(ctx)
    assert "New app" in sent.text
    assert "Title" in _labels(sent)
    assert "Link" in _labels(sent)


async def test_an_operator_with_no_link_is_told_to_link_rather_than_ignored(ctx):
    """The gate lets an operator through, and the reply explains the rest."""
    await handlers.handle_message(FakeEvent("/add", sender_id=OPERATOR), ctx)

    body = _last(ctx).text
    assert "not linked" in body
    assert "/link" in body


async def test_the_command_list_hides_management_from_ordinary_users(ctx):
    await start_handler.handle_start(FakeEvent("/start"), "", ctx)
    assert "/add" not in _last(ctx).text


async def test_the_command_list_shows_management_to_an_editor(ctx):
    await _as(ctx)
    await start_handler.handle_start(FakeEvent("/start"), "", ctx)

    body = _last(ctx).text
    for name in ("/manage", "/add", "/edit", "/publish"):
        assert name in body


def test_the_management_commands_stay_out_of_the_public_lists():
    listed = {line.split(" - ")[0] for line in handlers.botfather_block().splitlines()}
    for name in ("manage", "add", "edit", "publish", "delete"):
        assert name not in listed
        assert f"/{name} " not in handlers.command_list_html()


# --- filling the form ------------------------------------------------------


async def _open_form(ctx, telegram_id=EDITOR):
    await manage_handler.handle_add(FakeEvent("/add", sender_id=telegram_id), "", ctx)
    return await manage_handler.load_draft(ctx, telegram_id)


async def test_a_typed_value_lands_in_the_draft_rather_than_in_a_search(ctx):
    await _as(ctx)
    draft = await _open_form(ctx)
    draft.awaiting = "title"
    await manage_handler.save_draft(ctx, draft)

    handled = await manage_handler.consume_pending(FakeEvent("Tip Splitter"), "Tip Splitter", ctx)

    assert handled is True
    stored = await manage_handler.load_draft(ctx, EDITOR)
    assert stored.fields["title"] == "Tip Splitter"
    assert stored.awaiting is None


async def test_free_text_with_no_draft_waiting_is_left_for_the_search(ctx):
    await _as(ctx)
    await _open_form(ctx)

    assert await manage_handler.consume_pending(FakeEvent("wordle"), "wordle", ctx) is False


async def test_an_address_that_is_not_one_is_refused_and_the_field_stays_open(ctx):
    await _as(ctx)
    draft = await _open_form(ctx)
    draft.awaiting = "url"
    await manage_handler.save_draft(ctx, draft)

    await manage_handler.consume_pending(FakeEvent("wordle.test"), "wordle.test", ctx)

    assert "http" in _last(ctx).text
    stored = await manage_handler.load_draft(ctx, EDITOR)
    assert stored.awaiting == "url"
    assert "url" not in stored.fields


async def test_a_date_has_to_be_a_date(ctx):
    await _as(ctx)
    draft = await _open_form(ctx)
    draft.awaiting = "published_date"
    await manage_handler.save_draft(ctx, draft)

    await manage_handler.consume_pending(FakeEvent("yesterday"), "yesterday", ctx)
    assert (await manage_handler.load_draft(ctx, EDITOR)).awaiting == "published_date"

    await manage_handler.consume_pending(FakeEvent("2026-09-02"), "2026-09-02", ctx)
    stored = await manage_handler.load_draft(ctx, EDITOR)
    assert stored.fields["published_date"] == "2026-09-02"


async def test_the_draft_outlives_the_process(ctx):
    """It lives in SQLite, so reloading it is the whole test."""
    await _as(ctx)
    draft = await _open_form(ctx)
    draft.set("title", "Tip Splitter")
    await manage_handler.save_draft(ctx, draft)

    reloaded = await manage_handler.load_draft(ctx, EDITOR)
    assert reloaded.fields["title"] == "Tip Splitter"
    assert reloaded.message_id is not None


async def test_only_one_draft_is_kept_and_the_second_add_offers_it(ctx):
    await _as(ctx)
    draft = await _open_form(ctx)
    draft.set("title", "Tip Splitter")
    await manage_handler.save_draft(ctx, draft)

    await manage_handler.handle_add(FakeEvent("/add"), "", ctx)

    sent = _last(ctx)
    assert "Tip Splitter" in sent.text
    assert "Carry on" in _labels(sent)


async def test_throwing_the_open_draft_away_lands_on_the_app_that_was_wanted(ctx):
    """The old draft was in the way. Discarding it must not lose the intent."""
    await _as(ctx, is_admin=True)
    ctx.portal.all_apps = APPS
    draft = await _open_form(ctx)
    draft.set("title", "Something else")
    await manage_handler.save_draft(ctx, draft)

    await manage_handler._cb_pick(_pressed(ctx), {"id": "a1", "next": "edit"}, ctx)
    assert "Carry on" in _labels(_last(ctx))

    await manage_handler._cb_restart(_pressed(ctx), {"id": "a1"}, ctx)

    stored = await manage_handler.load_draft(ctx, EDITOR)
    assert stored.app_id == "a1"
    assert stored.fields["title"] == "Wordle"


# --- the buttons, end to end -----------------------------------------------


def _pressed(ctx, telegram_id=EDITOR):
    """A press on the message the draft is currently drawn on."""
    return FakeCallbackEvent("cb:x", sender_id=telegram_id, message=SentMessage(telegram_id, "", None))


def _edited(ctx) -> str:
    return ctx.client.edits[-1][2]


async def test_the_hub_offers_the_ways_in(ctx):
    await _as(ctx)

    await manage_handler.handle_manage(FakeEvent("/manage"), "", ctx)

    labels = _labels(_last(ctx))
    assert "Add an app" in labels
    assert "Edit an app" in labels
    assert "Unpublished apps" in labels
    # Removing is an admin action, so an editor is not offered it
    assert "Remove an app" not in labels


async def test_the_hub_offers_removing_to_an_admin(ctx):
    await _as(ctx, is_admin=True)

    await manage_handler.handle_manage(FakeEvent("/manage"), "", ctx)

    assert "Remove an app" in _labels(_last(ctx))


async def test_pressing_a_field_asks_for_it_on_the_same_message(ctx):
    await _as(ctx)
    await _open_form(ctx)

    await manage_handler._cb_field(_pressed(ctx), {"f": "title"}, ctx)

    assert "Send the title" in _edited(ctx)
    assert (await manage_handler.load_draft(ctx, EDITOR)).awaiting == "title"


async def test_a_tag_press_toggles_it_and_shows_the_new_state(ctx):
    await _as(ctx)
    await _open_form(ctx)

    await manage_handler._cb_tag(_pressed(ctx), {"t": "tools"}, ctx)
    assert (await manage_handler.load_draft(ctx, EDITOR)).fields["tags"] == ["tools"]

    await manage_handler._cb_tag(_pressed(ctx), {"t": "tools"}, ctx)
    assert (await manage_handler.load_draft(ctx, EDITOR)).fields["tags"] == []


async def test_a_tag_the_directory_does_not_use_is_refused(ctx):
    await _as(ctx)
    await _open_form(ctx)

    event = _pressed(ctx)
    await manage_handler._cb_tag(event, {"t": "cooking"}, ctx)

    assert event.answers[-1][1] is True
    assert "tags" not in (await manage_handler.load_draft(ctx, EDITOR)).fields


async def test_the_visibility_button_flips_the_draft_only(ctx):
    await _as(ctx)
    await _open_form(ctx)

    await manage_handler._cb_visibility(_pressed(ctx), {}, ctx)

    assert (await manage_handler.load_draft(ctx, EDITOR)).fields["published"] is True
    assert not [c for c in ctx.portal.calls if c[0].startswith("app")]


async def test_discarding_throws_the_draft_away_and_touches_nothing_else(ctx):
    await _as(ctx)
    await _open_form(ctx)

    await manage_handler._cb_discard(_pressed(ctx), {}, ctx)

    assert await manage_handler.load_draft(ctx, EDITOR) is None
    assert "Nothing in the directory changed" in _edited(ctx)


async def test_a_button_from_a_gone_draft_says_so_rather_than_failing(ctx):
    await _as(ctx)

    event = _pressed(ctx)
    await manage_handler._cb_field(event, {"f": "title"}, ctx)

    assert event.answers[-1][1] is True


# --- saving ----------------------------------------------------------------


async def _save(ctx, draft, telegram_id=EDITOR):
    await manage_handler.save_draft(ctx, draft)
    event = FakeCallbackEvent("cb:x", sender_id=telegram_id)
    await manage_handler._cb_save(event, {}, ctx)
    return event


async def test_a_save_without_the_required_fields_never_reaches_the_portal(ctx):
    await _as(ctx)
    draft = await _open_form(ctx)
    draft.set("title", "Tip Splitter")

    await _save(ctx, draft)

    assert not [c for c in ctx.portal.calls if c[0] == "create_app"]
    assert "Still needed" in _last(ctx).text


async def test_a_new_app_carries_every_field_and_is_a_draft_unless_published(ctx):
    await _as(ctx)
    draft = await _open_form(ctx)
    draft.set("title", "Tip Splitter")
    draft.set("url", "https://tips.test")
    draft.set("tags", ["tools"])
    draft.set("image", "https://img.test/tips.png")

    await _save(ctx, draft)

    call = next(c for c in ctx.portal.calls if c[0] == "create_app")
    payload = call[1][1]
    assert payload["title"] == "Tip Splitter"
    assert payload["url"] == "https://tips.test"
    assert payload["tags"] == ["tools"]
    assert payload["galleryUrls"] == ["https://img.test/tips.png"]
    assert payload["published"] is False


async def test_saving_clears_the_draft_and_the_cached_directory(ctx):
    await _as(ctx)
    await ctx.cache.write("apps:list", [], 900)
    draft = await _open_form(ctx)
    draft.set("title", "Tip Splitter")
    draft.set("url", "https://tips.test")

    await _save(ctx, draft)

    assert await manage_handler.load_draft(ctx, EDITOR) is None
    assert await ctx.cache.read("apps:list") is None


async def test_an_edit_sends_only_what_changed(ctx):
    """An untouched gallery is left alone rather than overwritten with one image."""
    await _as(ctx)
    ctx.portal.all_apps = APPS
    draft = manage_handler.draft_from_app(EDITOR, APPS[0])
    draft.set("description", "Guess the five letter word")

    await _save(ctx, draft)

    call = next(c for c in ctx.portal.calls if c[0] == "update_app")
    _, app_id, payload = call[1]
    assert app_id == "a1"
    assert payload == {"description": "Guess the five letter word"}


async def test_an_edit_with_nothing_changed_says_so(ctx):
    await _as(ctx)
    draft = manage_handler.draft_from_app(EDITOR, APPS[0])

    await _save(ctx, draft)

    assert not [c for c in ctx.portal.calls if c[0] == "update_app"]
    assert "Nothing has changed" in _last(ctx).text


async def test_a_portal_outage_keeps_the_draft(ctx):
    await _as(ctx)
    draft = await _open_form(ctx)
    draft.set("title", "Tip Splitter")
    draft.set("url", "https://tips.test")
    ctx.portal.writes_raise = PortalUnavailable("down")

    await _save(ctx, draft)

    assert await manage_handler.load_draft(ctx, EDITOR) is not None
    assert "not responding" in _last(ctx).text


async def test_a_refusal_from_the_portal_is_explained_in_plain_words(ctx):
    await _as(ctx)
    draft = manage_handler.draft_from_app(EDITOR, APPS[1])
    draft.set("title", "Invoice Maker 2")
    ctx.portal.writes_raise = PortalError("nope", 403, "not_yours")

    await _save(ctx, draft)

    assert "somebody else" in _last(ctx).text


# --- picking, publishing and removing --------------------------------------


async def test_an_editor_is_only_offered_the_apps_they_created(ctx):
    await _as(ctx)
    ctx.portal.all_apps = APPS

    await manage_handler.handle_edit(FakeEvent("/edit"), "", ctx)

    body = _last(ctx).text
    assert "Wordle" in body
    assert "Invoice Maker" not in body


async def test_an_admin_is_offered_all_of_them(ctx):
    await _as(ctx, is_admin=True)
    ctx.portal.all_apps = APPS

    await manage_handler.handle_edit(FakeEvent("/edit"), "", ctx)

    body = _last(ctx).text
    assert "Wordle" in body
    assert "Invoice Maker" in body


async def test_the_picker_takes_a_search_term(ctx):
    await _as(ctx, is_admin=True)
    ctx.portal.all_apps = APPS

    await manage_handler.handle_edit(FakeEvent("/edit invoice"), "invoice", ctx)

    body = _last(ctx).text
    assert "Invoice Maker" in body
    assert "Wordle" not in body


async def test_publishing_flips_only_that_one_column(ctx):
    await _as(ctx)
    ctx.portal.all_apps = APPS

    event = FakeCallbackEvent("cb:x")
    await manage_handler._cb_visibility_set(event, {"id": "a1", "on": False}, ctx)

    call = next(c for c in ctx.portal.calls if c[0] == "update_app")
    assert call[1][2] == {"published": False}


async def test_delete_is_refused_for_an_editor_and_offers_the_alternative(ctx):
    await _as(ctx)
    ctx.portal.all_apps = APPS

    await manage_handler.handle_delete(FakeEvent("/delete"), "", ctx)

    body = _last(ctx).text
    assert "admin" in body.lower()
    assert "/publish" in body
    assert not [c for c in ctx.portal.calls if c[0] == "delete_app"]


async def test_delete_asks_before_it_removes_anything(ctx):
    await _as(ctx, is_admin=True)
    ctx.portal.all_apps = APPS

    await manage_handler._cb_pick(FakeCallbackEvent("cb:x"), {"id": "a1", "next": "delete"}, ctx)

    labels = _labels(_last(ctx))
    assert "Yes, remove it" in labels
    assert "Take it back to a draft" in labels
    assert not [c for c in ctx.portal.calls if c[0] == "delete_app"]


async def test_the_confirmed_delete_goes_through_and_clears_the_cache(ctx):
    await _as(ctx, is_admin=True)
    await ctx.cache.write("apps:list", [], 900)

    await manage_handler._cb_delete(FakeCallbackEvent("cb:x"), {"id": "a1"}, ctx)

    assert ("delete_app", (EDITOR, "a1")) in ctx.portal.calls
    assert await ctx.cache.read("apps:list") is None
