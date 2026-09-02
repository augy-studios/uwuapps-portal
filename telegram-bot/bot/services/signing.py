"""The bot to portal authentication headers.

Bot calls carry the shared secret, so the signature covers the timestamp, a
nonce and the exact JSON body. The portal recomputes it in
lib/uwu-telegram.js.

The portal allows only a small amount of clock skew, so the VPS clock must be
synchronised or every call looks like a mysterious 403. SETUP.md makes
installing a time sync daemon a step rather than a footnote.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time


def _hmac_hex(key: str, message: str) -> str:
    return hmac.new(key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


def bot_headers(shared_secret: str, body: str, timestamp: int | None = None) -> dict[str, str]:
    """Headers for a bot to portal call on /api/telegram.

    The signature covers the timestamp, a nonce and the exact JSON body. The
    portal recomputes it over `JSON.stringify(req.body)`, so the body must be
    serialised compactly and its keys must not look like array indices, which
    a JavaScript object would reorder.
    """
    ts = str(timestamp if timestamp is not None else int(time.time()))
    nonce = secrets.token_hex(16)
    message = f"{ts}.{nonce}.{body}"
    return {
        "X-Bot-TS": ts,
        "X-Bot-Nonce": nonce,
        "X-Bot-Signature": _hmac_hex(shared_secret, message),
        "Content-Type": "application/json",
    }


def constant_time_equals(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))
