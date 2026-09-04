"""Acceptance criteria 2, 7, 8, 13 and 14, plus the bot to portal headers.

2.  /start, /link, /unlink and /code behave as specified.
7.  Every command still answers usefully while the portal is unreachable.
8.  Plain text searches the directory, and never while a link flow is waiting.
13. /code right after an automatic code leaves exactly one live code, and says so.
14. Every code message offers a copy button, and a client without support for
    one still gets a working message with the digits in a tap to copy block.
"""

from __future__ import annotations

import hashlib
import hmac

import pytest

from bot import rich
from bot.handlers import apps as apps_handler
from bot.handlers import link as link_handler
from bot.handlers import mfa as mfa_handler
from bot.handlers import misc as misc_handler
from bot.handlers import start as start_handler
from bot.services import signing
from bot.services.portal import IssuedCode, PortalError, PortalUnavailable

from .conftest import FakeCallbackEvent, FakeEvent

APPS = [
    {
        "id": "a1",
        "title": "Wordle",
        "description": "Guess the word",
        "url": "https://wordle.test",
        "tags": ["games"],
        "published": True,
        "published_date": "2026-02-01",
        "sort_order": 1,
    },
    {
        "id": "a2",
        "title": "Invoice Maker",
        "description": "Make an invoice",
        "url": "https://invoice.test",
        "tags": ["tools"],
        "published": True,
        "published_date": "2026-03-01",
        "sort_order": 2,
    },
    {
        "id": "a3",
        "title": "Tip Splitter",
        "description": "Split a bill in Singapore",
        "url": "https://tips.test",
        "tags": ["tools", "singapore"],
        "published": True,
        "published_date": "2026-01-01",
        "sort_order": 3,
    },
]


# --- /start ----------------------------------------------------------------


async def test_start_carries_the_whole_command_list(ctx):
    await start_handler.handle_start(FakeEvent("/start"), "", ctx)
    body = ctx.client.sent[-1].text
    for name in ("/link", "/code", "/browse"):
        assert name in body


async def test_start_offers_linking_before_a_link_exists(ctx):
    await start_handler.handle_start(FakeEvent("/start"), "", ctx)
    labels = _labels(ctx.client.sent[-1])
    assert "Link my account" in labels
    assert "My account" not in labels


async def test_start_offers_the_account_once_linked(ctx):
    await ctx.db.touch_user(42, "tester", "Test", "en")
    await ctx.db.upsert_link(42, "user-1", "tester", "Tester", False)

    await start_handler.handle_start(FakeEvent("/start"), "", ctx)
    labels = _labels(ctx.client.sent[-1])
    assert "My account" in labels
    assert "Link my account" not in labels


async def test_start_hides_the_admin_commands_from_ordinary_users(ctx):
    await start_handler.handle_start(FakeEvent("/start", sender_id=42), "", ctx)
    assert "/stats" not in ctx.client.sent[-1].text

    await start_handler.handle_start(FakeEvent("/start", sender_id=999), "", ctx)
    assert "/stats" in ctx.client.sent[-1].text


async def test_the_deep_link_payload_redeems_a_code(ctx):
    event = FakeEvent("/start link_ABCD2345")
    await start_handler.handle_start(event, "link_ABCD2345", ctx)

    assert ("redeem_link_code", ("ABCD2345", 42, "tester")) in ctx.portal.calls
    assert await ctx.db.get_link(42) is not None


# --- /link -----------------------------------------------------------------


async def test_link_with_no_argument_explains_where_a_code_comes_from(ctx):
    await link_handler.handle_link(FakeEvent("/link"), "", ctx)
    body = ctx.client.sent[-1].text
    assert "Settings" in body
    assert link_handler.is_awaiting(42)


async def test_link_never_echoes_the_code_back(ctx):
    """Chats get forwarded, so the code must not survive in the reply."""
    await link_handler.handle_link(FakeEvent("/link ABCD2345"), "ABCD2345", ctx)
    assert "ABCD2345" not in ctx.client.sent[-1].text


async def test_a_malformed_code_is_refused_without_calling_the_portal(ctx):
    await link_handler.handle_link(FakeEvent("/link nope"), "nope", ctx)
    assert ctx.portal.calls == []
    assert "not look like" in ctx.client.sent[-1].text


async def test_five_failures_pause_linking_for_an_hour(ctx):
    for _ in range(5):
        await ctx.db.record_link_attempt(42, succeeded=False)

    await link_handler.handle_link(FakeEvent("/link ABCD2345"), "ABCD2345", ctx)

    assert ctx.portal.calls == []
    assert "paused" in ctx.client.sent[-1].text.lower()


