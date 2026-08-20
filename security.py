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

    # V3 is signed with the account/subaccount associated with the number;
    # Ma-V3 is always signed with the main-account token. Accept either when
    # it validates so a main-account credential works for subaccount traffic.
    signature_headers = (
        request.headers.get("X-Plivo-Signature-V3", ""),
        request.headers.get("X-Plivo-Signature-Ma-V3", ""),
    )
    nonce = request.headers.get("X-Plivo-Signature-V3-Nonce", "")
    signatures = [
        value.strip()
        for header in signature_headers
        for value in header.split(",")
        if value.strip()
    ]
    if not signatures or not nonce:
        raise HTTPException(403, "missing Plivo signature headers")

    # PUBLIC_BASE_URL, not request.url — behind nginx the scheme/host Plivo
    # signed is not what FastAPI sees, and the signature would never match.
    url = config.PLIVO_PUBLIC_BASE_URL.rstrip("/") + request.url.path
    expected = _expected(url, params, nonce, config.PLIVO_AUTH_TOKEN)

    # Account may have several active tokens -> comma-separated signatures.
    if not any(hmac.compare_digest(expected, signature) for signature in signatures):
        raise HTTPException(403, "invalid Plivo signature")
    return params
