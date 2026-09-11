"""
Plivo telephony gateway.

chat_manager is text-in / text-out and knows nothing about audio.
This gateway is audio-in / audio-out and knows nothing about cake.
Every decision below follows from that one line.
"""
import hmac
import logging
import secrets
import re
from xml.sax.saxutils import escape
from contextlib import asynccontextmanager

from starlette.concurrency import run_in_threadpool

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, WebSocket

import config
import phrase_cache as pc
import plivo_xml
from audio_cache import purge, purge_expired, read as read_audio, write as write_audio
from brain.client import BrainUnavailable, chat as brain_chat
from calls import state as calls
from orders import emitter as orders
from cost import cost_emitter
import print_client
from security import verify_plivo
from speech.assemblyai_stt import stream_utterance as assemblyai_stream_utterance
from speech.deepgram_stt import stream_utterance
from speech.sarvam_stt import stream_utterance as sarvam_stream_utterance
from speech.elevenlabs_stt import stream_utterance as elevenlabs_stream_utterance
from speech.elevenlabs_tts import TTSUnavailable, synthesize

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("gateway")


def _prewarm_fixed_phrases() -> None:
    """Synthesize fixed phrases once at startup and store in phrase cache.

    Logs the active voice/model so operators know what parameters the cache
    was built against — helpful if ELEVENLABS_VOICE_ID or ELEVENLABS_MODEL_ID
    ever changes (old files become unreachable; new ones are synthesized fresh).
    Pre-warm failures are non-fatal: the system starts normally and synthesizes
    on demand for the first caller.
    """
    logger.info(
        "phrase cache pre-warm starting voice_id=%s model_id=%s dir=%s",
        config.ELEVENLABS_VOICE_ID,
        config.ELEVENLABS_MODEL_ID,
        config.PHRASE_CACHE_DIR,
    )
    for label, text in [
        ("REPROMPT", config.REPROMPT),
        ("BRAIN_DOWN_MSG", config.BRAIN_DOWN_MSG),
    ]:
        try:
            if pc.get(text):
                logger.info("phrase cache already warm: %s", label)
                continue
            audio_bytes = synthesize(text)
            pc.put(text, audio_bytes)
            logger.info("phrase cache pre-warmed: %s", label)
        except Exception:
            logger.exception(
                "phrase cache pre-warm failed for %s — will synthesize on demand", label
            )


@asynccontextmanager
async def lifespan(app: FastAPI):
    if config.STT_PROVIDER == "deepgram" and not config.DEEPGRAM_API_KEY:
        raise RuntimeError("DEEPGRAM_API_KEY is required for Deepgram STT")
    if config.STT_PROVIDER == "sarvam" and not config.SARVAM_API_KEY:
        raise RuntimeError("SARVAM_API_KEY is required for Sarvam STT")
    if config.STT_PROVIDER == "elevenlabs" and not config.ELEVENLABS_API_KEY:
        raise RuntimeError("ELEVENLABS_API_KEY is required for ElevenLabs STT")
    if config.STT_PROVIDER == "assemblyai" and not config.ASSEMBLY_API_KEY:
        raise RuntimeError("ASSEMBLY_API_KEY is required for AssemblyAI STT")
    logger.info("Phone STT provider=%s assembly_model=%s", config.STT_PROVIDER, config.ASSEMBLY_MODEL)
    _prewarm_fixed_phrases()
    yield


app = FastAPI(title="Plivo Telephony Gateway", lifespan=lifespan)


def require_dashboard_api_key(
    x_api_key: str = Header(default="", alias="X-API-Key"),
) -> None:
    """Protect operational feeds while leaving Plivo webhooks unchanged."""
    if not config.DASHBOARD_API_KEY:
        raise HTTPException(503, "dashboard API key is not configured")
    if not hmac.compare_digest(x_api_key, config.DASHBOARD_API_KEY):
        raise HTTPException(401, "invalid or missing API key")


def xml_response(body: str) -> Response:
    return Response(content=body, media_type="application/xml")


