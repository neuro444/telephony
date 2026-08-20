"""
Single control panel for the Plivo telephony gateway. Env-driven, mirrors
chat_manager's config.py convention so moving between the two repos costs
no re-orientation.
"""
import os


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


# ── Plivo ─────────────────────────────
PLIVO_AUTH_ID = os.getenv("PLIVO_AUTH_ID", "")
PLIVO_AUTH_TOKEN = os.getenv("PLIVO_AUTH_TOKEN", "")  # signs webhooks
PLIVO_PHONE_NUMBER = os.getenv("PLIVO_PHONE_NUMBER", "")  # caller ID on transfer
PLIVO_PUBLIC_BASE_URL = os.getenv("PLIVO_PUBLIC_BASE_URL", "")
# MUST exactly match the public URL Plivo is configured to call — the
# webhook signature is computed over it. Behind nginx, request.url is NOT
# reliable for this; always use this value instead. e.g. https://voice.example.com

# ── Brain (chat_manager) ──────────────
CHAT_MANAGER_URL = os.getenv("CHAT_MANAGER_URL", "http://chat-manager-api:8000")
BRAIN_API_KEY = os.getenv("BRAIN_API_KEY", "")
BRAIN_API_KEY_HEADER = os.getenv("BRAIN_API_KEY_HEADER", "X-API-Key")
BRAIN_TIMEOUT = _float("BRAIN_TIMEOUT", 20.0)

# ── Transfer ──────────────────────────
PLIVO_TRANSFER_NUMBER = os.getenv("PLIVO_TRANSFER_NUMBER", "")  # manager, E.164
TRANSFER_TIMEOUT = _int("TRANSFER_TIMEOUT", 25)

# ── Speech ────────────────────────────
# ElevenLabs voice + model. The brain's .env already names a chosen
# Indian-accent voice (ELEVEN_VOICE) — use the same id here.
# v1 uses Plivo's native <GetInput> speech recognition — no STT service call
# in the turn loop. See speech/base.py if a self-hosted/managed STT ever
# needs to be swapped in (e.g. if SpeechConfidenceScore proves too low on
# Indian-accented menu items).
SPEECH_LANGUAGE = os.getenv("SPEECH_LANGUAGE", "en-US")
SPEECH_END_TIMEOUT = os.getenv("SPEECH_END_TIMEOUT", "auto")
EXECUTION_TIMEOUT = _int("EXECUTION_TIMEOUT", 15)
SPEECH_HINTS = os.getenv("SPEECH_HINTS", "")  # generated from menu/menu_flat.json
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "")
ELEVENLABS_MODEL_ID = os.getenv("ELEVENLABS_MODEL_ID", "eleven_turbo_v2_5")

# ── Audio cache ───────────────────────
AUDIO_DIR = os.getenv("AUDIO_DIR", "/app/audio")
AUDIO_TTL_SECONDS = _int("AUDIO_TTL_SECONDS", 600)

# ── Orders ────────────────────────────
ORDERS_LOG_PATH = os.getenv("ORDERS_LOG_PATH", "/app/orders/orders.jsonl")

# ── Copy ──────────────────────────────
# The greeting itself is NOT configured here — chat_manager writes it, and
# greets returning callers by name. This is only the opening message the
# gateway SENDS to chat_manager to elicit that greeting; its prompt already
# treats a bare "hello" as a request for a fresh welcome.
GREETING_PROMPT = os.getenv("GREETING_PROMPT", "hello")

# The messages below are only used when chat_manager or ElevenLabs CANNOT
# answer. Everything a caller hears on a healthy call comes from the brain.
REPROMPT = os.getenv(
    "REPROMPT", "Sorry, I didn't catch that. Could you repeat it?"
)
BRAIN_DOWN_MSG = os.getenv(
    "BRAIN_DOWN_MSG", "Sorry, let me get someone to help you."
)
TRANSFER_FAILED_MSG = os.getenv(
    "TRANSFER_FAILED_MSG",
    "Sorry, no one is free right now. Please call back shortly.",
)
