"""Signs short-lived JWTs for the embedded watsonx Orchestrate web chat.

With Orchestrate "Embed Security" on, every web-chat request must carry a JWT
signed (RS256) with OUR private key; Orchestrate verifies it with the public
key registered under Settings → Embed Security. The private key therefore
lives only on the backend — never in a NEXT_PUBLIC_ variable.

Config (environment):
    WXO_EMBED_PRIVATE_KEY        PEM text (newlines may be written as "\\n"), or
    WXO_EMBED_PRIVATE_KEY_PATH   path to the PEM file
    WXO_EMBED_TOKEN_TTL_SECONDS  optional, default 3600 (IBM max is 7100)

If no key is configured, chat security is treated as off and
:func:`sign_chat_token` returns None.
"""
from __future__ import annotations

import os
import time
import uuid
from functools import lru_cache
from pathlib import Path

# IBM documents a maximum token lifetime of 7100 seconds.
_MAX_TTL = 7100


@lru_cache(maxsize=1)
def _private_key_pem() -> str | None:
    pem = os.environ.get("WXO_EMBED_PRIVATE_KEY", "").strip()
    if pem:
        return pem.replace("\\n", "\n")
    path = os.environ.get("WXO_EMBED_PRIVATE_KEY_PATH", "").strip()
    if path and Path(path).is_file():
        return Path(path).read_text(encoding="utf-8")
    return None


def chat_security_enabled() -> bool:
    return _private_key_pem() is not None


def token_ttl_seconds() -> int:
    try:
        ttl = int(os.environ.get("WXO_EMBED_TOKEN_TTL_SECONDS", "3600"))
    except ValueError:
        ttl = 3600
    return max(60, min(ttl, _MAX_TTL))


def sign_chat_token(visitor_id: str | None = None) -> tuple[str, int] | None:
    """Return ``(jwt, expires_in_seconds)``, or None when security is off.

    Claims: ``sub`` identifies the (anonymous) demo visitor; ``context``
    carries non-sensitive info the agent may see. No ``user_payload`` is sent —
    IBM only requires it for on-behalf-of SSO, which this demo doesn't use.
    """
    import jwt  # PyJWT

    pem = _private_key_pem()
    if pem is None:
        return None
    ttl = token_ttl_seconds()
    now = int(time.time())
    claims = {
        "sub": visitor_id or f"demo-{uuid.uuid4()}",
        "iat": now,
        "exp": now + ttl,
        "context": {"app": "claimback", "role": "demo_user"},
    }
    return jwt.encode(claims, pem, algorithm="RS256"), ttl