def tts_cached(text: str, call_uuid: str, *, use_phrase_cache: bool = False) -> str:
    """Synthesize once, cache to disk, return the URL for <Play>.

    use_phrase_cache=True  → check the persistent phrase cache first.
        HIT:  return the stored URL, ElevenLabs is not called ($0 cost).
        MISS: synthesize via ElevenLabs, store permanently for all future calls.
    use_phrase_cache=False (default) → per-call ephemeral cache only, as before.
        Audio is written to audio_cache and purged on hangup.

    TTSUnavailable is surfaced to callers so they can decide how to degrade
    (see the BrainUnavailable path in /voice/turn for an example).
    """
    if use_phrase_cache:
        url = pc.get(text)
        if url:
            return url  # cache hit — skip ElevenLabs entirely
    audio_bytes = synthesize(text)
    if use_phrase_cache:
        return pc.put(text, audio_bytes)  # store permanently, serve from /phrase/
    return write_audio(audio_bytes, call_uuid)  # ephemeral, purged on hangup


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "brain_url": config.CHAT_MANAGER_URL}


@app.get("/orders/recent", dependencies=[Depends(require_dashboard_api_key)])
async def orders_recent(limit: int = 50) -> dict:
    return {"orders": orders.recent(limit)}


@app.get("/handoffs/recent", dependencies=[Depends(require_dashboard_api_key)])
async def handoffs_recent(limit: int = 50) -> dict:
    return {"handoffs": orders.recent_handoffs(limit)}


@app.get("/cost/calls", dependencies=[Depends(require_dashboard_api_key)])
async def cost_calls(limit: int = 50) -> dict:
    records = cost_emitter.recent(limit)
    return {"calls": records, "total_seconds": cost_emitter.total_seconds(records)}


@app.get("/audio/{audio_id}.mp3")
async def get_audio(audio_id: str) -> Response:
    data = read_audio(audio_id)
    if data is None:
        return Response(status_code=404)
    return Response(content=data, media_type="audio/mpeg")


@app.get("/phrase/{key}.mp3")
async def get_phrase_audio(key: str) -> Response:
    """Serve a permanently cached phrase clip.

    Kept separate from /audio/ so phrase files are never accidentally swept
    by purge() (which only touches per-call clips in AUDIO_DIR).
    """
    data = pc.read(key)
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
    state = calls.start(call_uuid, caller)
    state.stt_provider = config.STT_PROVIDER
    purge_expired()  # sweep clips from calls that never reached /voice/hangup
    logger.info("call answered call_uuid=%s from=%s", call_uuid, caller)

    try:
        reply = await run_in_threadpool(brain_chat, user_id=caller, session_id=None, message=config.GREETING_PROMPT)
    except BrainUnavailable:
        logger.exception("brain unreachable on answer call_uuid=%s", call_uuid)
        return _speak_and_transfer_response(config.BRAIN_DOWN_MSG)

    session_id = reply.get("session_id")
    if session_id:
        calls.bind_session(call_uuid, session_id)

    greeting_text = reply.get("answer", "")
    try:
        audio_url = await run_in_threadpool(tts_cached, greeting_text, call_uuid)
        return _continue_response(audio_url, call_uuid, speak=False)
    except TTSUnavailable:
        # Plivo's own <Speak> as a fallback so a TTS outage doesn't kill
        # the very first turn of every call.
        logger.exception("TTS unavailable on greeting, falling back to <Speak>")
        return _continue_response(greeting_text, call_uuid, speak=True)


