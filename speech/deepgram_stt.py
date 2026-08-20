"""Deepgram speech-to-text.

Implements the SpeechToText protocol from speech/base.py. Not wired into
app.py's /voice/turn by default -- the gateway currently uses Plivo's native
<GetInput> speech recognition (see plivo_xml.py). This module exists so the
STT step can be swapped in later without a rewrite: replace the <GetInput>
block in plivo_xml.py with a <Record> block, and call transcribe() on the
recorded audio inside /voice/turn instead of reading params["Speech"].

Pricing (checked 2026-08-19): Deepgram batch (nova-3) is ~$0.0043/min,
cheaper than OpenAI Whisper API's ~$0.006/min for the same non-streaming
use case. Deepgram also supports keyterm boosting for menu-item accuracy
(see config.SPEECH_HINTS / scripts/generate_hints.py), which Whisper does
not offer.
"""
import logging

import httpx

import config

logger = logging.getLogger(__name__)

_DEEPGRAM_URL = "https://api.deepgram.com/v1/listen"


class STTUnavailable(RuntimeError):
    """Raised when Deepgram cannot be reached or returns an error."""


def transcribe(audio_bytes: bytes, *, mimetype: str = "audio/x-mulaw;rate=8000") -> str:
    """Transcribe recorded call audio to text via Deepgram's batch API.

    Uses nova-3 with keyterm boosting from config.SPEECH_HINTS (the same
    menu-derived hint list used for Plivo's native ASR) so accuracy on
    menu items stays consistent regardless of which STT path is active.

    mimetype defaults to Plivo's own mu-law 8kHz recording format so no
    transcoding step is needed between Plivo and Deepgram.
    """
    if not config.DEEPGRAM_API_KEY:
        raise STTUnavailable("DEEPGRAM_API_KEY not configured")

    params = {
        "model": "nova-3",
        "smart_format": "true",
        "punctuate": "true",
    }
    if config.SPEECH_HINTS:
        # Deepgram's keyterm param accepts multiple values; httpx handles a
        # list value as repeated query params automatically.
        params["keyterm"] = [h.strip() for h in config.SPEECH_HINTS.split(",") if h.strip()]

    try:
        r = httpx.post(
            _DEEPGRAM_URL,
            params=params,
            headers={
                "Authorization": f"Token {config.DEEPGRAM_API_KEY}",
                "Content-Type": mimetype,
            },
            content=audio_bytes,
            timeout=20.0,
        )
        r.raise_for_status()
        data = r.json()
        return (
            data.get("results", {})
            .get("channels", [{}])[0]
            .get("alternatives", [{}])[0]
            .get("transcript", "")
        )
    except (httpx.HTTPError, KeyError, IndexError) as exc:
        logger.exception("Deepgram transcription failed")
        raise STTUnavailable(str(exc)) from exc
