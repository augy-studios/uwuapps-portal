"""The composition helper itself: escaping, the two halves, splitting, the
copy button, and the raw request a rich message goes out on."""

from __future__ import annotations

import pytest
from telethon.tl import functions, types

from bot import reply, rich


# --- escaping --------------------------------------------------------------


def test_portal_text_is_escaped_for_the_markdown_dialect():
    assert rich.escape_md("*not bold*") == "\\*not bold\\*"
    assert rich.escape_md("a_b [c] #d | e") == "a\\_b \\[c\\] \\#d \\| e"
    assert rich.escape_md("Tom & Jerry") == "Tom & Jerry"
    assert rich.escape_md(None) == ""


def test_a_table_cell_keeps_to_one_line_and_one_cell():
    assert rich.escape_cell("a|b\nc") == "a\\|b c"


def test_authored_markup_is_not_escaped():
    assert rich.bold("x*y") == "**x\\*y**"
    assert rich.italic("tools") == "*tools*"
    assert rich.code("12`34") == "`12'34`"


# --- the two halves --------------------------------------------------------


def test_the_title_becomes_a_heading_and_the_footer_closes_the_message():
    view = rich.message("body", title="Heading", footer="Page 1 of 2")
    assert view["markdown"].startswith("# Heading\n\n")
    assert view["markdown"].endswith("\n\nPage 1 of 2")
    assert view["fallback"].startswith("Heading\n\n")
    assert view["fallback"].endswith("Page 1 of 2")


def test_the_fallback_reads_the_markup_back_out():
    markdown = "\n".join(
        [
            "# Results for wor\\*dle",
            "",
            "**1. Wordle**  ",
            "Guess the word  ",
            "*games*",
            "",
            "- **/start** See what this is",
            "> quoted",
            "`123456`",
        ]
    )
    assert rich.plain(markdown) == "\n".join(
        [
            "Results for wor*dle",
            "",
            "1. Wordle",
            "Guess the word",
            "games",
            "",
            "- /start See what this is",
            "quoted",
            "123456",
        ]
    )


def test_a_table_boils_down_to_one_line_per_row():
    markdown = rich.table(["Value"], [("Known users", 12), ("Pipe | here", "x")], corner="Field")
    assert markdown.splitlines()[0] == "| Field | Value |"
    assert markdown.splitlines()[1] == "| --- | --- |"
    assert rich.plain(markdown) == "Known users: 12\nPipe | here: x"

    wide = rich.table(["App", "State"], [(1, "Wordle", "published")])
    assert wide.splitlines()[0] == "|  | App | State |"
    assert rich.plain(wide) == "1: Wordle, published"


def test_the_fallback_is_never_empty():
    view = rich.message("**")
    assert view["fallback"]


def test_a_builder_may_hand_in_its_own_fallback():
    view = rich.message("# x", fallback="the plain reading")
    assert view["fallback"] == "the plain reading"


def test_hard_line_breaks_join_lines_inside_a_block():
    assert rich.lines("a", "", "b") == "a  \nb"


# --- splitting -------------------------------------------------------------


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


async def test_a_rich_message_that_will_not_fit_goes_out_plain_in_pieces(ctx):
    view = rich.message("\n\n".join(["z" * 500] * 20), title="Long")
    await rich.send_rich_message(ctx.client, 42, view)

    assert len(ctx.client.sent) > 1
    assert all(m.markdown is None for m in ctx.client.sent)
    assert ctx.client.sent[0].text.startswith("Long")


# --- the wire --------------------------------------------------------------


async def test_a_rich_message_goes_out_as_a_raw_request_with_both_halves(ctx):
    view = rich.message("**hello**", title="Heading")
    sent = await rich.send_rich_message(
        ctx.client, 42, view, reply_to=7,
        buttons=[[rich.Btn.link("Open", "https://portal.test")]],
    )

    request = ctx.client.requests[-1]
    assert isinstance(request, functions.messages.SendMessageRequest)
    assert isinstance(request.rich_message, types.InputRichMessageMarkdown)
    assert request.rich_message.markdown == "# Heading\n\n**hello**"
    assert request.message == "Heading\n\nhello"
    assert request.reply_to.reply_to_msg_id == 7
    assert request.no_webpage is True
    assert isinstance(request.reply_markup, types.ReplyInlineMarkup)
    assert sent.chat_id == 42 and sent.id == ctx.client.sent[-1].id


async def test_a_plain_notice_never_touches_the_raw_request_path(ctx):
    await rich.send_rich_message(ctx.client, 42, "Nothing changed.")
    assert ctx.client.requests == []
    assert ctx.client.sent[-1].text == "Nothing changed."
    assert ctx.client.sent[-1].markdown is None


async def test_a_rejected_rich_payload_falls_back_to_the_plain_half(ctx, caplog):
    ctx.client.raw_raises = RuntimeError("RICH_MESSAGE_INVALID")
    view = rich.message("body", title="Heading")

    sent = await rich.send_rich_message(
        ctx.client, 42, view, buttons=[[rich.Btn.link("Open", "https://portal.test")]]
    )

    assert sent is not None
    assert ctx.client.sent[-1].text == "Heading\n\nbody"
    assert ctx.client.sent[-1].markdown is None
    assert ctx.client.sent[-1].buttons is not None
    assert "falling back" in caplog.text


async def test_an_edit_with_no_buttons_sends_an_empty_keyboard_to_remove_the_old_one(ctx):
    view = rich.message("body", title="Heading")
    sent = await rich.send_rich_message(
        ctx.client, 42, view, buttons=[[rich.Btn.callback("Next", "apps.page", {"page": 1})]],
    )
    await rich.send_rich_message(ctx.client, 42, rich.message("done"), edit=sent)

    request = ctx.client.requests[-1]
    assert isinstance(request, functions.messages.EditMessageRequest)
    assert request.id == sent.id
    assert isinstance(request.reply_markup, types.ReplyInlineMarkup)
    assert request.reply_markup.rows == []
    assert ctx.client.edits[-1][3] is None


async def test_a_plain_edit_with_no_buttons_also_removes_the_keyboard(ctx):
    sent = await rich.send_rich_message(
        ctx.client, 42, "first", buttons=[[rich.Btn.callback("Next", "apps.page", {"page": 1})]],
    )
    await rich.send_rich_message(ctx.client, 42, "second", edit=sent)
    assert ctx.client.edits[-1][3] is None


def test_the_message_id_is_read_off_whatever_shape_the_send_returns():
    assert reply.sent_message_id(
        types.UpdateShortSentMessage(id=5, pts=0, pts_count=0, date=None)
    ) == 5
    updates = types.Updates(
        updates=[types.UpdateMessageID(id=9, random_id=1)], users=[], chats=[], date=None, seq=0
    )
    assert reply.sent_message_id(updates) == 9
    assert reply.sent_message_id(object()) is None


# --- the registry ----------------------------------------------------------


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
        ctx.client, 42, rich.message("page 1"),
        buttons=[[rich.Btn.callback("Next", "apps.page", {"page": 1})]],
        owner_id=42,
    )
    await rich.send_rich_message(
        ctx.client, 42, rich.message("page 2"),
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