async def test_an_already_linked_user_is_told_which_account(ctx):
    await ctx.db.touch_user(42, "tester", "Test", "en")
    await ctx.db.upsert_link(42, "user-1", "tester", "Tester", False)

    await link_handler.handle_link(FakeEvent("/link"), "", ctx)

    body = ctx.client.sent[-1].text
    assert "Tester" in body
    assert "Settings" in body


# --- criterion 8, free text is a search ------------------------------------


async def test_plain_text_searches_the_directory(ctx):
    ctx.portal.apps = APPS
    await apps_handler.search(FakeEvent("wordle"), "wordle", ctx)
    assert "Wordle" in ctx.client.sent[-1].text


async def test_a_single_match_opens_the_card_directly(ctx):
    ctx.portal.apps = APPS
    await apps_handler.search(FakeEvent("wordle"), "wordle", ctx)
    sent = ctx.client.sent[-1]
    assert "Guess the word" in sent.text
    assert "Open the app" in _labels(sent)


async def test_no_matches_offers_a_way_onward(ctx):
    ctx.portal.apps = APPS
    await apps_handler.search(FakeEvent("nothing here"), "nothing here", ctx)
    labels = _labels(ctx.client.sent[-1])
    assert "Browse everything" in labels
    assert "See what is new" in labels


async def test_very_short_input_asks_for_more(ctx):
    ctx.portal.apps = APPS
    await apps_handler.search(FakeEvent("wo"), "wo", ctx)
    assert "short" in ctx.client.sent[-1].text.lower()
    assert ctx.portal.calls == []


async def test_search_treats_the_input_as_a_literal_not_a_pattern(ctx):
    ctx.portal.apps = APPS
    await apps_handler.search(FakeEvent(".*"), "wor.*dle", ctx)
    assert "No matches" in ctx.client.sent[-1].text or "match" in ctx.client.sent[-1].text


async def test_a_waiting_link_flow_takes_priority_over_search(ctx):
    await link_handler.handle_link(FakeEvent("/link"), "", ctx)
    ctx.client.sent.clear()

    handled = await link_handler.consume_pending(FakeEvent("ABCD2345"), "ABCD2345", ctx)

    assert handled is True
    assert ("redeem_link_code", ("ABCD2345", 42, "tester")) in ctx.portal.calls


async def test_ordinary_text_during_a_link_flow_falls_through_to_search(ctx):
    await link_handler.handle_link(FakeEvent("/link"), "", ctx)

    handled = await link_handler.consume_pending(FakeEvent("wordle"), "wordle", ctx)

    assert handled is False
    assert not link_handler.is_awaiting(42)


# --- /browse and the sort button -------------------------------------------


def _titles(selected) -> list[str]:
    return [a["title"] for a in selected]


def test_each_order_arranges_the_directory_its_own_way():
    order = lambda sort: _titles(apps_handler.select(APPS, "all", "", sort))  # noqa: E731

    assert order(apps_handler.SORT_DEFAULT) == ["Wordle", "Invoice Maker", "Tip Splitter"]
    assert order(apps_handler.SORT_AZ) == ["Invoice Maker", "Tip Splitter", "Wordle"]
    assert order(apps_handler.SORT_ZA) == ["Wordle", "Tip Splitter", "Invoice Maker"]
    assert order(apps_handler.SORT_NEWEST) == ["Invoice Maker", "Wordle", "Tip Splitter"]
    assert order(apps_handler.SORT_OLDEST) == ["Tip Splitter", "Wordle", "Invoice Maker"]


def test_an_app_with_no_date_sorts_last_whichever_way_round_the_dates_go():
    undated = APPS + [{"id": "a4", "title": "Aardvark", "published": True}]

    for sort in (apps_handler.SORT_NEWEST, apps_handler.SORT_OLDEST):
        assert _titles(apps_handler.select(undated, "all", "", sort))[-1] == "Aardvark"


def test_an_unknown_order_falls_back_to_the_default_one():
    assert apps_handler.clean_sort("sideways") == apps_handler.SORT_DEFAULT
    assert _titles(apps_handler.select(APPS, "all", "", "sideways")) == _titles(
        apps_handler.select(APPS, "all", "", apps_handler.SORT_DEFAULT)
    )


def test_the_cycle_wraps_round_to_the_start():
    seen = [apps_handler.SORT_DEFAULT]
    for _ in range(len(apps_handler.SORT_CYCLE)):
        seen.append(apps_handler.next_sort(seen[-1]))
    assert seen == apps_handler.SORT_CYCLE + [apps_handler.SORT_DEFAULT]


