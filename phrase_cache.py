"""Persistent cross-call TTS phrase cache.

audio_cache.py is per-call, ephemeral: every clip is deleted on hangup.
This module is permanent: a phrase synthesized once is stored forever and
replayed on every subsequent call that produces the same text+voice+model.

Cache key = SHA-256 of (voice_id | model_id | stability | similarity_boost |
normalized_text). Any change in any of those parameters produces a new key,
so stale audio from an old voice config is never replayed — old files simply
become unreachable (new key → miss → fresh synthesis).

The caller (app.py) never needs to know whether it got a hit or a miss --
it always receives a public URL to hand to Plivo's <Play>, exactly as it
does today with audio_cache.write().
"""
import hashlib
import logging
import os

import config

logger = logging.getLogger(__name__)

# Voice settings are currently hardcoded in speech/elevenlabs_tts.py.
# Included in the cache key so any future change to either value
# automatically invalidates old entries (new key → miss → fresh synthesis).
_STABILITY = "0.5"
_SIMILARITY_BOOST = "0.75"


def _cache_key(text: str) -> str:
    """Return a stable hex digest that uniquely identifies this phrase+voice."""
    raw = "|".join([
        config.ELEVENLABS_VOICE_ID,
        config.ELEVENLABS_MODEL_ID,
        _STABILITY,
        _SIMILARITY_BOOST,
        text.strip().lower(),   # normalize: trim + lowercase so trivial
    ])                          # formatting differences don't produce duplicates
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _path(key: str) -> str:
    return os.path.join(config.PHRASE_CACHE_DIR, f"{key}.mp3")


def _public_url(key: str) -> str:
    return f"{config.PLIVO_PUBLIC_BASE_URL.rstrip('/')}/phrase/{key}.mp3"


def get(text: str) -> str | None:
    """Return a public <Play> URL if this phrase is already cached, else None."""
    key = _cache_key(text)
    if os.path.exists(_path(key)):
        logger.debug("phrase cache HIT: %.60s", text)
        return _public_url(key)
    return None


def put(text: str, audio_bytes: bytes) -> str:
    """Store audio permanently and return its public URL."""
    os.makedirs(config.PHRASE_CACHE_DIR, exist_ok=True)
    key = _cache_key(text)
    with open(_path(key), "wb") as fh:
        fh.write(audio_bytes)
    logger.info("phrase cache STORED: %.60s -> %s.mp3", text, key[:8])
    return _public_url(key)


def read(key: str) -> bytes | None:
    """Return raw mp3 bytes for the given hash key, or None if not found.

    Called by GET /phrase/{key}.mp3 so Plivo can fetch the audio it was
    handed a URL for.
    """
    path = _path(key)
    if not os.path.exists(path):
        return None
    with open(path, "rb") as fh:
        return fh.read()
