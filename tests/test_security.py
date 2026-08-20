"""Plivo V3 webhook signature verification.

Without this check, anyone who learns the webhook URL can drive the phone
agent and bill the account.
"""
import base64
import hashlib
import hmac

import pytest
from fastapi import HTTPException

import config
import security

URL = "https://example.com/abcd?foo=bar"
NONCE = "kjsdhfsd87sd7yisud2"
TOKEN = "test-auth-token"
PARAMS = {"Digits": "1234", "CallUUID": "abc", "From": "+15551111111"}


def sign(url=URL, params=PARAMS, nonce=NONCE, token=TOKEN):
    return security._expected(url, params, nonce, token)


def test_signature_matches_plivos_documented_construction():
    """URL + sorted(k+v) + '.' + nonce, HMAC-SHA256, base64."""
    payload = URL + "CallUUIDabcDigits1234From+15551111111" + "." + NONCE
    expected = base64.b64encode(
        hmac.new(TOKEN.encode(), payload.encode(), hashlib.sha256).digest()
    ).decode()
    assert sign() == expected


def test_tampered_param_changes_signature():
    assert sign() != sign(params={**PARAMS, "Digits": "9999"})


def test_wrong_token_changes_signature():
    assert sign() != sign(token="other-token")


def test_different_url_changes_signature():
    """The signed URL is the public one — this is why request.url is unsafe."""
    assert sign() != sign(url="https://example.com/abcd")


def test_param_sorting_is_case_sensitive():
    """Plivo sorts Unix-style; Python's sorted() on str already matches."""
    params = {"b": "1", "A": "2", "a": "3"}
    payload_order = "".join(f"{k}{v}" for k, v in sorted(params.items()))
    assert payload_order == "A2a3b1"


class FakeRequest:
    def __init__(self, params, headers, path="/voice/turn"):
        self._params = params
        self.headers = headers

        class _URL:
            pass

        self.url = _URL()
        self.url.path = path

    async def form(self):
        class _Form:
            def __init__(self, p):
                self._p = p

            def multi_items(self):
                return list(self._p.items())

        return _Form(self._params)


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(config, "PLIVO_AUTH_TOKEN", TOKEN)
    monkeypatch.setattr(config, "PLIVO_PUBLIC_BASE_URL", "https://voice.test")


def headers_for(params, path="/voice/turn", token=TOKEN):
    sig = security._expected(f"https://voice.test{path}", params, NONCE, token)
    return {"X-Plivo-Signature-V3": sig, "X-Plivo-Signature-V3-Nonce": NONCE}


def _run(coro):
    """verify_plivo is async; these tests drive it synchronously."""
    import asyncio

    return asyncio.run(coro)


def test_valid_signature_returns_params(configured):
    params = {"CallUUID": "abc", "From": "+15551111111"}
    req = FakeRequest(params, headers_for(params))
    assert _run(security.verify_plivo(req)) == params


def test_invalid_signature_rejected(configured):
    params = {"CallUUID": "abc"}
    req = FakeRequest(params, {"X-Plivo-Signature-V3": "bogus",
                               "X-Plivo-Signature-V3-Nonce": NONCE})
    with pytest.raises(HTTPException) as e:
        _run(security.verify_plivo(req))
    assert e.value.status_code == 403


def test_missing_headers_rejected(configured):
    req = FakeRequest({"CallUUID": "abc"}, {})
    with pytest.raises(HTTPException) as e:
        _run(security.verify_plivo(req))
    assert e.value.status_code == 403


def test_tampered_body_rejected(configured):
    """Signature covers the params — changing one after signing must fail."""
    signed = {"CallUUID": "abc", "From": "+15551111111"}
    hdrs = headers_for(signed)
    req = FakeRequest({**signed, "From": "+19998887777"}, hdrs)
    with pytest.raises(HTTPException) as e:
        _run(security.verify_plivo(req))
    assert e.value.status_code == 403


def test_multiple_active_tokens_any_match_accepted(configured):
    """Plivo sends comma-separated signatures when several tokens are active."""
    params = {"CallUUID": "abc"}
    good = security._expected("https://voice.test/voice/turn", params, NONCE, TOKEN)
    req = FakeRequest(params, {
        "X-Plivo-Signature-V3": f"someoldsignature,{good}",
        "X-Plivo-Signature-V3-Nonce": NONCE,
    })
    assert _run(security.verify_plivo(req)) == params


def test_unconfigured_token_is_500_not_a_silent_pass(configured, monkeypatch):
    """A missing token must never mean 'allow everything'."""
    monkeypatch.setattr(config, "PLIVO_AUTH_TOKEN", "")
    req = FakeRequest({"CallUUID": "abc"}, headers_for({"CallUUID": "abc"}))
    with pytest.raises(HTTPException) as e:
        _run(security.verify_plivo(req))
    assert e.value.status_code == 500
