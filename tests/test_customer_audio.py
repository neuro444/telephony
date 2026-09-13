import base64
import asyncio
import pytest
import config
from calls.state import CallState
from customer_audio import CaptureStream


class Socket:
    def __init__(self, events):
        self.events = iter(events)
    async def receive_json(self):
        return next(self.events)


def test_exact_bytes_single_reader_and_bounded_capture(monkeypatch):
    monkeypatch.setattr(config, 'DEBUG_CUSTOMER_AUDIO', True)
    monkeypatch.setattr(config, 'DEBUG_AUDIO_CALLERS', {'+15550001111'})
    raw = bytes(range(256))
    event = {'event': 'media', 'media': {'payload': base64.b64encode(raw).decode()}}
    stream = CaptureStream(Socket([event] * 2000), CallState('call', '+15550001111'))
    for _ in range(2000):
        assert asyncio.run(stream.receive_json()) is event
    clip = stream.result()
    assert len(base64.b64decode(clip['mulaw_base64'])) <= 480000
    assert base64.b64decode(clip['mulaw_base64'])[:256] == raw
    assert clip['complete'] is False
    assert clip['call_id'] == 'call'


def test_disabled_and_test_caller_filter(monkeypatch):
    monkeypatch.setattr(config, 'DEBUG_CUSTOMER_AUDIO', True)
    monkeypatch.setattr(config, 'DEBUG_AUDIO_CALLERS', {'allowed'})
    event = {'event': 'media', 'media': {'payload': '//8='}}
    stream = CaptureStream(Socket([event]), CallState('call', 'other'))
    assert asyncio.run(stream.receive_json()) is event
    assert stream.result() is None


def test_bad_capture_does_not_change_event(monkeypatch):
    monkeypatch.setattr(config, 'DEBUG_CUSTOMER_AUDIO', True)
    monkeypatch.setattr(config, 'DEBUG_AUDIO_CALLERS', set())
    bad = {'event': 'media', 'media': {'payload': '*'}}
    good = {'event': 'media', 'media': {'payload': '//8='}}
    stream = CaptureStream(Socket([bad, good]), CallState('call', 'tester'))
    assert asyncio.run(stream.receive_json()) is bad
    assert asyncio.run(stream.receive_json()) is good
    assert stream.result()['complete'] is False


def test_stream_clip_follows_exact_turn(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect
    from calls import state as calls
    from xml.etree import ElementTree as ET
    from urllib.parse import urlsplit
    from fastapi import Request
    import app as gateway
    import security
    from test_turn_flow import BASE, ANSWER_FORM

    monkeypatch.setattr(config, 'STT_PROVIDER', 'deepgram')
    monkeypatch.setattr(config, 'DEEPGRAM_API_KEY', 'test-key')
    monkeypatch.setattr(config, 'DEBUG_CUSTOMER_AUDIO', True)
    monkeypatch.setattr(config, 'DEBUG_AUDIO_CALLERS', set())
    monkeypatch.setattr(config, 'AUDIO_DIR', str(tmp_path / 'tts'))
    monkeypatch.setattr(config, 'PHRASE_CACHE_DIR', str(tmp_path / 'phrases'))
    monkeypatch.setattr(gateway, 'synthesize', lambda text: b'FAKE-MP3')
    seen = []
    def brain(**kwargs):
        seen.append(kwargs)
        return BASE
    monkeypatch.setattr(gateway, 'brain_chat', brain)
    async def transcribe(socket, call_id):
        await socket.receive_json()
        await socket.receive_json()
        return 'fifteen porotta'
    monkeypatch.setattr(gateway, 'stream_utterance', transcribe)
    async def verify(request: Request):
        return dict(await request.form())
    gateway.app.dependency_overrides[security.verify_voice] = verify
    try:
        with TestClient(gateway.app) as client:
            xml = ET.fromstring(client.post('/voice/answer', data=ANSWER_FORM).text)
            token = calls.get('cu1').stream_token
            path = urlsplit(xml.find('Stream').text).path
            with client.websocket_connect(path) as ws:
                ws.send_json({'event':'start','start':{'callId':'cu1','mediaFormat':{'encoding':'audio/x-mulaw','sampleRate':8000}}})
                ws.send_json({'event':'media','media':{'payload':'//8='}})
                with pytest.raises(WebSocketDisconnect):
                    ws.receive_text()
            assert client.post('/voice/stream_result/'+token, data=ANSWER_FORM).status_code == 200
            assert 'customer_audio' not in seen[0]  # synthetic greeting
            assert seen[1]['message'] == 'fifteen porotta'
            assert seen[1]['session_id'] == 's1'
            assert base64.b64decode(seen[1]['customer_audio']['mulaw_base64']) == b'\xff\xff'
            assert calls.get('cu1').customer_audio is None  # next turn has fresh capture
    finally:
        gateway.app.dependency_overrides.clear()
