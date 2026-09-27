"""The new app announcement: the listing's images ride inline in the rich
message, and the plain half never shows their markup."""

from __future__ import annotations

from bot import rich
from bot.handlers.misc import TOPIC_NEW_APPS
from bot.jobs import new_apps


def _app(**overrides):
    app = {
        "id": "a1",
        "title": "Wordle",
        "description": "Guess the word",
        "url": "https://wordle.test",
        "published": True,
    }
    app.update(overrides)
    return app


def test_the_gallery_starts_at_the_thumbnail_and_keeps_its_order():
    app = _app(
        gallery_urls=["https://img.test/1.png", "https://img.test/2.png", "https://img.test/3.png"],
        thumbnail_index=1,
    )
    assert new_apps.gallery(app) == [
        "https://img.test/2.png", "https://img.test/1.png", "https://img.test/3.png",
    ]


def test_an_older_row_with_only_a_thumbnail_still_shows_it():
    assert new_apps.gallery(_app(thumbnail_url="https://img.test/t.png")) == [
        "https://img.test/t.png"
    ]
    assert new_apps.gallery(_app()) == []


def test_a_bad_index_or_url_is_tolerated():
    app = _app(gallery_urls=["https://img.test/1.png", "ftp://img.test/2.png"], thumbnail_index=9)
    assert new_apps.gallery(app) == ["https://img.test/1.png"]
    app = _app(gallery_urls=["https://img.test/1.png"], thumbnail_index="x")
    assert new_apps.gallery(app) == ["https://img.test/1.png"]


def test_the_images_sit_inline_after_the_description():
    app = _app(gallery_urls=["https://img.test/1.png", "https://img.test/2.png"])
    view, buttons = new_apps.build_announcement(app)

    assert view["markdown"] == "\n\n".join([
        "# New in the directory",
        "**Wordle**  \nGuess the word",
        "![Wordle, image 1](https://img.test/1.png)",
        "![Wordle, image 2](https://img.test/2.png)",
        "Turn these off any time with /notify.",
    ])
    assert view["fallback"] == (
        "New in the directory\n\nWordle\nGuess the word\n\n"
        "Turn these off any time with /notify."
    )
    assert buttons[0][0].target == "https://wordle.test"


def test_no_gallery_reads_as_before():
    view, _ = new_apps.build_announcement(_app())
    assert "![" not in view["markdown"]


def test_images_stop_before_the_message_would_stop_being_rich():
    long_url = "https://img.test/" + "x" * 1000 + ".png"
    app = _app(gallery_urls=[f"{long_url}?{n}" for n in range(10)])
    view, _ = new_apps.build_announcement(app)

    assert len(view["markdown"]) <= rich.TELEGRAM_LIMIT
    assert 0 < view["markdown"].count("![") < 10


async def test_subscribers_get_the_images_in_the_rich_message(ctx):
    ctx.portal.apps = [_app(id="old")]
    await new_apps.announce({}, ctx)  # seeds seen_apps, announces nothing
    assert ctx.client.sent == []

    await ctx.db.touch_user(42, "tester", "Test", "en")
    await ctx.db.subscribe(42, TOPIC_NEW_APPS)
    ctx.portal.apps.append(_app(gallery_urls=["https://img.test/1.png"]))
    await new_apps.refresh({}, ctx)
    await new_apps.announce({}, ctx)

    assert len(ctx.client.sent) == 1
    sent = ctx.client.sent[0]
    assert "![Wordle, image 1](https://img.test/1.png)" in sent.markdown
    assert "img.test" not in sent.text
