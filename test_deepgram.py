#!/usr/bin/env python3
"""
Standalone Deepgram test — verifies DEEPGRAM_API_KEY works and transcribes
a real audio file, without touching the gateway or a live call.

Usage:
    python3 test_deepgram.py path/to/audio.wav

Any common audio file works (wav, mp3, m4a) — Deepgram auto-detects format
when Content-Type doesn't match mu-law. For a quick test with no audio file
handy, record a few seconds on your phone's voice memo app and use that.

Reads DEEPGRAM_API_KEY from .env automatically.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "speech"))


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


def main():
    if len(sys.argv) != 2:
        print("Usage: python3 test_deepgram.py path/to/audio.wav")
        return

    env = load_env()
    key = env.get("DEEPGRAM_API_KEY", "")
    if not key:
        print("DEEPGRAM_API_KEY not set in .env — add it first.")
        print("Get one free at https://console.deepgram.com (free $200 credit)")
        return

    os.environ["DEEPGRAM_API_KEY"] = key

    import config
    import deepgram_stt

    with open(sys.argv[1], "rb") as fh:
        audio_bytes = fh.read()

    print(f"Sending {len(audio_bytes)} bytes to Deepgram...")
    try:
        # Let Deepgram auto-detect format for a generic file (not mu-law).
        text = deepgram_stt.transcribe(audio_bytes, mimetype="audio/wav")
        print(f"\nTranscript: {text!r}")
        print("\nPASS: Deepgram is working.")
    except deepgram_stt.STTUnavailable as exc:
        print(f"\nFAIL: {exc}")


if __name__ == "__main__":
    main()
