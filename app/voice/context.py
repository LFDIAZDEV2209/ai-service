"""Token reducido autenticado, sin identidad ni PHI en sus claims visibles."""

import base64
import hashlib
import hmac
import json
import time

from app.voice.store import VoiceContextError

AUDIENCE = "vita-custom-llm"


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _decode(value: str) -> bytes:
    return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)


def sign_context(session_id: str, expires: int, secret: str) -> str:
    if len(secret.encode()) < 32:
        raise VoiceContextError("voice_context_unconfigured", 503)
    payload = _encode(
        json.dumps(
            {"sid": session_id, "exp": expires, "aud": AUDIENCE},
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    )
    message = f"v1.{payload}"
    signature = hmac.digest(secret.encode(), message.encode(), hashlib.sha256)
    return f"{message}.{_encode(signature)}"


def verify_context(token: str, secret: str, *, now: int | None = None) -> tuple[str, int]:
    if len(secret.encode()) < 32:
        raise VoiceContextError("voice_context_unconfigured", 503)
    try:
        if len(token) > 2048:
            raise ValueError
        version, payload, signature = token.split(".")
        expected = hmac.digest(secret.encode(), f"{version}.{payload}".encode(), hashlib.sha256)
        if version != "v1" or not hmac.compare_digest(_decode(signature), expected):
            raise ValueError
        claims = json.loads(_decode(payload))
        current = int(time.time()) if now is None else now
        if (
            set(claims) != {"sid", "exp", "aud"}
            or claims["aud"] != AUDIENCE
            or not isinstance(claims["sid"], str)
            or len(claims["sid"]) != 36
            or type(claims["exp"]) is not int
            or not current < claims["exp"] <= current + 600
        ):
            raise ValueError
        return claims["sid"], claims["exp"]
    except (ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise VoiceContextError("voice_context_invalid") from exc
