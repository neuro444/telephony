"""ElevenLabs text-to-speech.

Plivo fetches <Play> URLs over the public internet, so synthesized audio
must be written to disk and served from GET /audio/{id}.mp3 — do not stream
TTS inline in a webhook response, Plivo expects XML there, not audio bytes.
"""
import logging

import httpx

import config

logger = logging.getLogger(__name__)

_ELEVENLABS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"


class TTSUnavailable(RuntimeError):
    """Raised when ElevenLabs cannot be reached or returns an error."""


def synthesize(text: str) -> bytes:
    """Return mp3 bytes for the given text via ElevenLabs."""
    if not config.ELEVENLABS_API_KEY or not config.ELEVENLABS_VOICE_ID:
        raise TTSUnavailable("ELEVENLABS_API_KEY / ELEVENLABS_VOICE_ID not configured")

    url = _ELEVENLABS_URL.format(voice_id=config.ELEVENLABS_VOICE_ID)
    try:
        r = httpx.post(
            url,
            headers={
                "xi-api-key": config.ELEVENLABS_API_KEY,
                "Content-Type": "application/json",
            },
            json={
                "text": text,
                "model_id": "eleven_v3",
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
            },
            timeout=30.0,
        )
        r.raise_for_status()
        return r.content
    except httpx.HTTPError as exc:
        logger.exception("ElevenLabs TTS failed")
        raise TTSUnavailable(str(exc)) from exc
