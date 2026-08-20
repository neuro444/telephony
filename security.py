"""Plivo V3 webhook signature verification.

Plivo assembles: full URL + "." + POST params sorted case-sensitively by name
(name and value concatenated) + "." + nonce, then HMAC-SHA256 with the Auth
Token, base64-encoded. Multiple active auth tokens produce a comma-separated
header — any one matching is valid.

Without this check, anyone who learns your webhook URL can drive your phone
agent and bill your account.
"""
import base64
import hashlib
import hmac

from fastapi import Request, HTTPException

import config


def _expected(url: str, params: dict[str, str], nonce: str, token: str) -> str:
    payload = (
        url
        + "."
        + "".join(f"{k}{v}" for k, v in sorted(params.items()))
        + "."
        + nonce
    )
    digest = hmac.new(token.encode(), payload.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


async def verify_plivo(request: Request) -> dict[str, str]:
    """FastAPI dependency: returns the validated form params, or raises 403."""
    params = {k: v for k, v in (await request.form()).multi_items()}
    if not config.PLIVO_AUTH_TOKEN:
        raise HTTPException(500, "PLIVO_AUTH_TOKEN not configured")

    signature = request.headers.get("X-Plivo-Signature-V3", "")
    nonce = request.headers.get("X-Plivo-Signature-V3-Nonce", "")
    if not signature or not nonce:
        raise HTTPException(403, "missing Plivo signature headers")

    # PUBLIC_BASE_URL, not request.url — behind nginx the scheme/host Plivo
    # signed is not what FastAPI sees, and the signature would never match.
    url = config.PLIVO_PUBLIC_BASE_URL.rstrip("/") + request.url.path
    expected = _expected(url, params, nonce, config.PLIVO_AUTH_TOKEN)

    # Account may have several active tokens -> comma-separated signatures.
    if not any(
        hmac.compare_digest(expected, s.strip()) for s in signature.split(",")
    ):
        raise HTTPException(403, "invalid Plivo signature")
    return params
