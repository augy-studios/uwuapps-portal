"""The composition helper itself: escaping, splitting, and the copy button."""

from __future__ import annotations

import pytest

from bot import rich


def test_portal_text_is_escaped():
    assert rich.esc("<script>alert(1)</script>") == "&lt;script&gt;alert(1)&lt;/script&gt;"
    assert rich.esc("Tom & Jerry") == "Tom &amp; Jerry"


def test_long_output_splits_on_paragraph_boundaries():
    paragraph = "x" * 500
    body = "\n\n".join([paragraph] * 20)
    chunks = rich.split_body(body, limit=1200)

    assert len(chunks) > 1
    assert all(len(chunk) <= 1200 for chunk in chunks)
    assert "".join(chunks).replace("\n", "") == body.replace("\n", "")


def test_a_single_paragraph_too_long_to_split_is_still_delivered():
    chunks = rich.split_body("y" * 5000, limit=1000)
    assert len(chunks) == 5
    assert all(len(chunk) <= 1000 for chunk in chunks)


def test_the_title_renders_bold_on_the_first_chunk_only():
    chunks = rich.compose("a\n\n" + "b" * 5000, title="Heading")
    assert chunks[0].startswith("<b>Heading</b>")
    assert not chunks[1].startswith("<b>Heading</b>")


def test_the_footer_lands_on_the_last_chunk_only():
    chunks = rich.compose("a\n\n" + "b" * 5000, footer="Page 1 of 2")
    assert "Page 1 of 2" not in chunks[0]
    assert chunks[-1].endswith("Page 1 of 2")


async def test_buttons_attach_to_the_final_chunk(ctx):
    await rich.send_rich_message(
        ctx.client,
        42,
        "c" * 9000,
        buttons=[[rich.Btn.link("Open the web app", "https://portal.test")]],
    )
    assert len(ctx.client.sent) > 1
    assert ctx.client.sent[0].buttons is None
    assert ctx.client.sent[-1].buttons is not None


async def test_an_edit_drops_the_buttons_the_old_message_carried(ctx):
    """A superseded code must not stay copyable out of an old message."""
    sent = await rich.send_rich_message(
        ctx.client, 42, "first",
        buttons=[[rich.Btn.callback("Next", "apps.page", {"page": 1})]],
        owner_id=42,
    )
    assert await ctx.db.fetchval("select count(*) from callbacks", default=0) == 1

    await rich.send_rich_message(ctx.client, 42, "that code is no longer valid", edit=sent)

    assert await ctx.db.fetchval("select count(*) from callbacks", default=0) == 0
    assert ctx.client.edits[-1][3] is None


async def test_an_edit_rebinds_new_buttons_to_the_same_message(ctx):
    sent = await rich.send_rich_message(
        ctx.client, 42, "page 1",
        buttons=[[rich.Btn.callback("Next", "apps.page", {"page": 1})]],
        owner_id=42,
    )
    await rich.send_rich_message(
        ctx.client, 42, "page 2",
        buttons=[[rich.Btn.callback("Previous", "apps.page", {"page": 0})]],
        owner_id=42, edit=sent,
    )

    rows = await ctx.db.fetchall("select message_id from callbacks")
    assert len(rows) == 1
    assert rows[0]["message_id"] == sent.id


def test_a_copy_button_carries_no_callback_token():
    button = rich.Btn.copy("Copy the code", "123456")
    assert button.action is None
    assert button.value == "123456"


async def test_a_copy_button_writes_no_registry_row(ctx):
    """It is outside the token registry entirely, so there is nothing to expire."""
    await rich.send_rich_message(
        ctx.client, 42, "code", buttons=[[rich.Btn.copy("Copy the code", "123456")]]
    )
    assert await ctx.db.fetchval("select count(*) from callbacks", default=0) == 0


async def test_a_button_for_an_unregistered_action_is_refused(ctx):
    with pytest.raises(ValueError):
        await rich.send_rich_message(
            ctx.client, 42, "body",
            buttons=[[rich.Btn.callback("Nope", "does.not.exist")]],
        )


def test_humanize_seconds_reads_naturally():
    assert rich.humanize_seconds(1) == "1 second"
    assert rich.humanize_seconds(45) == "45 seconds"
    assert rich.humanize_seconds(300) == "5 minutes"
    assert rich.humanize_seconds(330) == "5 minutes and 30 seconds"
