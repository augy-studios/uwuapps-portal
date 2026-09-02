"""The message style rules, enforced rather than remembered.

Acceptance criteria 3 and 4:

3. No user facing string anywhere contains an em dash.
4. No command description or reply names the bot.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from bot import rich
from bot.handlers import all_commands, botfather_block, command_list_html

ROOT = Path(__file__).resolve().parent.parent
BOT_PACKAGE = ROOT / "bot"

EM_DASHES = ("—", "―")

# The product is "the portal" or "UwU Suite". Referring to the bot by name, by
# username, or as "the bot" is what these catch.
NAMES_THE_BOT = re.compile(r"\bbots?\b|@[A-Za-z_]{3,}bot\b", re.IGNORECASE)

# A log line is not user facing, and printf style markers are how they are told
# apart from copy without keeping a list by hand.
LOG_MARKERS = re.compile(r"%[sdrif]")


def _python_sources() -> list[Path]:
    """The shipped code. The tests themselves need the character to test for it."""
    return [ROOT / "bot.py", *BOT_PACKAGE.rglob("*.py")]


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """Ids of the constant nodes that are docstrings, which are not copy."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                if isinstance(body[0].value.value, str):
                    ids.add(id(body[0].value))
    return ids


def _prose_strings(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = _docstring_nodes(tree)
    out: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in docstrings:
            continue
        text = node.value
        if " " not in text or LOG_MARKERS.search(text):
            continue
        out.append(text)
    return out


# --- criterion 3 -----------------------------------------------------------


def test_no_em_dash_in_any_source_file():
    offenders = []
    for path in _python_sources():
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if any(dash in text for dash in EM_DASHES):
            offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == [], f"Em dash found in {offenders}"


def test_no_em_dash_in_the_documentation():
    offenders = []
    for path in ROOT.glob("*.md"):
        text = path.read_text(encoding="utf-8")
        if any(dash in text for dash in EM_DASHES):
            offenders.append(path.name)
    assert offenders == [], f"Em dash found in {offenders}"


def test_sanitize_strips_an_em_dash_that_slips_through():
    dash = "—"
    assert rich.sanitize(f"one {dash} two") == "one, two"
    assert rich.sanitize(f"one{dash}two") == "one, two"
    assert dash not in rich.sanitize(f"a {dash} b {dash} c")


def test_compose_sanitizes_the_title_and_the_footer():
    dash = "—"
    chunks = rich.compose("body", title=f"A {dash} B", footer=f"C {dash} D")
    assert dash not in chunks[0]


# --- criterion 4 -----------------------------------------------------------


def test_no_command_description_names_the_bot():
    offenders = [
        f"/{c.name}: {c.description}"
        for c in all_commands()
        if NAMES_THE_BOT.search(c.description)
    ]
    assert offenders == [], f"Command descriptions naming the bot: {offenders}"


def test_no_reply_names_the_bot():
    """Every prose string in the user facing modules, docstrings excluded."""
    surface = list((BOT_PACKAGE / "handlers").glob("*.py")) + [BOT_PACKAGE / "rich.py"]
    offenders = []
    for path in surface:
        for text in _prose_strings(path):
            if NAMES_THE_BOT.search(text):
                offenders.append(f"{path.name}: {text[:70]}")
    assert offenders == [], f"Replies naming the bot: {offenders}"


def test_the_generated_command_list_names_no_bot():
    assert not NAMES_THE_BOT.search(command_list_html(include_admin=True))
    assert not NAMES_THE_BOT.search(botfather_block())


# --- the registry is the single source of both lists -----------------------


def test_every_command_carries_a_description():
    assert all(c.description.strip() for c in all_commands())


def test_the_required_commands_are_registered():
    names = {c.name for c in all_commands()}
    assert {"start", "link", "unlink", "code"} <= names


def test_unlink_is_hidden_from_both_lists():
    """Advertising a command whose only answer is a refusal is worse than silence."""
    assert "unlink" not in botfather_block()
    assert "/unlink" not in command_list_html()


def test_admin_commands_stay_out_of_the_public_list():
    """Visible to an admin inside /start, absent from the list everyone sees."""
    assert "stats" not in botfather_block()
    assert "/stats" not in command_list_html()
    assert "/stats" in command_list_html(include_admin=True)


def test_botfather_block_shape():
    for line in botfather_block().splitlines():
        name, _, description = line.partition(" - ")
        assert re.fullmatch(r"[a-z0-9_]{1,32}", name), line
        assert description, line
        assert len(description) <= 256, line


def test_the_start_list_and_the_botfather_block_agree():
    start_names = re.findall(r"^/(\w+)", command_list_html(), re.MULTILINE)
    botfather_names = [line.split(" - ")[0] for line in botfather_block().splitlines()]
    assert start_names == botfather_names


@pytest.mark.parametrize("command_name", ["start", "link", "code"])
def test_the_three_documented_descriptions_are_intact(command_name):
    """These are the lines the setup guide tells the operator to paste."""
    expected = {
        "start": "See what this is and how to begin",
        "link": "Link this Telegram account to your portal account",
        "code": "Get a one time code for signing in",
    }
    match = next(c for c in all_commands() if c.name == command_name)
    assert match.description == expected[command_name]
