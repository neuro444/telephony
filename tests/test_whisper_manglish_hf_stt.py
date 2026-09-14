import asyncio
import base64
import struct
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
from speech import whisper_manglish_hf_stt
from test_turn_flow import client, stub_brain, BASE, ANSWER_FORM, tags


def test_mulaw_known_samples():
    assert whisper_manglish_hf_stt.mulaw_to_pcm(bytes([0, 128, 127, 255])) == struct.pack('<hhhh', -32124, 32124, 0, 0)


def test_wav_bytes_header_is_well_formed():
    pcm = struct.pack('<hh', -32124, 0)
    wav = whisper_manglish_hf_stt._wav_bytes(pcm, sample_rate=8000)
    assert wav[:4] == b'RIFF'
    assert wav[8:12] == b'WAVE'
    assert struct.unpack('<I', wav[4:8])[0] == 36 + len(pcm)
    assert wav[-len(pcm):] == pcm


def test_menu_prompt_reads_csv_and_is_capped():
    prompt = whisper_manglish_hf_stt._menu_prompt()
    assert 'Kizhi Porotta' in prompt
    assert prompt.count(',') < whisper_manglish_hf_stt._MAX_HINT_PHRASES


@pytest.mark.parametrize('failed', [False, True])
def test_whisper_manglish_hf_call_dispatch_and_no_plivo_fallback(client, monkeypatch, failed):
    monkeypatch.setattr(config, 'STT_PROVIDER', 'whisper_manglish_hf')
    seen = []
    stub_brain(monkeypatch, BASE, seen)
    result = client.post('/voice/answer', data=ANSWER_FORM)
    stream = parseString(result.text).getElementsByTagName('Stream')[0]
    assert stream.getAttribute('bidirectional') == 'true'

    async def adapter(ws, call_id):
        if failed:
            raise RuntimeError('endpoint down')
        return 'two samosas'
    monkeypatch.setattr(gateway, 'whisper_manglish_hf_stream_utterance', adapter)
    with client.websocket_connect(urlsplit(stream.firstChild.data).path) as ws:
        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()
    token = calls.get('cu1').stream_token
    result = client.post('/voice/stream_result/' + token, data=ANSWER_FORM)
    assert calls.get('cu1').stt_provider == 'whisper_manglish_hf'
    assert 'GetInput' not in result.text
    if failed:
        assert tags(result.text) == ['Speak', 'Dial']
    else:
        assert seen[-1]['message'] == 'two samosas'
        assert tags(result.text) == ['Play', 'Stream', 'Redirect']


def test_stream_utterance_posts_wav_and_menu_prompt(monkeypatch):
    monkeypatch.setattr(config, 'HF_WHISPER_ENDPOINT_URL', 'https://hf.test/endpoint')
    monkeypatch.setattr(config, 'HF_WHISPER_API_TOKEN', 'secret-token')
    posted = {}

    class Response:
        status_code = 200
        text = ''
        def json(self):
            return {'text': 'two samosas'}

    class FakeAsyncClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def post(self, url, headers=None, json=None):
            posted.update(url=url, headers=headers, json=json)
            return Response()

    monkeypatch.setattr(whisper_manglish_hf_stt.httpx, 'AsyncClient', FakeAsyncClient)

    class Plivo:
        def __init__(self):
            self.count = 0

        async def receive_json(self):
            self.count += 1
            if self.count == 1:
                return {'event': 'start', 'start': {'callId': 'test',
                         'mediaFormat': {'encoding': 'audio/x-mulaw', 'sampleRate': 8000}}}
            if self.count == 2:
                return {'event': 'media', 'media': {'payload': base64.b64encode(bytes([0, 255])).decode()}}
            return {'event': 'stop'}

    result = asyncio.run(whisper_manglish_hf_stt.stream_utterance(Plivo(), 'test'))
    assert result == 'two samosas'
    assert posted['url'] == 'https://hf.test/endpoint'
    assert posted['headers']['Authorization'] == 'Bearer secret-token'
    wav = base64.b64decode(posted['json']['inputs'])
    assert wav[:4] == b'RIFF'
    assert 'Kizhi Porotta' in posted['json']['parameters']['prompt']


def test_stream_utterance_raises_on_http_error(monkeypatch):
    monkeypatch.setattr(config, 'HF_WHISPER_ENDPOINT_URL', 'https://hf.test/endpoint')
    monkeypatch.setattr(config, 'HF_WHISPER_API_TOKEN', 'secret-token')

    class Response:
        status_code = 400
        text = 'bad prompt'

    class FakeAsyncClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def post(self, *a, **kw):
            return Response()

    monkeypatch.setattr(whisper_manglish_hf_stt.httpx, 'AsyncClient', FakeAsyncClient)

    class Plivo:
        def __init__(self):
            self.count = 0

        async def receive_json(self):
            self.count += 1
            if self.count == 1:
                return {'event': 'start', 'start': {'callId': 'test',
                         'mediaFormat': {'encoding': 'audio/x-mulaw', 'sampleRate': 8000}}}
            if self.count == 2:
                return {'event': 'media', 'media': {'payload': base64.b64encode(bytes([0, 255])).decode()}}
            return {'event': 'stop'}

    with pytest.raises(RuntimeError):
        asyncio.run(whisper_manglish_hf_stt.stream_utterance(Plivo(), 'test'))


def test_stream_utterance_requires_config(monkeypatch):
    monkeypatch.setattr(config, 'HF_WHISPER_ENDPOINT_URL', '')
    monkeypatch.setattr(config, 'HF_WHISPER_API_TOKEN', '')

    class Plivo:
        async def receive_json(self):
            await asyncio.Future()

    with pytest.raises(RuntimeError):
        asyncio.run(whisper_manglish_hf_stt.stream_utterance(Plivo(), 'test'))


def test_toggle_preserves_secrets_and_requires_key(tmp_path):
    script = tmp_path / 'stt'
    script.write_text((Path(__file__).resolve().parents[1] / 'stt').read_text())
    env = tmp_path / '.env'
    env.write_text('STT_PROVIDER=deepgram\nDEEPGRAM_API_KEY=secret-one\n')
    before = env.read_text()
    result = subprocess.run([sys.executable, str(script), 'whisper_manglish_hf'], capture_output=True, text=True)
    assert result.returncode != 0 and env.read_text() == before
    env.write_text(before + 'HF_WHISPER_API_TOKEN=secret-two\n')
    result = subprocess.run([sys.executable, str(script), 'whisper_manglish_hf'], capture_output=True, text=True)
    assert result.returncode == 0
    assert 'STT_PROVIDER=whisper_manglish_hf' in env.read_text()
    assert 'DEEPGRAM_API_KEY=secret-one' in env.read_text()
    assert 'HF_WHISPER_API_TOKEN=secret-two' in env.read_text()
    assert 'secret-' not in result.stdout + result.stderr
