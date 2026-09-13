"""The ONLY place in this repo that talks to chat_manager.

The conversation contract remains text-in / text-out. Optional customer audio
is diagnostic evidence attached to the corresponding user message; it is not
sent to the language model.
"""
import logging

import httpx

import config

logger = logging.getLogger(__name__)


class BrainUnavailable(RuntimeError):
    """Raised whenever chat_manager cannot be reached or returns garbage.

    Callers must treat this as "apologize and transfer to a human" — never
    a dead line.
    """


def chat(user_id: str, session_id: str | None, message: str, customer_audio: dict | None = None) -> dict:
    """Call chat_manager /chat. Returns its JSON verbatim.

    Deliberately NOT modeled as a typed schema: chat_manager passes new
    prompt-defined fields straight through, and an unknown key must never
    be a parse error here. Callers should use .get() defensively for any
    optional flag (e.g. Transfer_to_Manager may not exist on every chat_manager
    version — treat a missing key the same as False).
    """
    headers = {}
    if config.BRAIN_API_KEY:
        headers[config.BRAIN_API_KEY_HEADER] = config.BRAIN_API_KEY

    try:
        r = httpx.post(
            f"{config.CHAT_MANAGER_URL}/chat",
            json={"user_id": user_id, "session_id": session_id, "message": message,
                  **({"customer_audio": customer_audio} if customer_audio else {})},
            headers=headers,
            timeout=config.BRAIN_TIMEOUT,
        )
        r.raise_for_status()
        return r.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.exception("chat_manager call failed user_id=%s", user_id)
        raise BrainUnavailable(str(exc)) from exc
