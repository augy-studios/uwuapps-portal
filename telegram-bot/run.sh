#!/usr/bin/env bash
# Activate the virtualenv and hand the process over to Python, so Ctrl-c
# and systemd signals reach the bot instead of stopping at the shell.
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
    echo "No virtualenv found at .venv" >&2
    echo "Run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
    exit 1
fi

if [ ! -f .env ]; then
    echo "No .env found. Copy .env.example to .env and fill it in." >&2
    exit 1
fi

exec .venv/bin/python bot.py
