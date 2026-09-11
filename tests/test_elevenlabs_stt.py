import asyncio
import base64
import json
import subprocess
import sys
from pathlib import Path
from xml.dom.minidom import parseString
from urllib.parse import urlsplit

import pytest
from starlette.websockets import WebSocketDisconnect
import app as gateway
import config
from calls import state as calls
from speech import elevenlabs_stt
from test_turn_flow import client, stub_brain, BASE, ANSWER_FORM, tags


@pytest.mark.parametrize('failed', [False, True])
def test_elevenlabs_call_dispatch_and_no_plivo_fallback(client, monkeypatch, failed):
    monkeypatch.setattr(config, 'STT_PROVIDER', 'elevenlabs')
    seen = []
    stub_brain(monkeypatch, BASE, seen)
    result = client.post('/voice/answer', data=ANSWER_FORM)
    stream = parseString(result.text).getElementsByTagName('Stream')[0]
    assert stream.getAttribute('bidirectional') == 'true'

    async def adapter(ws, call_id):
        if failed:
            raise RuntimeError('provider down')
        return 'two samosas'
    monkeypatch.setattr(gateway, 'elevenlabs_stream_utterance', adapter)
    with client.websocket_connect(urlsplit(stream.firstChild.data).path) as ws:
        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()
    token = calls.get('cu1').stream_token
    result = client.post('/voice/stream_result/' + token, data=ANSWER_FORM)
    assert calls.get('cu1').stt_provider == 'elevenlabs'
    assert 'GetInput' not in result.text
    if failed:
        assert tags(result.text) == ['Speak', 'Dial']
    else:
        assert seen[-1]['message'] == 'two samosas'
        assert tags(result.text) == ['Play', 'Stream', 'Redirect']


@pytest.mark.parametrize('closes_without_transcript', [False, True])
def test_elevenlabs_waits_for_committed_transcript(monkeypatch, closes_without_transcript):
    monkeypatch.setattr(config, 'ELEVENLABS_API_KEY', 'test')
    sent = []

    class Socket:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def send(self, message): sent.append(json.loads(message))
        def __aiter__(self): return self.messages()
        async def messages(self):
            while len(sent) < 2:
                await asyncio.sleep(0)
            if not closes_without_transcript:
                yield json.dumps({'message_type': 'committed_transcript', 'text': 'two samosas'})

    class Plivo:
        def __init__(self): self.count = 0
        async def receive_json(self):
            self.count += 1
            if self.count == 1:
                return {'event': 'start', 'start': {'callId': 'test',
                    'mediaFormat': {'encoding': 'audio/x-mulaw', 'sampleRate': 8000}}}
            if self.count == 2:
                return {'event': 'media', 'media': {'payload': base64.b64encode(bytes([0, 255])).decode()}}
            if self.count == 3:
                return {'event': 'stop'}
            await asyncio.Future()

    monkeypatch.setattr(elevenlabs_stt, 'connect', lambda *a, **kw: Socket())
    if closes_without_transcript:
        with pytest.raises(RuntimeError):
            asyncio.run(elevenlabs_stt.stream_utterance(Plivo(), 'test'))
    else:
        assert asyncio.run(elevenlabs_stt.stream_utterance(Plivo(), 'test')) == 'two samosas'
    assert sent[0]['audio_base_64'] == base64.b64encode(bytes([0, 255])).decode()
    assert sent[0]['commit'] is False
    assert sent[-1]['commit'] is True


def test_toggle_preserves_secrets_and_requires_key(tmp_path):
    script = tmp_path / 'stt'
    script.write_text((Path(__file__).resolve().parents[1] / 'stt').read_text())
    env = tmp_path / '.env'
    env.write_text('STT_PROVIDER=deepgram\nDEEPGRAM_API_KEY=secret-one\n')
    before = env.read_text()
    result = subprocess.run([sys.executable, str(script), 'elevenlabs'], capture_output=True, text=True)
    assert result.returncode != 0 and env.read_text() == before
    env.write_text(before + 'ELEVENLABS_API_KEY=secret-two\n')
    result = subprocess.run([sys.executable, str(script), 'elevenlabs'], capture_output=True, text=True)
    assert result.returncode == 0
    assert 'STT_PROVIDER=elevenlabs' in env.read_text()
    assert 'ELEVENLABS_STT_MODEL=scribe_v2_realtime' in env.read_text()
    assert 'DEEPGRAM_API_KEY=secret-one' in env.read_text()
    assert 'ELEVENLABS_API_KEY=secret-two' in env.read_text()
    assert 'secret-' not in result.stdout + result.stderr
