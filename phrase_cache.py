"""Persistent cache for TTS phrases that are spoken identically every time.

Unlike audio_cache.py (per-call, deleted on hangup), entries here survive
across calls forever, keyed by everything that affects the resulting audio:
text, voice, model, and voice_settings. If any of those inputs change, the
old cached clip simply stops matching -- no risk of playing stale audio.

Phase 1 scope: only known-fixed phrases (REPROMPT, BRAIN_DOWN_MSG) go through
this cache. LLM-generated text (the greeting, normal replies) stays on
audio_cache's per-call path for now -- see mds/tts_repeat_phrase_caching_proposal.md.

Lives in the same AUDIO_DIR as audio_cache so the existing GET /audio/{id}.mp3
route serves both without change. The "phrase-" prefix is how audio_cache's
purge_expired() tells these apart from short-lived per-call clips and leaves
them alone.
"""
import hashlib
import json
import os

import config

PREFIX = "phrase-"


def _cache_key(text: str, *, voice_id: str, model_id: str, voice_settings: dict) -> str:
    payload = json.dumps(
        {
            "text": text,
            "voice_id": voice_id,
            "model_id": model_id,
            "voice_settings": voice_settings,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def audio_id_for(text: str, *, voice_id: str, model_id: str, voice_settings: dict) -> str:
    return f"{PREFIX}{_cache_key(text, voice_id=voice_id, model_id=model_id, voice_settings=voice_settings)}"


def _path(audio_id: str) -> str:
    safe = "".join(c for c in audio_id if c.isalnum() or c in "-_")
    return os.path.join(config.AUDIO_DIR, f"{safe}.mp3")


def exists(audio_id: str) -> bool:
    return os.path.exists(_path(audio_id))


def put(audio_id: str, audio_bytes: bytes) -> None:
    os.makedirs(config.AUDIO_DIR, exist_ok=True)
    with open(_path(audio_id), "wb") as fh:
        fh.write(audio_bytes)
