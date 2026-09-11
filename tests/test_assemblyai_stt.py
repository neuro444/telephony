import asyncio
import base64
import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from xml.dom.minidom import parseString
import pytest
from starlette.websockets import WebSocketDisconnect
import app as gateway
import config
from calls import state as calls
from speech import assemblyai_stt as adapter
from test_turn_flow import client, stub_brain, BASE, ANSWER_FORM, tags


@pytest.mark.parametrize('model', adapter.MODELS)
def test_model_parameters(monkeypatch, model):
    monkeypatch.setattr(config, 'ASSEMBLY_MODEL', model)
    params = parse_qs(urlsplit(adapter.connection_url()).query)
    assert params == {'speech_model': [model], 'sample_rate': ['8000'], 'encoding': ['pcm_mulaw']}


@pytest.mark.parametrize('outcome', ['success', 'error', 'timeout', 'disconnect'])
def test_chunking_and_termination(monkeypatch, outcome):
    monkeypatch.setattr(config, 'ASSEMBLY_API_KEY', 'test-key')
    monkeypatch.setattr(config, 'ASSEMBLY_TURN_TIMEOUT', .02 if outcome == 'timeout' else 1)
    sent = []
    class Socket:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def recv(self): return json.dumps({'type': 'Begin'})
        async def send(self, data): sent.append(data)
        def __aiter__(self): return self.events()
        async def events(self):
            if any(isinstance(x, str) and json.loads(x).get('type') == 'Terminate' for x in sent):
                yield json.dumps({'type': 'Termination'})
                return
            while not sent: await asyncio.sleep(0)
            if outcome == 'error':
                yield json.dumps({'type': 'Error', 'error': 'test rejection'})
                return
            if outcome in {'timeout', 'disconnect'}: await asyncio.Future()
            yield json.dumps({'type': 'Turn', 'end_of_turn': False, 'transcript': 'partial'})
            yield json.dumps({'type': 'Turn', 'end_of_turn': True, 'transcript': 'two samosas'})
    class Plivo:
        count = 0
        async def receive_json(self):
            self.count += 1
            if self.count == 1:
                return {'event': 'start', 'start': {'callId': 'test', 'mediaFormat': {'encoding': 'audio/x-mulaw', 'sampleRate': 8000}}}
            if self.count <= 6:
                return {'event': 'media', 'media': {'payload': base64.b64encode(b'\xff'*160).decode()}}
            if outcome == 'disconnect': raise WebSocketDisconnect()
            await asyncio.Future()
    monkeypatch.setattr(adapter, 'connect', lambda *args, **kwargs: Socket())
    if outcome in {'error', 'disconnect'}:
        with pytest.raises((RuntimeError, WebSocketDisconnect)):
            asyncio.run(adapter.stream_utterance(Plivo(), 'test'))
    else:
        assert asyncio.run(adapter.stream_utterance(Plivo(), 'test')) == ('two samosas' if outcome == 'success' else '')
    assert sent[0] == b'\xff'*800
    assert json.loads(sent[-1]) == {'type': 'Terminate'}


@pytest.mark.parametrize('failed', [False, True])
def test_call_dispatch_without_plivo_fallback(client, monkeypatch, failed):
    monkeypatch.setattr(config, 'STT_PROVIDER', 'assemblyai')
    seen = []
    stub_brain(monkeypatch, BASE, seen)
    response = client.post('/voice/answer', data=ANSWER_FORM)
    stream = parseString(response.text).getElementsByTagName('Stream')[0]
    assert stream.getAttribute('bidirectional') == 'true'
    async def transcribe(ws, call_id):
        if failed: raise RuntimeError('provider unavailable')
        return 'two samosas'
    monkeypatch.setattr(gateway, 'assemblyai_stream_utterance', transcribe)
    with client.websocket_connect(urlsplit(stream.firstChild.data).path) as ws:
        with pytest.raises(WebSocketDisconnect): ws.receive_text()
    response = client.post('/voice/stream_result/'+calls.get('cu1').stream_token, data=ANSWER_FORM)
    assert calls.get('cu1').stt_provider == 'assemblyai'
    assert 'GetInput' not in response.text
    if failed: assert tags(response.text) == ['Speak', 'Dial']
    else:
        assert seen[-1]['message'] == 'two samosas'
        assert tags(response.text) == ['Play', 'Stream', 'Redirect']


@pytest.mark.parametrize('model', adapter.MODELS)
def test_toggle_selects_model_and_hides_key(tmp_path, model):
    script = tmp_path/'stt'
    script.write_text((Path(__file__).resolve().parents[1]/'stt').read_text())
    env = tmp_path/'.env'
    env.write_text('ASSEMBLY_API_KEY=secret-test\nSTT_PROVIDER=deepgram\n')
    result = subprocess.run([sys.executable, str(script), 'assemblyai', '--model', model], capture_output=True, text=True)
    assert result.returncode == 0
    assert 'STT_PROVIDER=assemblyai' in env.read_text()
    assert 'ASSEMBLY_MODEL='+model in env.read_text()
    assert 'secret-test' not in result.stdout+result.stderr
    assert model in result.stdout
