"""
Single control panel for the Plivo telephony gateway. Env-driven, mirrors
chat_manager's config.py convention so moving between the two repos costs
no re-orientation.
"""
import os
from contextvars import ContextVar


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
TELEPHONY_PROVIDER = os.getenv("TELEPHONY_PROVIDER", "plivo").lower()
if TELEPHONY_PROVIDER not in {"twilio", "plivo"}:
    raise ValueError("TELEPHONY_PROVIDER must be twilio or plivo")
_carrier_context: ContextVar[str | None] = ContextVar("telephone_carrier", default=None)


def current_carrier() -> str:
    return _carrier_context.get() or TELEPHONY_PROVIDER


def set_carrier(provider: str) -> None:
    _carrier_context.set(provider)


TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "")
TWILIO_PUBLIC_BASE_URL = os.getenv("TWILIO_PUBLIC_BASE_URL", "")
TWILIO_TRANSFER_NUMBER = os.getenv("TWILIO_TRANSFER_NUMBER", "")


def public_base_url() -> str:
    return (TWILIO_PUBLIC_BASE_URL if current_carrier() == "twilio" else PLIVO_PUBLIC_BASE_URL).rstrip("/")


def transfer_number() -> str:
    # Preserve the existing manager destination when changing carriers.
    return (TWILIO_TRANSFER_NUMBER or PLIVO_TRANSFER_NUMBER) if current_carrier() == "twilio" else PLIVO_TRANSFER_NUMBER


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

# Server-to-server dashboard access for order, handoff, and cost feeds.
DASHBOARD_API_KEY = os.getenv("DASHBOARD_API_KEY", "")

# ── Transfer ──────────────────────────
PLIVO_TRANSFER_NUMBER = os.getenv("PLIVO_TRANSFER_NUMBER", "")  # manager, E.164
TRANSFER_TIMEOUT = _int("TRANSFER_TIMEOUT", 25)

# ── Speech ────────────────────────────
# ElevenLabs voice + model. The brain's .env already names a chosen
# Indian-accent voice (ELEVEN_VOICE) — use the same id here.
STT_PROVIDER = os.getenv("STT_PROVIDER", "deepgram").lower()
if STT_PROVIDER not in {"plivo", "deepgram", "sarvam", "elevenlabs", "assemblyai", "whisper_manglish_hf"}:
    raise ValueError("STT_PROVIDER must be plivo, deepgram, sarvam, elevenlabs, assemblyai, or whisper_manglish_hf")
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY", "")
DEEPGRAM_MODEL = os.getenv("DEEPGRAM_MODEL", "nova-3")
DEEPGRAM_LANGUAGE = os.getenv("DEEPGRAM_LANGUAGE", "multi")
DEEPGRAM_ENDPOINTING_MS = _int("DEEPGRAM_ENDPOINTING_MS", 700)
DEEPGRAM_TURN_TIMEOUT = _int("DEEPGRAM_TURN_TIMEOUT", 30)
SARVAM_API_KEY = os.getenv("SARVAM_API_KEY", "")
SARVAM_MODEL = os.getenv("SARVAM_MODEL", "saaras:v3")
SARVAM_LANGUAGE = os.getenv("SARVAM_LANGUAGE", "en-IN")
SARVAM_TURN_TIMEOUT = _int("SARVAM_TURN_TIMEOUT", 30)
ELEVENLABS_STT_TURN_TIMEOUT = _int("ELEVENLABS_STT_TURN_TIMEOUT", 30)
ELEVENLABS_STT_LANGUAGE = os.getenv("ELEVENLABS_STT_LANGUAGE", "en")
PLIVO_SPEECH_MODEL = os.getenv("PLIVO_SPEECH_MODEL", "phone_call")
if PLIVO_SPEECH_MODEL not in {"phone_call", "default", "command_and_search"}:
    raise ValueError("Unsupported PLIVO_SPEECH_MODEL")
SPEECH_LANGUAGE = os.getenv("SPEECH_LANGUAGE", "en-US")
SPEECH_END_TIMEOUT = os.getenv("SPEECH_END_TIMEOUT", "auto")
EXECUTION_TIMEOUT = _int("EXECUTION_TIMEOUT", 15)
SPEECH_HINTS = os.getenv("SPEECH_HINTS", "")  # generated from menu/menu_flat.json
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "")
ELEVENLABS_MODEL_ID = os.getenv("ELEVENLABS_MODEL_ID", "eleven_turbo_v2_5")
# Experimental: self-hosted Manglish Whisper fine-tune on a Hugging Face
# Inference Endpoint. One HTTP call per utterance, not a streaming session;
# scale-to-zero cold starts are possible after idle periods.
HF_WHISPER_ENDPOINT_URL = os.getenv("HF_WHISPER_ENDPOINT_URL", "")
HF_WHISPER_API_TOKEN = os.getenv("HF_WHISPER_API_TOKEN", "")
HF_WHISPER_TURN_TIMEOUT = _int("HF_WHISPER_TURN_TIMEOUT", 60)

# Customer audio debug capture: opt in; optional comma-separated E.164 test callers.
DEBUG_CUSTOMER_AUDIO = os.getenv("DEBUG_CUSTOMER_AUDIO", "false").lower() in {"true", "1", "yes"}
DEBUG_AUDIO_CALLERS = {x.strip() for x in os.getenv("DEBUG_AUDIO_CALLERS", "").split(",") if x.strip()}

# ── Audio cache ───────────────────────
AUDIO_DIR = os.getenv("AUDIO_DIR", "/data/audio")
AUDIO_TTL_SECONDS = _int("AUDIO_TTL_SECONDS", 600)
# Persistent phrase cache — survives container restarts via mounted volume.
# Stores ElevenLabs audio for fixed phrases so they are never synthesized twice.
PHRASE_CACHE_DIR = os.getenv("PHRASE_CACHE_DIR", "/app/phrase_cache")

# ── Orders ────────────────────────────
ORDERS_LOG_PATH = os.getenv("ORDERS_LOG_PATH", "/data/orders/orders.jsonl")
COST_LOG_PATH = os.getenv("COST_LOG_PATH", "/data/cost/costs.jsonl")
PRINT_API_URL = os.getenv("PRINT_API_URL", "")
PRINT_API_KEY = os.getenv("PRINT_API_KEY", "")

# ── Privacy ───────────────────────────
# When True (default), phone numbers are masked before being written to
# on-disk JSONL log files (costs.jsonl, orders.jsonl). Set to "false" in
# local dev or CI so unit tests can assert on exact phone-number strings
# without a decode step. Must be "true" in any production deployment.
MASK_PII_LOGS = os.getenv("MASK_PII_LOGS", "true").lower() in ("1", "true", "yes")

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

STT_DOWN_MSG = os.getenv(
    "STT_DOWN_MSG",
    "Sorry, we're having trouble hearing you. Connecting you to our team now.",
)

# AssemblyAI streaming. Accept the standard SDK key name as an alias.
ASSEMBLY_API_KEY = os.getenv("ASSEMBLY_API_KEY", "") or os.getenv("ASSEMBLYAI_API_KEY", "")
ASSEMBLY_MODEL = os.getenv("ASSEMBLY_MODEL", "universal-streaming-english")
ASSEMBLY_TURN_TIMEOUT = _int("ASSEMBLY_TURN_TIMEOUT", 30)
if ASSEMBLY_MODEL not in {"universal-streaming-english", "universal-streaming-multilingual", "universal-3-5-pro"}:
    raise ValueError("Unsupported ASSEMBLY_MODEL")
