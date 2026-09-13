"""TwiML for the existing prompt/listen/reply call flow; STT stays external."""
from xml.sax.saxutils import escape, quoteattr
import config


def _dial(number: str) -> str:
    return (
        f'<Dial callerId={quoteattr(config.TWILIO_PHONE_NUMBER)} '
        f'timeout={quoteattr(str(config.TRANSFER_TIMEOUT))} '
        f'action={quoteattr(config.public_base_url() + "/twilio/voice/transfer_done")} method="POST">'
        f'<Number>{escape(number)}</Number></Dial>'
    )


def speak_and_hangup(text: str) -> str:
    return f'<Response><Say>{escape(text)}</Say><Hangup/></Response>'


def speak_and_transfer(text: str, number: str) -> str:
    return f'<Response><Say>{escape(text)}</Say>{_dial(number)}</Response>'


def play_and_hangup(audio_url: str) -> str:
    return f'<Response><Play>{escape(audio_url)}</Play><Hangup/></Response>'


def play_and_transfer(audio_url: str, number: str) -> str:
    return f'<Response><Play>{escape(audio_url)}</Play>{_dial(number)}</Response>'


def stream_and_continue(prompt: str, stream_url: str, token: str, *, timeout=None) -> str:
    # Closing our socket after STT completes releases Connect and runs Redirect.
    # STT adapters enforce their own turn timeout; Stream has no timeout attribute.
    return (
        f'<Response>{prompt}<Connect><Stream url={quoteattr(stream_url)} '
        f'statusCallback={quoteattr(config.public_base_url() + "/twilio/voice/stream_status")} '
        f'statusCallbackMethod="POST"/></Connect>'
        f'<Redirect method="POST">{escape(config.public_base_url() + "/twilio/voice/stream_result/" + token)}</Redirect>'
        '</Response>'
    )