@app.post("/voice/turn")
async def turn(params: dict = Depends(verify_plivo)) -> Response:
    """One caller utterance -> one agent reply."""
    call_uuid = params.get("CallUUID", "")
    caller = params.get("From", "")
    speech = (params.get("Speech") or "").strip()

    # Caller said nothing intelligible. Re-prompt rather than dropping the call.
    if not speech:
        try:
            audio_url = await run_in_threadpool(tts_cached, config.REPROMPT, call_uuid, use_phrase_cache=True)
            return _continue_response(audio_url, call_uuid, speak=False)
        except TTSUnavailable:
            return _continue_response(config.REPROMPT, call_uuid, speak=True)

    state = calls.get(call_uuid)
    try:
        reply = await run_in_threadpool(
            brain_chat,
            user_id=caller, session_id=state.session_id, message=speech
        )
    except BrainUnavailable:
        logger.exception("brain unreachable call_uuid=%s", call_uuid)
        try:
            audio_url = await run_in_threadpool(tts_cached, config.BRAIN_DOWN_MSG, call_uuid, use_phrase_cache=True)
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
    await run_in_threadpool(_emit_events, reply, call_uuid=call_uuid, caller=caller, session_id=session_id)

    # One llm_turn cost record per turn -- previously nothing forwarded this
    # data anywhere at all, only Plivo's own call-minute cost was tracked.
    # Wrapped: a cost-logging failure (e.g. disk full) must never crash the
    # turn and strand the caller before they hear a reply.
    try:
        cost_emitter.emit_llm_turn(
            call_uuid=call_uuid,
            turn_seq=calls.next_turn_seq(call_uuid),
            model=reply.get("model_used", ""),
            input_tokens=reply.get("input_tokens", 0),
            output_tokens=reply.get("output_tokens", 0),
            tts_chars=reply.get("tts_chars", 0),
            latency_ms=reply.get("latency_ms"),
        )
    except Exception:
        logger.exception("failed to emit llm_turn cost record call_uuid=%s", call_uuid)

    try:
        audio_url = await run_in_threadpool(tts_cached, reply["answer"], call_uuid)
    except TTSUnavailable:
        logger.exception("TTS unavailable for reply, falling back to <Speak>")
        # Degrade to Plivo's own TTS rather than drop the call.
        return _handle_flags_with_speak(reply, call_uuid)

    # Transfer_to_Manager (live handoff) and To_manager (async cake/catering
    # follow-up) are different things — confusing them either drops a lead
    # or hangs up on a customer.
    if reply.get("Transfer_to_Manager"):
        return xml_response(
            plivo_xml.play_and_transfer(audio_url, config.PLIVO_TRANSFER_NUMBER)
        )

    if reply.get("call_ended"):
        return xml_response(plivo_xml.play_and_hangup(audio_url))

    return _continue_response(audio_url, call_uuid, speak=False)


@app.post("/voice/no_input")
async def no_input(params: dict = Depends(verify_plivo)) -> Response:
    """Reprompt instead of ending the call when GetInput recognizes no speech."""
    call_uuid = params.get("CallUUID", "")
    logger.info("no speech detected; reprompting call_uuid=%s", call_uuid)
    try:
        audio_url = await run_in_threadpool(tts_cached, config.REPROMPT, call_uuid, use_phrase_cache=True)
        return _continue_response(audio_url, call_uuid, speak=False)
    except TTSUnavailable:
        return _continue_response(config.REPROMPT, call_uuid, speak=True)


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