async def test_browse_opens_on_the_default_order_and_offers_the_sort_button(ctx):
    ctx.portal.apps = APPS
    await apps_handler.handle_browse(FakeEvent("/browse"), "", ctx)

    sent = ctx.client.sent[-1]
    assert "Sort: Default" in _labels(sent)
    assert "in the default order" in sent.text


async def test_the_sort_button_moves_one_step_and_says_so(ctx):
    ctx.portal.apps = APPS
    data = await ctx.callbacks.register(
        "apps.sort", {"mode": "all", "q": "", "s": apps_handler.SORT_DEFAULT}, owner_id=42
    )

    event = FakeCallbackEvent(data)
    await ctx.callbacks.dispatch(event, ctx)

    assert event.answers[-1][0] == "Sorted: A to Z"
    sent = ctx.client.sent[-1]
    assert "Sort: A to Z" in _labels(sent)
    assert sent.text.index("Invoice Maker") < sent.text.index("Wordle")


async def test_a_button_from_before_browse_existed_still_opens_the_newest_first(ctx):
    ctx.portal.apps = APPS
    data = await ctx.callbacks.register(
        "apps.page", {"mode": "new", "q": "", "page": 0}, owner_id=42
    )

    await ctx.callbacks.dispatch(FakeCallbackEvent(data), ctx)

    sent = ctx.client.sent[-1]
    assert "newest first" in sent.text
    assert "Sort: Newest" in _labels(sent)


# --- criterion 7, the portal is down ---------------------------------------


async def test_apps_falls_back_to_the_cache_and_says_so(ctx):
    ctx.portal.apps = APPS
    await apps_handler.show_page(FakeEvent("/apps"), ctx, mode="all")
    ctx.client.sent.clear()

    await ctx.cache.invalidate("apps:list")
    await ctx.cache.write("apps:list", APPS, -1)  # cached, but stale
    ctx.portal.raises = PortalUnavailable("down")

    await apps_handler.show_page(FakeEvent("/apps"), ctx, mode="all")

    body = ctx.client.sent[-1].text
    assert "Wordle" in body
    assert "out of date" in body


async def test_apps_with_no_cache_at_all_says_the_portal_is_quiet(ctx):
    ctx.portal.raises = PortalUnavailable("down")
    await apps_handler.show_page(FakeEvent("/apps"), ctx, mode="all")
    assert ctx.client.sent[-1].text == rich.PORTAL_DOWN


async def test_whoami_answers_from_the_mirror_row_when_the_portal_is_quiet(ctx):
    await ctx.db.touch_user(42, "tester", "Test", "en")
    await ctx.db.upsert_link(42, "user-1", "tester", "Tester", False)
    ctx.portal.raises = PortalUnavailable("down")

    await misc_handler.handle_whoami(FakeEvent("/whoami"), "", ctx)

    body = ctx.client.sent[-1].text
    assert "Tester" in body
    assert "out of date" in body


async def test_link_says_something_useful_when_the_portal_is_quiet(ctx):
    ctx.portal.raises = PortalUnavailable("down")
    await link_handler.handle_link(FakeEvent("/link ABCD2345"), "ABCD2345", ctx)
    assert ctx.client.sent[-1].text == rich.PORTAL_DOWN


# --- /code, criteria 13 and 14 ---------------------------------------------


async def test_code_needs_a_link_first(ctx):
    await mfa_handler.handle_code(FakeEvent("/code"), "", ctx)
    assert "not linked" in ctx.client.sent[-1].text.lower()
    assert ctx.portal.calls == []


async def test_code_shows_the_digits_in_a_tap_to_copy_block(ctx):
    await _link(ctx)
    await mfa_handler.handle_code(FakeEvent("/code"), "", ctx)

    sent = ctx.client.sent[-1]
    assert "<code>123456</code>" in sent.text
    assert "will ever ask you for this code" in sent.text
    assert "5 minutes" in sent.text


async def test_code_offers_a_copy_button(ctx):
    await _link(ctx)
    await mfa_handler.handle_code(FakeEvent("/code"), "", ctx)

    labels = _labels(ctx.client.sent[-1])
    if rich.HAS_COPY_BUTTON:
        assert "Copy the code" in labels
    else:
        # Criterion 14: without support the message still works, because the
        # <code> block is tap to copy on every client.
        assert labels == []
        assert "<code>123456</code>" in ctx.client.sent[-1].text


