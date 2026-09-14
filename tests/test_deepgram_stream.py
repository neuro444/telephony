import asyncio
import base64
import json
from urllib.parse import urlsplit
from xml.dom.minidom import parseString

import pytest
from starlette.websockets import WebSocketDisconnect

import app as gateway
import config
from calls import state as calls
from speech import deepgram_stt
from test_turn_flow import client, stub_brain, BASE, ANSWER_FORM, tags


def test_stream_turn_preserves_session_and_returns_to_listening(client, monkeypatch):
    monkeypatch.setattr(config, "STT_PROVIDER", "deepgram")
    seen = []
    stub_brain(monkeypatch, BASE, seen)
    response = client.post('/voice/answer', data=ANSWER_FORM)
    assert tags(response.text) == ['Play', 'Stream', 'Redirect']
    stream = parseString(response.text).getElementsByTagName('Stream')[0]
    assert stream.getAttribute('contentType') == 'audio/x-mulaw;rate=8000'
    assert stream.getAttribute('bidirectional') == 'true'
    assert stream.getAttribute('keepCallAlive') == 'true'
    assert stream.getAttribute('statusCallbackUrl').endswith('/voice/stream_status')
    path = urlsplit(stream.firstChild.data).path

    async def transcribe(ws, call_uuid):
        assert call_uuid == 'cu1'
        return 'two samosas'

    monkeypatch.setattr(gateway, 'stream_utterance', transcribe)
    with client.websocket_connect(path) as ws:
        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()
    # The token cannot be reused to open another billable stream.
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(path):
            pass
    response = client.post('/voice/stream_result/' + calls.get('cu1').stream_token, data=ANSWER_FORM)
    assert seen[-1]['message'] == 'two samosas'
    assert seen[-1]['session_id'] == 's1'
    assert tags(response.text) == ['Play', 'Stream', 'Redirect']


def test_stream_failure_transfers_without_plivo_stt(client, monkeypatch):
    monkeypatch.setattr(config, 'STT_PROVIDER', 'deepgram')
    stub_brain(monkeypatch, BASE)
    response = client.post('/voice/answer', data=ANSWER_FORM)
    stream = parseString(response.text).getElementsByTagName('Stream')[0]

    async def broken(*args):
        raise RuntimeError('provider unavailable')

    monkeypatch.setattr(gateway, 'stream_utterance', broken)
    with client.websocket_connect(urlsplit(stream.firstChild.data).path) as ws:
        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()
    response = client.post('/voice/stream_result/' + calls.get('cu1').stream_token, data=ANSWER_FORM)
    assert tags(response.text) == ['Speak', 'Dial']
    assert 'GetInput' not in response.text
    assert calls.get('cu1').stt_provider == 'deepgram'


