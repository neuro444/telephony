"""
Plivo telephony gateway.

chat_manager is text-in / text-out and knows nothing about audio.
This gateway is audio-in / audio-out and knows nothing about cake.
Every decision below follows from that one line.
"""
import logging

from fastapi import Depends, FastAPI, Request, Response

import config
import plivo_xml
from audio_cache import purge, purge_expired, read as read_audio, write as write_audio
from brain.client import BrainUnavailable, chat as brain_chat
from calls import state as calls
from orders import emitter as orders
from cost import cost_emitter
import print_client
from security import verify_plivo
from speech.elevenlabs_tts import TTSUnavailable, synthesize

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("gateway")

app = FastAPI(title="Plivo Telephony Gateway")


def xml_response(body: str) -> Response:
    return Response(content=body, media_type="application/xml")


def tts_cached(text: str, call_uuid: str) -> str:
    """Synthesize once, cache to disk, return the URL for <Play>.

    On TTS failure, falls back to Plivo's own <Speak> is handled by the
    caller (see the BrainUnavailable path in /voice/turn) — this function
    itself just surfaces TTSUnavailable so callers can decide.
    """
    audio_bytes = synthesize(text)
    return write_audio(audio_bytes, call_uuid)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "brain_url": config.CHAT_MANAGER_URL}


@app.get("/orders/recent")
async def orders_recent(limit: int = 50) -> dict:
    return {"orders": orders.recent(limit)}


@app.get("/handoffs/recent")
async def handoffs_recent(limit: int = 50) -> dict:
    return {"handoffs": orders.recent_handoffs(limit)}


@app.get("/cost/calls")
async def cost_calls(limit: int = 50) -> dict:
    records = cost_emitter.recent(limit)
    return {"calls": records, "total_seconds": cost_emitter.total_seconds(records)}


@app.get("/audio/{audio_id}.mp3")
async def get_audio(audio_id: str) -> Response:
    data = read_audio(audio_id)
    if data is None:
        return Response(status_code=404)
    return Response(content=data, media_type="audio/mpeg")


@app.post("/voice/answer")
async def answer(params: dict = Depends(verify_plivo)) -> Response:
    """Plivo hits this when a call connects.

    The greeting is NOT hardcoded here. chat_manager writes it — it greets
    returning callers by name and its prompt already treats a bare "hello"
    as a request for a fresh welcome. A fixed string in the gateway would
    throw that away and give every caller the same line.
    """
    call_uuid = params.get("CallUUID", "")
    caller = params.get("From", "")
    calls.start(call_uuid, caller)  # new call -> no session yet
    purge_expired()  # sweep clips from calls that never reached /voice/hangup
    logger.info("call answered call_uuid=%s from=%s", call_uuid, caller)

    try:
        reply = brain_chat(user_id=caller, session_id=None, message=config.GREETING_PROMPT)
    except BrainUnavailable:
        logger.exception("brain unreachable on answer call_uuid=%s", call_uuid)
        return _speak_and_transfer_response(config.BRAIN_DOWN_MSG)

    session_id = reply.get("session_id")
    if session_id:
        calls.bind_session(call_uuid, session_id)

    greeting_text = reply.get("answer", "")
    try:
        audio_url = tts_cached(greeting_text, call_uuid)
        return xml_response(plivo_xml.play_and_continue(audio_url))
    except TTSUnavailable:
        # Plivo's own <Speak> as a fallback so a TTS outage doesn't kill
        # the very first turn of every call.
        logger.exception("TTS unavailable on greeting, falling back to <Speak>")
        return xml_response(plivo_xml.speak_and_continue(greeting_text))


@app.post("/voice/turn")
async def turn(params: dict = Depends(verify_plivo)) -> Response:
    """One caller utterance -> one agent reply."""
    call_uuid = params.get("CallUUID", "")
    caller = params.get("From", "")
    speech = (params.get("Speech") or "").strip()

    # Caller said nothing intelligible. Re-prompt rather than dropping the call.
    if not speech:
        try:
            audio_url = tts_cached(config.REPROMPT, call_uuid)
            return xml_response(plivo_xml.play_and_continue(audio_url))
        except TTSUnavailable:
            return xml_response(plivo_xml.speak_and_continue(config.REPROMPT))

    state = calls.get(call_uuid)
    try:
        reply = brain_chat(
            user_id=caller, session_id=state.session_id, message=speech
        )
    except BrainUnavailable:
        logger.exception("brain unreachable call_uuid=%s", call_uuid)
        try:
            audio_url = tts_cached(config.BRAIN_DOWN_MSG, call_uuid)
            return xml_response(
                plivo_xml.play_and_transfer(audio_url, config.PLIVO_TRANSFER_NUMBER)
            )
        except TTSUnavailable:
            # Both the brain AND TTS are down — never leave a dead line.
            return xml_response(plivo_xml.speak_and_hangup(config.BRAIN_DOWN_MSG))

    session_id = reply.get("session_id")
    if session_id:
        calls.bind_session(call_uuid, session_id)

    # Emit before synthesizing audio: a completed order must survive a TTS
    # outage. Emitting after the tts_cached() try/except would drop exactly
    # the orders that completed successfully.
    _emit_events(reply, call_uuid=call_uuid, caller=caller, session_id=session_id)

    try:
        audio_url = tts_cached(reply["answer"], call_uuid)
    except TTSUnavailable:
        logger.exception("TTS unavailable for reply, falling back to <Speak>")
        # Degrade to Plivo's own TTS rather than drop the call.
        return _handle_flags_with_speak(reply)

    # Transfer_to_Manager (live handoff) and To_manager (async cake/catering
    # follow-up) are different things — confusing them either drops a lead
    # or hangs up on a customer.
    if reply.get("Transfer_to_Manager"):
        return xml_response(
            plivo_xml.play_and_transfer(audio_url, config.PLIVO_TRANSFER_NUMBER)
        )

    if reply.get("call_ended"):
        return xml_response(plivo_xml.play_and_hangup(audio_url))

    return xml_response(plivo_xml.play_and_continue(audio_url))


