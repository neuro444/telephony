"""Plivo XML builders. Pure functions — no I/O, no state.

Every interpolation goes through escape(). A caller named "Bob & Sons" or a
menu item with "&" in it produces malformed XML otherwise, and the call
drops with no useful error.
"""
from xml.sax.saxutils import escape, quoteattr

import config


def _attr(value: str) -> str:
    """Quote a value for use as an XML attribute.

    escape() alone does NOT escape quotes, so a value containing " would
    terminate the attribute early and produce malformed XML. quoteattr()
    supplies the surrounding quotes itself — callers must not add their own.
    """
    return quoteattr(str(value))


def _get_input(prompt_xml: str) -> str:
    """Prompt the caller and collect their speech.

    speechModel=phone_call is Plivo's model tuned for phone audio quality.
    hints boost recognition of menu terms the default model mangles.
    """
    action_url = f"{config.PLIVO_PUBLIC_BASE_URL.rstrip('/')}/voice/turn"
    no_input_url = f"{config.PLIVO_PUBLIC_BASE_URL.rstrip('/')}/voice/no_input"
    return (
        f"<GetInput action={_attr(action_url)} method=\"POST\" "
        f'inputType="speech" speechModel={_attr(config.PLIVO_SPEECH_MODEL)} '
        f"language={_attr(config.SPEECH_LANGUAGE)} "
        f"executionTimeout={_attr(config.EXECUTION_TIMEOUT)} "
        f"speechEndTimeout={_attr(config.SPEECH_END_TIMEOUT)} "
        f"hints={_attr(config.SPEECH_HINTS)} "
        f'redirect="true">'
        f"{prompt_xml}"
        f"</GetInput>"
        # When no speech is recognized Plivo continues to the next XML verb
        # instead of calling `action`. Without this redirect, reaching the end
        # of the response disconnects the still-active caller.
        f"<Redirect method=\"POST\">{escape(no_input_url)}</Redirect>"
    )


def _dial(number: str) -> str:
    """The <Dial> half of a live transfer.

    action= is REQUIRED, not optional: if staff do not answer, Plivo posts
    DialStatus=no-answer|busy|failed there and the caller is otherwise left
    in silence. dialMusic stops the caller hearing dead air while it rings.
    """
    done_url = f"{config.PLIVO_PUBLIC_BASE_URL.rstrip('/')}/voice/transfer_done"
    return (
        f"<Dial callerId={_attr(config.PLIVO_PHONE_NUMBER)} "
        f"timeout={_attr(config.TRANSFER_TIMEOUT)} "
        f'dialMusic="real" '
        f"action={_attr(done_url)} method=\"POST\">"
        f"<Number>{escape(number)}</Number></Dial>"
    )


def speak_and_continue(text: str) -> str:
    """Plivo's own TTS, then listen — the fallback when ElevenLabs is down."""
    return f"<Response>{_get_input(f'<Speak>{escape(text)}</Speak>')}</Response>"


def speak_and_hangup(text: str) -> str:
    """Say one last thing, then end the call. Never leave a dead line."""
    return f"<Response><Speak>{escape(text)}</Speak><Hangup/></Response>"


def speak_and_transfer(text: str, number: str) -> str:
    """Spoken message, then hand the live call to staff — used when TTS is down."""
    return f"<Response><Speak>{escape(text)}</Speak>" + _dial(number) + "</Response>"


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
        + _dial(number)
        + "</Response>"
    )


# transfer_failed and apology_and_hangup were byte-identical to
# speak_and_hangup; callers use that directly.


def stream_and_continue(prompt: str, stream_url: str, token: str, *, timeout: int | None = None) -> str:
    """Play a prompt, stream one inbound utterance, then consume its result."""
    result_url = config.PLIVO_PUBLIC_BASE_URL.rstrip("/") + "/voice/stream_result/" + token
    status_url = config.PLIVO_PUBLIC_BASE_URL.rstrip("/") + "/voice/stream_status"
    return (
        f"<Response>{prompt}"
        f'<Stream bidirectional="true" audioTrack="inbound" keepCallAlive="true" '
        f'contentType="audio/x-mulaw;rate=8000" '
        f'statusCallbackUrl={_attr(status_url)} statusCallbackMethod="POST" '
        f'streamTimeout={_attr((config.DEEPGRAM_TURN_TIMEOUT if timeout is None else timeout) + 10)}>'
        f"{escape(stream_url)}</Stream>"
        f'<Redirect method="POST">{escape(result_url)}</Redirect></Response>'
    )