def test_stream_rejects_unknown_token(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect('/voice/stream/unknown/invalid'):
            pass


def test_stream_connection_failure_before_start_transfers(client, monkeypatch):
    monkeypatch.setattr(config, 'STT_PROVIDER', 'deepgram')
    stub_brain(monkeypatch, BASE)
    client.post('/voice/answer', data=ANSWER_FORM)
    response = client.post('/voice/stream_result/' + calls.get('cu1').stream_token, data=ANSWER_FORM)
    assert tags(response.text) == ['Speak', 'Dial']
    assert 'GetInput' not in response.text


def test_live_audio_forwarding_and_final_segments(monkeypatch):
    monkeypatch.setattr(config, 'DEEPGRAM_API_KEY', 'test-key')
    monkeypatch.setattr(config, 'DEEPGRAM_LANGUAGE', 'multi')
    monkeypatch.setattr(config, 'SPEECH_LANGUAGE', 'en-US')
    sent = []
    options = {}

    class Deepgram:
        async def send(self, data):
            sent.append(data)
        def __aiter__(self):
            return self.messages()
        async def messages(self):
            # Wait until the Plivo sender actually forwards media.
            while not sent:
                await asyncio.sleep(0)
            for text, final, endpoint in [('ignored interim', False, False),
                                           ('two', True, False),
                                           ('samosas', True, True)]:
                yield json.dumps({'type': 'Results', 'is_final': final,
                                  'speech_final': endpoint,
                                  'channel': {'alternatives': [{'transcript': text}]}})
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass

    def connect(url, **kwargs):
        options.update(url=url, **kwargs)
        return Deepgram()

    class Plivo:
        def __init__(self):
            self.events = iter([
                {'event': 'start', 'start': {'callId': 'cu1', 'mediaFormat': {
                    'encoding': 'audio/x-mulaw', 'sampleRate': 8000}}},
                {'event': 'media', 'media': {'payload': base64.b64encode(b'audio').decode()}},
            ])
        async def receive_json(self):
            event = next(self.events, None)
            if event:
                return event
            await asyncio.Future()

    monkeypatch.setattr(deepgram_stt, 'connect', connect)
    assert asyncio.run(deepgram_stt.stream_utterance(Plivo(), 'cu1')) == 'two samosas'
    from urllib.parse import parse_qs, urlsplit
    assert parse_qs(urlsplit(options['url']).query)['language'] == ['multi']
    assert sent == [b'audio']
    assert 'model=nova-3' in options['url']
    assert 'encoding=mulaw' in options['url']
    assert options['additional_headers']['Authorization'] == 'Token test-key'


@pytest.mark.parametrize("flag,expected", [("call_ended", ["Play", "Hangup"]),
                                          ("Transfer_to_Manager", ["Play", "Dial"])])
def test_deepgram_preserves_terminal_actions(client, monkeypatch, flag, expected):
    state = calls.start("cu1", ANSWER_FORM["From"])
    state.stt_provider = "deepgram"
    state.stream_token = "test-result-token"
    state.stream_claimed = True
    state.stream_transcript = "yes please"
    stub_brain(monkeypatch, {**BASE, flag: True})
    response = client.post('/voice/stream_result/test-result-token', data=ANSWER_FORM)
    assert tags(response.text) == expected
    assert client.post('/voice/stream_result/test-result-token', data=ANSWER_FORM).status_code == 409


def test_stream_result_requires_plivo_signature():
    from fastapi.testclient import TestClient
    client = TestClient(gateway.app, raise_server_exceptions=False)
    assert client.post('/voice/stream_result/fake', data=ANSWER_FORM).status_code == 403


def test_stream_status_logs_failure_without_mutating_turn(client, caplog):
    import logging
    state = calls.start("cu1", ANSWER_FORM["From"])
    state.stream_token = "current-turn"
    with caplog.at_level(logging.INFO):
        response = client.post('/voice/stream_status', data={**ANSWER_FORM,
            "Event": "failed", "StatusReason": "Connection failed wss://example.test/private-token"})
    assert response.status_code == 200
    assert "Connection failed" in caplog.text
    assert "private-token" not in caplog.text
    assert state.stream_token == "current-turn"
    assert not state.stream_failed


def test_stream_status_requires_signature():
    from fastapi.testclient import TestClient
    assert TestClient(gateway.app).post('/voice/stream_status', data={}).status_code == 403


def test_keyterms_capped_at_verified_safe_count(monkeypatch):
    # Deepgram's stated "500 token" keyterm limit does not match its actual
    # enforcement (verified against the live API: 72 of our real menu
    # keyterms succeeds, 73 fails, despite both being far under 500 words).
    # Cap at a fixed, empirically-safe entry count instead of trying to
    # reproduce Deepgram's undocumented tokenizer.
    hints = ",".join(f"Term {i}" for i in range(100))
    monkeypatch.setattr(config, "SPEECH_HINTS", hints)
    keyterms = [h.strip() for h in config.SPEECH_HINTS.split(",") if h.strip()]
    capped = deepgram_stt._capped_keyterms(keyterms)
    assert capped == keyterms[:deepgram_stt.DEEPGRAM_MAX_KEYTERMS]
    assert len(capped) == 60
