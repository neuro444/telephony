"""ElevenLabs Scribe v2 Realtime streaming, using the same one-utterance Plivo transport.

Plivo's audio is already 8kHz mulaw and the API accepts that natively via
audio_format=ulaw_8000 — no decode or resample needed, unlike Sarvam which
requires PCM.
"""
import asyncio
import base64
import json
from urllib.parse import urlencode

from websockets.asyncio.client import connect
import config


def connection_url() -> str:
    return "wss://api.elevenlabs.io/v1/speech-to-text/realtime?" + urlencode({
        "model_id": "scribe_v2_realtime", "audio_format": "ulaw_8000",
        "commit_strategy": "vad", "language_code": config.ELEVENLABS_STT_LANGUAGE,
    })


async def stream_utterance(plivo, call_uuid: str) -> str:
    if not config.ELEVENLABS_API_KEY:
        raise RuntimeError("ELEVENLABS_API_KEY is required")
    async with connect(connection_url(),
                       additional_headers={"xi-api-key": config.ELEVENLABS_API_KEY},
                       open_timeout=10, close_timeout=2, max_size=1024 * 1024) as scribe:
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
                    await scribe.send(json.dumps({
                        "message_type": "input_audio_chunk",
                        "audio_base_64": event["media"]["payload"],
                        "commit": False,
                    }))
                elif event.get("event") == "stop":
                    await scribe.send(json.dumps({
                        "message_type": "input_audio_chunk",
                        "audio_base_64": "",
                        "commit": True,
                    }))
                    return ""

        async def receive():
            async for raw in scribe:
                event = json.loads(raw)
                if event.get("message_type") == "committed_transcript":
                    transcript = event.get("text", "").strip()
                    if transcript:
                        return transcript
            raise RuntimeError("ElevenLabs closed before transcription")

        sender, receiver = asyncio.create_task(forward()), asyncio.create_task(receive())
        try:
            done, _ = await asyncio.wait([sender, receiver],
                timeout=config.ELEVENLABS_STT_TURN_TIMEOUT, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()  # propagate transport/provider errors
            return receiver.result() if receiver in done else ""
        finally:
            sender.cancel()
            receiver.cancel()
            await asyncio.gather(sender, receiver, return_exceptions=True)