async def test_a_message_without_copy_support_still_carries_the_digits(ctx, monkeypatch):
    monkeypatch.setattr(rich, "HAS_COPY_BUTTON", False)
    await _link(ctx)
    await mfa_handler.handle_code(FakeEvent("/code"), "", ctx)

    sent = ctx.client.sent[-1]
    assert "<code>123456</code>" in sent.text
    assert sent.buttons is None


async def test_superseding_a_pushed_code_says_the_earlier_one_is_dead(ctx):
    await _link(ctx)

    async def issue(telegram_id):
        return IssuedCode(
            code="654321",
            expires_at="2026-01-01T00:05:00+00:00",
            seconds_remaining=300,
            superseded_pushed_code=True,
        )

    ctx.portal.mfa_issue_code = issue
    await mfa_handler.handle_code(FakeEvent("/code"), "", ctx)

    body = ctx.client.sent[-1].text
    assert "current code" in body
    assert "no longer works" in body


async def test_issuing_a_code_writes_an_audit_row_without_the_digits(ctx):
    await _link(ctx)
    await mfa_handler.handle_code(FakeEvent("/code"), "", ctx)

    rows = await ctx.db.fetchall("select * from mfa_events")
    assert len(rows) == 1
    assert rows[0]["event"] == "code_issued"
    assert "123456" not in str(dict(rows[0]))


async def test_the_code_message_is_scheduled_for_deletion(ctx):
    await _link(ctx)
    await mfa_handler.handle_code(FakeEvent("/code"), "", ctx)

    row = await ctx.db.fetchone(
        "select job_type, payload from scheduled_jobs where job_type = 'mfa.expire_code_message'"
    )
    assert row is not None
    assert "123456" not in row["payload"]


async def test_code_on_an_account_without_the_second_factor_gets_an_explanation(ctx):
    await _link(ctx)
    ctx.portal.raises = PortalError("Two factor authentication is off", 400, "mfa_disabled")

    await mfa_handler.handle_code(FakeEvent("/code"), "", ctx)

    assert "Settings" in ctx.client.sent[-1].text
    assert "123456" not in ctx.client.sent[-1].text


# --- the mfa buttons the portal sends --------------------------------------


async def test_an_approval_press_resolves_through_the_portal(ctx):
    from .conftest import FakeCallbackEvent, SentMessage

    await _link(ctx)
    prompt = SentMessage(42, "Approve this sign in", [["Approve"]])
    event = FakeCallbackEvent("mfa:a:11111111-2222-3333-4444-555555555555", message=prompt)

    handled = await mfa_handler.handle_callback(event, ctx)

    assert handled
    assert ctx.portal.calls[-1][0] == "mfa_resolve"
    assert ctx.portal.calls[-1][1][2] == "approve"
    # The prompt is edited down and its buttons are gone
    assert ctx.client.edits[-1][3] is None


async def test_a_denial_tells_the_user_to_change_the_password(ctx):
    from .conftest import FakeCallbackEvent, SentMessage

    await _link(ctx)
    prompt = SentMessage(42, "Approve this sign in", [["Approve"]])
    event = FakeCallbackEvent("mfa:d:11111111-2222-3333-4444-555555555555", message=prompt)

    await mfa_handler.handle_callback(event, ctx)

    assert "change it" in ctx.client.edits[-1][2]


async def test_a_button_that_is_not_an_mfa_button_is_left_alone(ctx):
    from .conftest import FakeCallbackEvent

    assert await mfa_handler.handle_callback(FakeCallbackEvent("cb:abcdef"), ctx) is False


# --- the bot to portal headers ---------------------------------------------


def test_the_bot_signature_covers_the_timestamp_the_nonce_and_the_body():
    secret = "s" * 64
    body = '{"action":"lookup","telegramId":42}'
    headers = signing.bot_headers(secret, body, timestamp=1700000000)

    message = f"1700000000.{headers['X-Bot-Nonce']}.{body}"
    expected = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    assert headers["X-Bot-Signature"] == expected


def test_two_bot_calls_carry_different_nonces():
    secret = "s" * 64
    first = signing.bot_headers(secret, "{}")
    second = signing.bot_headers(secret, "{}")
    assert first["X-Bot-Nonce"] != second["X-Bot-Nonce"]


# --- helpers ---------------------------------------------------------------


async def _link(ctx, telegram_id: int = 42) -> None:
    await ctx.db.touch_user(telegram_id, "tester", "Test", "en")
    await ctx.db.upsert_link(telegram_id, "user-1", "tester", "Tester", False)


def _labels(sent) -> list[str]:
    return [getattr(b, "text", "") for row in (sent.buttons or []) for b in row]
