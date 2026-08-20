"""Text-mode call harness.

Exercises /voice/answer and /voice/turn without placing a real Plivo call
or needing valid signatures — this posts directly against the FastAPI app
in-process via TestClient, which does NOT go through verify_plivo (it's
only invoked when a real request hits the ASGI app through a live server).

To actually test signature verification, run the server for real and use
curl with a manually-computed signature, or temporarily stub PLIVO_AUTH_TOKEN.

This harness is for iterating on the turn loop / chat_manager integration
quickly: run it, type what the caller would say, see the XML Plivo would
receive back.

Usage:
    CHAT_MANAGER_URL=http://127.0.0.1:8000 python tests/text_harness.py
"""
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Disable signature verification for this harness only.
os.environ.setdefault("PLIVO_PUBLIC_BASE_URL", "http://testserver")

import config  # noqa: E402

# Monkey-patch verify_plivo to skip signature checking in this harness —
# it still returns the form params, matching the real dependency's shape.
import security  # noqa: E402


from fastapi import Request  # noqa: E402


async def _fake_verify(request: Request):
    return {k: v for k, v in (await request.form()).multi_items()}


security.verify_plivo = _fake_verify

from fastapi.testclient import TestClient  # noqa: E402
import app as gateway_app  # noqa: E402

client = TestClient(gateway_app.app)


def main() -> None:
    call_uuid = f"harness-{uuid.uuid4().hex[:8]}"
    caller = "+15551234567"

    print(f"--- /voice/answer (call_uuid={call_uuid}) ---")
    r = client.post("/voice/answer", data={"CallUUID": call_uuid, "From": caller})
    print(r.text)
    print()

    print("Type what the caller says. Empty line to quit.\n")
    while True:
        try:
            text = input("caller> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            break
        r = client.post(
            "/voice/turn",
            data={"CallUUID": call_uuid, "From": caller, "Speech": text},
        )
        print(r.text)
        print()

    r = client.post("/voice/hangup", data={"CallUUID": call_uuid})
    print(f"--- /voice/hangup -> {r.status_code} ---")


if __name__ == "__main__":
    main()