def _handle_flags_with_speak(reply: dict, call_uuid: str) -> Response:
    """Same flag routing as turn(), but via Plivo's <Speak> instead of a
    cached <Play> URL — used only when ElevenLabs TTS itself is down."""
    text = reply.get("answer", "")
    if reply.get("Transfer_to_Manager"):
        return _speak_and_transfer_response(text)
    if reply.get("call_ended"):
        return xml_response(plivo_xml.speak_and_hangup(text))
    return _continue_response(text, call_uuid, speak=True)


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
    bill_duration_raw = params.get("BillDuration")
    cost_emitter.emit_call_duration(
        call_uuid=call_uuid,
        caller=params.get("From", ""),
        duration_seconds=int(duration_raw) if duration_raw and duration_raw.isdigit() else None,
        bill_duration_seconds=(
            int(bill_duration_raw)
            if bill_duration_raw and bill_duration_raw.isdigit()
            else None
        ),
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


def _continue_response(value: str, call_uuid: str, *, speak: bool = False) -> Response:
    state = calls.get(call_uuid)
    if state.stt_provider not in {"deepgram", "sarvam", "elevenlabs", "assemblyai"}:
        builder = plivo_xml.speak_and_continue if speak else plivo_xml.play_and_continue
        return xml_response(builder(value))
    state.stream_token = secrets.token_urlsafe(32)
    state.stream_claimed = False
    state.stream_transcript = ""
    state.stream_failed = False
    base = config.PLIVO_PUBLIC_BASE_URL.rstrip("/")
    base = base.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
    url = f"{base}/voice/stream/{call_uuid}/{state.stream_token}"
    tag = "Speak" if speak else "Play"
    timeout = {
        "assemblyai": config.ASSEMBLY_TURN_TIMEOUT,
        "sarvam": config.SARVAM_TURN_TIMEOUT,
        "elevenlabs": config.ELEVENLABS_STT_TURN_TIMEOUT,
    }.get(state.stt_provider, config.DEEPGRAM_TURN_TIMEOUT)
    return xml_response(plivo_xml.stream_and_continue(f"<{tag}>{escape(value)}</{tag}>", url, state.stream_token,
        timeout=timeout))


@app.websocket("/voice/stream/{call_uuid}/{token}")
async def voice_stream(websocket: WebSocket, call_uuid: str, token: str):
    # A short-lived, single-use capability generated only by signed call webhooks.
    state = calls.get(call_uuid)
    if (state.finalized or state.stt_provider not in {"deepgram", "sarvam", "elevenlabs", "assemblyai"} or state.stream_claimed
            or not state.stream_token or not hmac.compare_digest(token, state.stream_token)):
        await websocket.close(code=1008)
        return
    state.stream_claimed = True
    await websocket.accept()
    try:
        adapter = {
            "assemblyai": assemblyai_stream_utterance,
            "sarvam": sarvam_stream_utterance,
            "elevenlabs": elevenlabs_stream_utterance,
        }.get(state.stt_provider, stream_utterance)
        state.stream_transcript = await adapter(websocket, call_uuid)
    except Exception as exc:
        # Full traceback for debugging — but never log provider payloads or
        # credentials, which is why we don't str(exc) or log exc.args directly.
        response = getattr(exc, "response", None)
        logger.exception("STT stream failed provider=%s error=%s http_status=%s; transferring call to manager",
                          state.stt_provider, type(exc).__name__, getattr(response, "status_code", None))
        state.stream_failed = True
    finally:
        try:
            await websocket.close()
        except RuntimeError:
            pass


@app.post("/voice/stream_result/{token}")
async def stream_result(token: str, params: dict = Depends(verify_plivo)) -> Response:
    state = calls.get(params.get("CallUUID", ""))
    if state.finalized:
        return xml_response("<Response><Hangup/></Response>")
    if not state.stream_token or not hmac.compare_digest(token, state.stream_token):
        raise HTTPException(409, "expired stream result")
    transcript = state.stream_transcript
    failed = state.stream_failed or not state.stream_claimed
    state.stream_transcript = ""
    state.stream_token = ""
    if failed:
        logger.warning("STT turn failed stage=%s",
                       state.stt_provider + "_stream" if state.stream_claimed else "plivo_stream_never_connected")
        return _speak_and_transfer_response(config.STT_DOWN_MSG)
    return await turn({**params, "Speech": transcript})


@app.post("/voice/stream_status")
async def stream_status(params: dict = Depends(verify_plivo)) -> Response:
    """Observe Plivo stream lifecycle without changing turn state on late callbacks."""
    reason = params.get("StatusReason", params.get("Error", ""))
    reason = re.sub(r"(?:https?|wss?)://\S+", "[url]", reason)
    for secret in (config.DEEPGRAM_API_KEY, config.SARVAM_API_KEY, config.ASSEMBLY_API_KEY, config.PLIVO_AUTH_TOKEN):
        if secret:
            reason = reason.replace(secret, "[redacted]")
    logger.info("Plivo stream status event=%r reason=%r",
                params.get("Event", "")[:80], reason[:300])
    return Response(status_code=200)