def _emit_events(reply: dict, *, call_uuid: str, caller: str, session_id: str | None) -> None:
    """Record final typed call artifacts, each at most once.

    order_ready -> a completed, priced order.
    To_manager  -> an async cake/catering follow-up. NOT a live transfer:
                   confusing it with Transfer_to_Manager either drops a lead
                   or hangs up on a customer.
    delivery    -> a completed website redirect with its summary.
    """
    if not session_id:
        return
    if reply.get("order_ready") and reply.get("order"):
        if calls.mark_order_emitted(call_uuid, session_id):
            orders.emit(reply, call_uuid=call_uuid, user_id=caller)
            print_client.print_order(
                reply["order"],
                call_uuid=call_uuid,
                caller=caller,
                order_type=reply.get("order_type"),
            )
    is_delivery = (
        reply.get("call_ended") and reply.get("order_type") == "delivery"
    )
    if reply.get("To_manager") or is_delivery:
        if calls.mark_handoff_emitted(call_uuid, session_id):
            orders.emit_handoff(reply, call_uuid=call_uuid, user_id=caller)
            if reply.get("To_manager") and reply.get("order_type") in {
                "cake", "catering", "cake/catering"
            }:
                print_client.print_manager_request(
                    reply, call_uuid=call_uuid, caller=caller
                )


def _speak_and_transfer_response(text: str) -> Response:
    """Spoken apology then a live transfer — used when TTS is unavailable."""
    return xml_response(
        plivo_xml.speak_and_transfer(text, config.PLIVO_TRANSFER_NUMBER)
    )


def _handle_flags_with_speak(reply: dict) -> Response:
    """Same flag routing as turn(), but via Plivo's <Speak> instead of a
    cached <Play> URL — used only when ElevenLabs TTS itself is down."""
    text = reply.get("answer", "")
    if reply.get("Transfer_to_Manager"):
        return _speak_and_transfer_response(text)
    if reply.get("call_ended"):
        return xml_response(plivo_xml.speak_and_hangup(text))
    return xml_response(plivo_xml.speak_and_continue(text))


@app.post("/voice/transfer_done")
async def transfer_done(params: dict = Depends(verify_plivo)) -> Response:
    """<Dial> posts here when the manager leg ends. Without this, a manager
    who is busy or away leaves the caller listening to nothing."""
    status = params.get("DialStatus", "")  # completed|no-answer|busy|failed
    if status == "completed":
        return xml_response("<Response><Hangup/></Response>")

    logger.warning(
        "manager transfer failed status=%s cause=%s call_uuid=%s",
        status,
        params.get("DialHangupCause"),
        params.get("CallUUID"),
    )
    return xml_response(plivo_xml.speak_and_hangup(config.TRANSFER_FAILED_MSG))


@app.post("/voice/hangup")
async def hangup(params: dict = Depends(verify_plivo)) -> Response:
    """Configured as the app's Hangup URL. Fires whenever the call ends,
    however it ends. Idempotency key is CallUUID (Plivo's own guidance) —
    callbacks can be delivered more than once."""
    call_uuid = params.get("CallUUID", "")
    if calls.already_finalized(call_uuid):
        return Response(status_code=200)
    calls.finalize(
        call_uuid,
        duration=params.get("Duration"),
        cause=params.get("HangupCause"),
    )
    duration_raw = params.get("Duration")
    cost_emitter.emit_call_duration(
        call_uuid=call_uuid,
        caller=params.get("From", ""),
        duration_seconds=int(duration_raw) if duration_raw and duration_raw.isdigit() else None,
        hangup_cause=params.get("HangupCause"),
    )
    purge(call_uuid)  # delete cached TTS mp3s for this call
    return Response(status_code=200)


@app.post("/voice/fallback")
async def fallback(params: dict = Depends(verify_plivo)) -> Response:
    """Configured as the app's Fallback Answer URL — reached only if
    /voice/answer itself is unreachable (gateway down, DNS issue, etc).
    Never a dead line: apologize and dial the restaurant directly."""
    del params
    return xml_response(
        f"<Response><Speak>We're having trouble taking your call online. "
        f"Connecting you now.</Speak>"
        f'<Dial callerId="{config.PLIVO_PHONE_NUMBER}">'
        f"<Number>{config.PLIVO_TRANSFER_NUMBER}</Number></Dial></Response>"
    )
