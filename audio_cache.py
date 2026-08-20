"""Short-lived TTS audio cache.

Plivo fetches <Play> URLs over the public internet, so synthesized speech
must exist as a file Plivo can GET, not be streamed inline in the webhook
response. Files are named with an unguessable id and purged on hangup (and
opportunistically by TTL) so nothing outlives its call.
"""
import logging
import os
import time
import uuid

import config

logger = logging.getLogger(__name__)


def _path(audio_id: str) -> str:
    safe = "".join(c for c in audio_id if c.isalnum() or c in "-_")
    return os.path.join(config.AUDIO_DIR, f"{safe}.mp3")


def write(audio_bytes: bytes, call_uuid: str) -> str:
    """Write mp3 bytes to disk, return the public URL to hand to <Play>."""
    os.makedirs(config.AUDIO_DIR, exist_ok=True)
    audio_id = f"{call_uuid}-{uuid.uuid4().hex[:8]}"
    with open(_path(audio_id), "wb") as fh:
        fh.write(audio_bytes)
    return f"{config.PLIVO_PUBLIC_BASE_URL.rstrip('/')}/audio/{audio_id}.mp3"


def read(audio_id: str) -> bytes | None:
    path = _path(audio_id)
    if not os.path.exists(path):
        return None
    with open(path, "rb") as fh:
        return fh.read()


def purge(call_uuid: str) -> None:
    """Delete every cached clip belonging to one call (fires on hangup)."""
    if not os.path.isdir(config.AUDIO_DIR):
        return
    prefix = f"{call_uuid}-"
    for name in os.listdir(config.AUDIO_DIR):
        if name.startswith(prefix):
            try:
                os.remove(os.path.join(config.AUDIO_DIR, name))
            except OSError:
                logger.warning("could not purge audio file %s", name)


def purge_expired() -> None:
    """Best-effort sweep for anything that outlived AUDIO_TTL_SECONDS
    (a call that never reached /voice/hangup, e.g. a hard network drop)."""
    if not os.path.isdir(config.AUDIO_DIR):
        return
    now = time.time()
    for name in os.listdir(config.AUDIO_DIR):
        path = os.path.join(config.AUDIO_DIR, name)
        try:
            if now - os.path.getmtime(path) > config.AUDIO_TTL_SECONDS:
                os.remove(path)
        except OSError:
            continue
