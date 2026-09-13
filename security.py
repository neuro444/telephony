"""Plivo V3 webhook signature verification.

Plivo assembles: full URL + "." + POST params sorted case-sensitively by name
(name and value concatenated) + "." + nonce, then HMAC-SHA256 with the Auth
Token, base64-encoded. Multiple active auth tokens produce a comma-separated
header — any one matching is valid.

Without this check, anyone who learns your webhook URL can drive your phone
agent and bill your account.
"""
import hmac

from fastapi import Request, HTTPException
from plivo.utils import validate_v3_signature

import config


def _twilio_url(request) -> str:
    url = config.TWILIO_PUBLIC_BASE_URL.rstrip("/") + request.url.path
    if request.url.query:
        url += "?" + request.url.query
    return url


async def verify_voice(request: Request) -> dict[str, str]:
    # Explicit Twilio routes plus unchanged legacy Plivo URLs can coexist.
    carrier = "twilio" if request.url.path.startswith("/twilio/") or request.headers.get("X-Twilio-Signature") else (
        "plivo" if request.headers.get("X-Plivo-Signature-V3") or request.headers.get("X-Plivo-Signature-Ma-V3") else config.TELEPHONY_PROVIDER
    )
    config.set_carrier(carrier)
    if carrier == "plivo":
        return await verify_plivo(request)
    from twilio.request_validator import RequestValidator
    if not config.TWILIO_AUTH_TOKEN or not config.TWILIO_ACCOUNT_SID:
        raise HTTPException(500, "Twilio credentials not configured")
    form = await request.form()
    if not RequestValidator(config.TWILIO_AUTH_TOKEN).validate(
        _twilio_url(request), form, request.headers.get("X-Twilio-Signature", "")
    ):
        raise HTTPException(403, "invalid Twilio signature")
    params = dict(form)
    if params.get("AccountSid") != config.TWILIO_ACCOUNT_SID or not params.get("CallSid"):
        raise HTTPException(403, "unexpected Twilio account or missing call")
    # Preserve internal field names so brain, order and printer paths stay shared.
    return {**params, "CallUUID": params["CallSid"],
            "DialStatus": params.get("DialCallStatus", ""),
            "Duration": params.get("CallDuration", ""),
            "BillDuration": "",  # CallDuration is not provider-billed duration.
            "HangupCause": params.get("CallStatus", ""),
            "Event": params.get("StreamEvent", ""),
            "StatusReason": params.get("StreamError", "")}


def verify_twilio_socket(websocket) -> bool:
    from twilio.request_validator import RequestValidator
    if not config.TWILIO_AUTH_TOKEN:
        return False
    url = _twilio_url(websocket)
    signature = websocket.headers.get("X-Twilio-Signature", "")
    validator = RequestValidator(config.TWILIO_AUTH_TOKEN)
    # Upgrade is an HTTPS GET. Accept the equivalent WSS representation too,
    # including Twilio's documented trailing-slash handshake variation.
    urls = (url, url.replace("https://", "wss://", 1))
    return any(validator.validate(candidate, {}, signature)
               for base in urls for candidate in (base, base + "/"))

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
    method = getattr(request, "method", "POST")
    if not any(
        validate_v3_signature(
            method, url, nonce, config.PLIVO_AUTH_TOKEN, signature, params
        )
        for signature in signatures
    ):
        raise HTTPException(403, "invalid Plivo signature")
    return params
