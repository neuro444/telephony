"""Signed carrier callbacks, media normalization, and unchanged shared flow."""
import asyncio
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from twilio.request_validator import RequestValidator
from plivo.utils.signature_v3 import construct_post_url, get_signature_v3

import app as gateway
import config
from calls import state as calls
from telephony_stream import TwilioStream
from test_turn_flow import BASE, stub_brain

FORM = {"AccountSid": "ACtest", "CallSid": "CAtest", "From": "+15550001111"}
BASE_URL = "https://twilio.test"


def signed(path, params):
    return {"X-Twilio-Signature": RequestValidator("twilio-test-token").compute_signature(BASE_URL + path, params)}


@pytest.fixture
def twilio_client(monkeypatch, tmp_path, orders_log):
    monkeypatch.setattr(config, "TELEPHONY_PROVIDER", "twilio")
    monkeypatch.setattr(config, "TWILIO_ACCOUNT_SID", "ACtest")
    monkeypatch.setattr(config, "TWILIO_AUTH_TOKEN", "twilio-test-token")
    monkeypatch.setattr(config, "TWILIO_PUBLIC_BASE_URL", BASE_URL)
    monkeypatch.setattr(config, "TWILIO_PHONE_NUMBER", "+15717162921")
    monkeypatch.setattr(config, "STT_PROVIDER", "assemblyai")
    monkeypatch.setattr(config, "AUDIO_DIR", str(tmp_path / "audio"))
    monkeypatch.setattr(config, "PHRASE_CACHE_DIR", str(tmp_path / "phrases"))
    monkeypatch.setattr(gateway, "synthesize", lambda text: b"FAKE-MP3")
    stub_brain(monkeypatch, BASE)
    return TestClient(gateway.app)


def post(client, path, params=None):
    params = FORM if params is None else params
    return client.post(path, data=params, headers=signed(path, params))


def test_signature_rejects_tampering_and_wrong_account(twilio_client):
    path = "/twilio/voice/answer"
    assert twilio_client.post(path, data=FORM).status_code == 403
    assert twilio_client.post(path, data={**FORM, "From": "altered"}, headers=signed(path, FORM)).status_code == 403
    assert post(twilio_client, path, {**FORM, "AccountSid": "ACother"}).status_code == 403
    assert not calls.registry._calls


@pytest.mark.parametrize("failed", [False, True])
def test_twilio_prompt_stream_next_turn(twilio_client, monkeypatch, failed):
    seen = []
    stub_brain(monkeypatch, BASE, seen)
    response = post(twilio_client, "/twilio/voice/answer")
    assert response.status_code == 200
    xml = ET.fromstring(response.text)
    assert [child.tag for child in xml] == ["Play", "Connect", "Redirect"]
    assert xml.find("Play").text.startswith(BASE_URL + "/audio/")
    stream = xml.find("Connect/Stream")
    assert stream.get("url").startswith("wss://twilio.test/twilio/voice/stream/")
    path = urlsplit(stream.get("url")).path
    async def transcribe(ws, call_id):
        metadata = await ws.receive_json()
        assert metadata["start"]["callId"] == call_id == "CAtest"
        if failed:
            raise RuntimeError("synthetic STT outage")
        return "two samosas"
    monkeypatch.setattr(gateway, "assemblyai_stream_utterance", transcribe)
    with pytest.raises(WebSocketDisconnect):
        with twilio_client.websocket_connect(path):
            pass
    assert not calls.get("CAtest").stream_claimed
    with twilio_client.websocket_connect(path, headers=signed(path, {})) as ws:
        ws.send_json({"event": "start", "start": {"callSid": "CAtest", "mediaFormat": {
            "encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1}}})
        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()
    result_path = urlsplit(xml.find("Redirect").text).path
    response = post(twilio_client, result_path)
    assert "GetInput" not in response.text and "<Speak>" not in response.text
    if failed:
        dial = ET.fromstring(response.text).find("Dial")
        assert dial.get("callerId") == "+15717162921"
        assert dial.find("Number").text == config.PLIVO_TRANSFER_NUMBER
    else:
        assert seen[-1]["message"] == "two samosas"
        assert seen[-1]["session_id"] == "s1"
    assert calls.get("CAtest").stt_provider == "assemblyai"
    assert post(twilio_client, result_path).status_code == 409


def test_plivo_still_works_with_twilio_default(twilio_client):
    params = {"CallUUID": "plivo-call", "From": "+15550002222"}
    path = "/voice/answer"
    nonce = "test-nonce"
    sig = get_signature_v3(config.PLIVO_AUTH_TOKEN.encode(),
                           construct_post_url(config.PLIVO_PUBLIC_BASE_URL + path, params), nonce.encode()).decode()
    response = twilio_client.post(path, data=params, headers={
        "X-Plivo-Signature-V3": sig, "X-Plivo-Signature-V3-Nonce": nonce})
    stream = ET.fromstring(response.text).find("Stream")
    assert stream.get("bidirectional") == "true"
    assert stream.get("keepCallAlive") == "true"
    assert calls.get("plivo-call").telephony_provider == "plivo"
    # A subsequent Twilio call must use its own carrier despite the Plivo request.
    assert ET.fromstring(post(twilio_client, "/twilio/voice/answer").text).find("Connect/Stream") is not None


def test_transfer_and_hangup(twilio_client, monkeypatch, cost_log):
    stub_brain(monkeypatch, {**BASE, "Transfer_to_Manager": True})
    post(twilio_client, "/twilio/voice/answer")
    # Exercise the shared turn path without using Twilio's built-in recognition.
    async def reply():
        config.set_carrier("twilio")
        return await gateway.turn({"CallUUID": "CAtest", "From": FORM["From"], "Speech": "manager"})
    xml = ET.fromstring(asyncio.run(reply()).body)
    assert xml.find("Dial").get("callerId") == "+15717162921"
    assert "dialMusic" not in xml.find("Dial").attrib
    response = post(twilio_client, "/twilio/voice/transfer_done", {**FORM, "DialCallStatus": "busy"})
    assert ET.fromstring(response.text).find("Say") is not None
    post(twilio_client, "/twilio/voice/hangup", {**FORM, "CallStatus": "in-progress"})
    assert not calls.get("CAtest").finalized
    ended = {**FORM, "CallStatus": "completed", "CallDuration": "42"}
    post(twilio_client, "/twilio/voice/hangup", ended)
    before = cost_log.read_text()
    post(twilio_client, "/twilio/voice/hangup", ended)
    assert cost_log.read_text() == before
    assert calls.get("CAtest").finalized
    assert calls.get("CAtest").duration == "42"


def test_twilio_media_bytes_are_not_changed():
    event = {"event": "media", "media": {"payload": "////"}}
    class Socket:
        async def receive_json(self):
            return event
    assert asyncio.run(TwilioStream(Socket()).receive_json()) == event
