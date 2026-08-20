#!/usr/bin/env python3
"""
Sends a correctly-signed fake Plivo webhook request to your running gateway,
proving verify_plivo() works end-to-end against a real HTTP request (not
the text harness, which bypasses it entirely).

Usage:
    python3 test_signature.py

Reads PLIVO_AUTH_TOKEN and PLIVO_PUBLIC_BASE_URL from your .env file
automatically -- no need to paste secrets into this script.
"""
import base64
import hashlib
import hmac
import re
import urllib.request
import urllib.parse
import uuid


def load_env(path=".env"):
    values = {}
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            values[k.strip()] = v.strip()
    return values


def sign(url, params, nonce, token):
    payload = url + "".join(f"{k}{v}" for k, v in sorted(params.items())) + "." + nonce
    digest = hmac.new(token.encode(), payload.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def main():
    env = load_env()
    token = env.get("PLIVO_AUTH_TOKEN", "")
    base_url = env.get("PLIVO_PUBLIC_BASE_URL", "")
    if not token or not base_url:
        print("Missing PLIVO_AUTH_TOKEN or PLIVO_PUBLIC_BASE_URL in .env")
        return

    path = "/voice/answer"
    url = base_url.rstrip("/") + path
    nonce = uuid.uuid4().hex
    params = {
        "CallUUID": f"sigtest-{uuid.uuid4().hex[:8]}",
        "From": "+15551234567",
        "To": "+14042071271",
    }
    signature = sign(url, params, nonce, token)

    body = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("X-Plivo-Signature-V3", signature)
    req.add_header("X-Plivo-Signature-V3-Nonce", nonce)

    print(f"POST {url}")
    print(f"params: {params}")
    print()

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            print(f"Status: {resp.status}")
            print(f"Body:\n{resp.read().decode()}")
            print()
            print("PASS: correctly-signed request was accepted.")
    except urllib.error.HTTPError as exc:
        print(f"Status: {exc.code}")
        print(f"Body:\n{exc.read().decode()}")
        if exc.code == 403:
            print()
            print("FAIL: a correctly-signed request was rejected as invalid.")
            print("This means the gateway's PLIVO_AUTH_TOKEN does not match")
            print("what was used to sign this request, or PLIVO_PUBLIC_BASE_URL")
            print("in .env does not exactly match the URL Plivo would call.")
        else:
            print(f"\nUnexpected status {exc.code} -- signature check passed")
            print("(a 403 would mean rejection; anything else means it got")
            print("past verify_plivo and failed elsewhere, which is progress).")

    print()
    print("--- Now the negative test: a WRONG signature should be rejected ---")
    bad_req = urllib.request.Request(url, data=body, method="POST")
    bad_req.add_header("Content-Type", "application/x-www-form-urlencoded")
    bad_req.add_header("X-Plivo-Signature-V3", "not-a-real-signature")
    bad_req.add_header("X-Plivo-Signature-V3-Nonce", nonce)
    try:
        with urllib.request.urlopen(bad_req, timeout=15) as resp:
            print(f"Status: {resp.status} -- FAIL, a bad signature should NOT succeed")
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            print(f"Status: {exc.code} -- PASS, bad signature correctly rejected")
        else:
            print(f"Status: {exc.code} -- unexpected, expected 403")


if __name__ == "__main__":
    main()
