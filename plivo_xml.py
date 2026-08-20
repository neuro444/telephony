"""Plivo XML builders. Pure functions — no I/O, no state.

Every interpolation goes through escape(). A caller named "Bob & Sons" or a
menu item with "&" in it produces malformed XML otherwise, and the call
drops with no useful error.
"""
from xml.sax.saxutils import escape

import config


def _get_input(prompt_xml: str) -> str:
    """Prompt the caller and collect their speech.

    speechModel=phone_call is Plivo's model tuned for phone audio quality.
    hints boost recognition of menu terms the default model mangles.
    """
    return (
        f'<GetInput action="{config.PLIVO_PUBLIC_BASE_URL}/voice/turn" method="POST" '
        f'inputType="speech" speechModel="phone_call" '
        f'language="{config.SPEECH_LANGUAGE}" '
        f'executionTimeout="{config.EXECUTION_TIMEOUT}" '
        f'speechEndTimeout="{config.SPEECH_END_TIMEOUT}" '
        f'hints="{escape(config.SPEECH_HINTS)}" '
        f'redirect="true">'
        f"{prompt_xml}"
        f"</GetInput>"
    )


def greeting(text: str) -> str:
    return f"<Response>{_get_input(f'<Speak>{escape(text)}</Speak>')}</Response>"


def play_and_continue(audio_url: str) -> str:
    """Speak the agent's answer, then listen for the next utterance."""
    return f"<Response>{_get_input(f'<Play>{escape(audio_url)}</Play>')}</Response>"


def play_and_hangup(audio_url: str) -> str:
    """Play the sign-off, then hang up.

    <Hangup/> executes only after <Play> finishes, so the caller always
    hears the full goodbye — no explicit sleep on end_delay_seconds needed.
    """
    return f"<Response><Play>{escape(audio_url)}</Play><Hangup/></Response>"


def play_and_transfer(audio_url: str, number: str) -> str:
    """Hand the live call to staff (DID forward).

    action= is REQUIRED, not optional: if staff do not answer, Plivo posts
    DialStatus=no-answer|busy|failed there and the caller is otherwise left
    in silence. dialMusic stops the caller hearing dead air while it rings.
    """
    return (
        f"<Response><Play>{escape(audio_url)}</Play>"
        f'<Dial callerId="{escape(config.PLIVO_PHONE_NUMBER)}" '
        f'timeout="{config.TRANSFER_TIMEOUT}" '
        f'dialMusic="real" '
        f'action="{config.PLIVO_PUBLIC_BASE_URL}/voice/transfer_done" method="POST">'
        f"<Number>{escape(number)}</Number></Dial></Response>"
    )


def transfer_failed(text: str) -> str:
    """Staff did not pick up. Never drop the caller into silence."""
    return f"<Response><Speak>{escape(text)}</Speak><Hangup/></Response>"


def apology_and_hangup(text: str) -> str:
    """Fallback when the brain is unreachable — never leave a dead line."""
    return f"<Response><Speak>{escape(text)}</Speak><Hangup/></Response>"
