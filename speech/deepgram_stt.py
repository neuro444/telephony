"""One live caller utterance over Deepgram's native WebSocket protocol."""
import asyncio
import base64
import json
from urllib.parse import urlencode

from websockets.asyncio.client import connect

import config

# Deepgram's keyterm prompting (nova-3 only) rejects the whole request with
# HTTP 400 ("Keyterm limit exceeded... maximum number of tokens... is 500")
# well before any word-count of 500 is reached - "tokens" here means
# Deepgram's own subword tokenization, which is not reproducible client-side
# and runs far higher per word for non-English proper nouns (e.g. Kerala
# dish names) than for plain English. Verified empirically against the live
# API: 72 of our menu keyterms (195 whitespace-split words) succeeds, 73
# terms (197 words) fails. Capping at 60 keeps a safety margin below that
# measured boundary instead of trusting Deepgram's stated limit or trying to
# reproduce its tokenizer.
DEEPGRAM_MAX_KEYTERMS = 60


def _capped_keyterms(keyterms: list[str]) -> list[str]:
    return keyterms[:DEEPGRAM_MAX_KEYTERMS]


async def stream_utterance(plivo, call_uuid: str) -> str:
    if not config.DEEPGRAM_API_KEY:
        raise RuntimeError("Deepgram is not configured")
    keyterms = [hint.strip() for hint in config.SPEECH_HINTS.split(",") if hint.strip()]
    params = {
        "model": config.DEEPGRAM_MODEL,
        "language": config.SPEECH_LANGUAGE,
        "encoding": "mulaw", "sample_rate": 8000, "channels": 1,
        "interim_results": "true", "smart_format": "true",
        "endpointing": config.DEEPGRAM_ENDPOINTING_MS,
        "keyterm": _capped_keyterms(keyterms),
    }
    parts = []
    async with connect(
        "wss://api.deepgram.com/v1/listen?" + urlencode(params, doseq=True),
        additional_headers={"Authorization": "Token " + config.DEEPGRAM_API_KEY},
        open_timeout=5, close_timeout=2, max_size=1024 * 1024,
    ) as deepgram:
        async def forward():
            started = False
            while True:
                event = await plivo.receive_json()
                if event.get("event") == "start":
                    start = event["start"]
                    fmt = start.get("mediaFormat", {})
                    if (start.get("callId") != call_uuid
                            or fmt.get("encoding") != "audio/x-mulaw"
                            or int(fmt.get("sampleRate", 0)) != 8000):
                        raise ValueError("Unexpected Plivo stream metadata")
                    started = True
                elif event.get("event") == "media":
                    if not started:
                        raise ValueError("Audio arrived before stream metadata")
                    await deepgram.send(base64.b64decode(event["media"]["payload"], validate=True))
                elif event.get("event") == "stop":
                    return

        async def receive():
            async for raw in deepgram:
                event = json.loads(raw)
                if event.get("type") == "Error":
                    raise RuntimeError("Deepgram rejected the stream")
                if event.get("type") != "Results":
                    continue
                alternatives = event.get("channel", {}).get("alternatives", [])
                text = alternatives[0].get("transcript", "").strip() if alternatives else ""
                if event.get("is_final") and text:
                    parts.append(text)
                if event.get("speech_final") and parts:
                    return
            raise RuntimeError("Deepgram closed before utterance completed")

        async def keepalive():
            while True:
                await asyncio.sleep(4)
                await deepgram.send(json.dumps({"type": "KeepAlive"}))

        tasks = [asyncio.create_task(fn()) for fn in (forward, receive, keepalive)]
        try:
            done, _ = await asyncio.wait(tasks, timeout=config.DEEPGRAM_TURN_TIMEOUT,
                                         return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    return " ".join(parts)
