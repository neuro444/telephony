#!/usr/bin/env python3
"""Replay downloaded .mulaw through one external STT adapter; never calls the brain.

Run with server environment / credentials. Provider requests are billed.
Use --hints-file with the downloaded diagnostics JSON to reuse the captured hints.
"""
import argparse
import asyncio
import base64
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config


async def replay(raw, provider):
    from speech.deepgram_stt import stream_utterance as deepgram
    from speech.assemblyai_stt import stream_utterance as assemblyai
    from speech.sarvam_stt import stream_utterance as sarvam
    from speech.elevenlabs_stt import stream_utterance as elevenlabs

    class Socket:
        offset = -1

        async def receive_json(self):
            if self.offset < 0:
                self.offset = 0
                return {"event": "start", "start": {"callId": "audio-replay",
                        "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000}}}
            if self.offset >= len(raw) + 40000:
                await asyncio.Future()
            await asyncio.sleep(.02)
            chunk = raw[self.offset:self.offset+160] if self.offset < len(raw) else b'\xff' * 160
            self.offset += len(chunk)
            return {"event": "media", "media": {"payload": base64.b64encode(chunk).decode()}}

    return await {"deepgram": deepgram, "assemblyai": assemblyai,
                  "sarvam": sarvam, "elevenlabs": elevenlabs}[provider](Socket(), "audio-replay")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("--provider", choices=["deepgram", "assemblyai", "sarvam", "elevenlabs"], required=True)
    parser.add_argument("--hints-file", type=Path)
    args = parser.parse_args()
    if args.audio.stat().st_size > 480000:
        parser.error("Maximum 60 seconds / 480000 bytes")
    raw = args.audio.read_bytes()
    if not raw:
        parser.error("Empty audio")
    if args.hints_file:
        config.SPEECH_HINTS = json.loads(args.hints_file.read_text())["hints"]
    import time
    started = time.monotonic()
    try:
        text = asyncio.run(replay(raw, args.provider))
    except Exception as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__}))
        return 1
    print(json.dumps({"provider": args.provider, "sha256": hashlib.sha256(raw).hexdigest(),
                      "transcript": text, "elapsed_ms": round((time.monotonic()-started)*1000),
                      "pacing": "20ms, with 5s synthetic trailing silence for endpointing"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
