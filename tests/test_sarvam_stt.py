import asyncio
import base64
import json
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
from speech import sarvam_stt
from test_turn_flow import client, stub_brain, BASE, ANSWER_FORM, tags


def test_mulaw_known_samples():
    assert sarvam_stt.mulaw_to_pcm(bytes([0, 128, 127, 255])) == struct.pack('<hhhh', -32124, 32124, 0, 0)


@pytest.mark.parametrize('failed', [False, True])
def test_sarvam_call_dispatch_and_no_plivo_fallback(client, monkeypatch, failed):
    monkeypatch.setattr(config, 'STT_PROVIDER', 'sarvam')
    seen=[]
    stub_brain(monkeypatch, BASE, seen)
    result=client.post('/voice/answer',data=ANSWER_FORM)
    stream=parseString(result.text).getElementsByTagName('Stream')[0]
    assert stream.getAttribute('bidirectional') == 'true'
    async def adapter(ws, call_id):
        if failed: raise RuntimeError('provider down')
        return 'two samosas'
    monkeypatch.setattr(gateway,'sarvam_stream_utterance',adapter)
    with client.websocket_connect(urlsplit(stream.firstChild.data).path) as ws:
        with pytest.raises(WebSocketDisconnect): ws.receive_text()
    token=calls.get('cu1').stream_token
    result=client.post('/voice/stream_result/'+token,data=ANSWER_FORM)
    assert calls.get('cu1').stt_provider == 'sarvam'
    assert 'GetInput' not in result.text
    if failed:
        assert tags(result.text) == ['Speak','Dial']
    else:
        assert seen[-1]['message']=='two samosas'
        assert tags(result.text)==['Play','Stream','Redirect']


@pytest.mark.parametrize('api_error', [False, True])
def test_sarvam_waits_for_transcript_after_vad(monkeypatch, api_error):
    monkeypatch.setattr(config,'SARVAM_API_KEY','test')
    sent=[]
    class Socket:
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def send(self,message): sent.append(json.loads(message))
        def __aiter__(self): return self.messages()
        async def messages(self):
            while not sent: await asyncio.sleep(0)
            yield json.dumps({'type':'events','data':{'signal_type':'END_SPEECH'}})
            await asyncio.sleep(0)
            if api_error: yield json.dumps({'type':'error','data':{'message':'bad audio'}})
            else: yield json.dumps({'type':'data','data':{'transcript':'two samosas'}})
    class Plivo:
        def __init__(self): self.count=0
        async def receive_json(self):
            self.count+=1
            if self.count==1:
                return {'event':'start','start':{'callId':'test','mediaFormat':{'encoding':'audio/x-mulaw','sampleRate':8000}}}
            if self.count==2:
                return {'event':'media','media':{'payload':base64.b64encode(bytes([0,255])).decode()}}
            await asyncio.Future()
    monkeypatch.setattr(sarvam_stt,'connect',lambda *a,**kw:Socket())
    if api_error:
        with pytest.raises(RuntimeError): asyncio.run(sarvam_stt.stream_utterance(Plivo(),'test'))
    else:
        assert asyncio.run(sarvam_stt.stream_utterance(Plivo(),'test'))=='two samosas'
    assert sent[0]['audio']['encoding']=='audio/wav'
    assert base64.b64decode(sent[0]['audio']['data'])==struct.pack('<hh',-32124,0)


def test_toggle_preserves_secrets_and_requires_key(tmp_path):
    script=tmp_path/'stt'
    script.write_text((Path(__file__).resolve().parents[1]/'stt').read_text())
    env=tmp_path/'.env'
    env.write_text('STT_PROVIDER=deepgram\nDEEPGRAM_API_KEY=secret-one\n')
    before=env.read_text()
    result=subprocess.run([sys.executable,str(script),'sarvam'],capture_output=True,text=True)
    assert result.returncode != 0 and env.read_text()==before
    env.write_text(before+'SARVAM_API_KEY=secret-two\n')
    result=subprocess.run([sys.executable,str(script),'sarvam'],capture_output=True,text=True)
    assert result.returncode==0
    assert 'STT_PROVIDER=sarvam' in env.read_text()
    assert 'SARVAM_MODEL=saaras:v3' in env.read_text()
    assert 'DEEPGRAM_API_KEY=secret-one' in env.read_text()
    assert 'SARVAM_API_KEY=secret-two' in env.read_text()
    assert 'secret-' not in result.stdout+result.stderr
