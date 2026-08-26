"""Demo: fixed-phrase TTS caching (phase 1).

Drives the real gateway app -- real phrase_cache.py, real audio_cache.py,
real file I/O on a throwaway AUDIO_DIR -- through three simulated calls, to
show the persistent phrase cache surviving hangup and being reused across
completely separate calls.

Only the two genuinely external dependencies are stubbed: the Plivo
signature check (needs a live signed webhook) and the ElevenLabs network
call (needs a real, billed API key). Everything else -- the cache-key
hashing, the disk cache, the hangup purge -- is the real production code.

Usage:
    python scripts/demo_phrase_cache.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("PLIVO_PUBLIC_BASE_URL", "https://voice.test")
os.environ.setdefault("PLIVO_PHONE_NUMBER", "+14042071333")
os.environ.setdefault("PLIVO_TRANSFER_NUMBER", "+16468753366")
os.environ.setdefault("PLIVO_AUTH_TOKEN", "test-auth-token")

import config  # noqa: E402

AUDIO_DIR = tempfile.mkdtemp()
config.AUDIO_DIR = AUDIO_DIR
config.COST_LOG_PATH = os.path.join(AUDIO_DIR, "costs.jsonl")
config.ORDERS_LOG_PATH = os.path.join(AUDIO_DIR, "orders.jsonl")

import app as gateway  # noqa: E402
import security  # noqa: E402
from fastapi import Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


async def _no_verify(request: Request) -> dict:
    """Stand-in for Plivo signature verification -- a real webhook call
    would carry a signed header this demo has no way to produce offline."""
    return {k: v for k, v in (await request.form()).multi_items()}


gateway.app.dependency_overrides[security.verify_plivo] = _no_verify

synth_calls: list[str] = []


def fake_synthesize(text: str) -> bytes:
    """Stand-in for the real ElevenLabs call -- what we're trying to prove
    is that this is NOT invoked on a cache hit, so it just needs to be
    observable, not real audio."""
    synth_calls.append(text)
    return b"FAKEMP3-" + text.encode()[:10]


gateway.synthesize = fake_synthesize

client = TestClient(gateway.app)


def line(title: str) -> None:
    print(f"\n--- {title} ---")


def status() -> None:
    print(f"  ElevenLabs calls so far: {len(synth_calls)} {synth_calls}")
    print(f"  files in AUDIO_DIR:      {sorted(os.listdir(AUDIO_DIR))}")


print(f"AUDIO_DIR (throwaway): {AUDIO_DIR}")

line("call A: caller says nothing -> REPROMPT synthesized for the first time")
client.post("/voice/turn", data={"CallUUID": "call-A", "From": "+15551111111", "Speech": ""})
status()

line("call A hangs up -- per-call audio is purged, but the phrase clip is not")
client.post("/voice/hangup", data={"CallUUID": "call-A", "From": "+15551111111"})
status()

line("call B: a totally different call, GetInput times out -> same REPROMPT text")
client.post("/voice/no_input", data={"CallUUID": "call-B", "From": "+15552222222"})
status()

assert len(synth_calls) == 1, "cache miss on call B -- phrase caching is broken"
print("\nPASS: call B's REPROMPT was a cache hit -- zero ElevenLabs cost, "
      "despite being a different call after the first one hung up.")
