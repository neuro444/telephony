import base64
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load():
    spec = importlib.util.spec_from_file_location('audio_installer', ROOT/'setup_customer_audio_server.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('fail_build', [False, True])
def test_setup_preserves_settings_and_rolls_back(tmp_path, monkeypatch, fail_build):
    m = load()
    infos = {}
    for key, name, service in [('chat','chat-manager-api','api'), ('gateway','plivo-gateway','gateway')]:
        path = tmp_path/key
        path.mkdir()
        (path/'docker-compose.yml').write_text('services: {}')
        (path/'.env').write_text('STT_PROVIDER=deepgram\nTELEPHONY_PROVIDER=plivo\nKEEP=unchanged\n')
        (path/'app.py').write_text('previous source')
        infos[name] = {'Image':'sha256:old-'+key, 'Config':{'Image':key+':latest',
            'Env':['STT_PROVIDER=deepgram','TELEPHONY_PROVIDER=plivo','API_KEY=test'],
            'Labels':{'com.docker.compose.project.config_files':str(path/'docker-compose.yml'),
            'com.docker.compose.project':'project-'+key, 'com.docker.compose.service':service,
            'com.docker.compose.project.working_dir':str(path)}}, 'Mounts':[{'Destination':'/data'}]}
    monkeypatch.setattr(m, 'inspect', lambda name: infos[name])
    monkeypatch.setattr(m.os, 'geteuid', lambda: 0)
    original_path = m.Path
    monkeypatch.setattr(m, 'Path', lambda value: tmp_path/'backups' if value=='/var/backups/customer-audio' else original_path(value))
    commands = []
    def run(args, **kwargs):
        commands.append(args)
        if 'config' in args:
            key = 'chat' if 'project-chat' in args else 'gateway'
            service = 'api' if key=='chat' else 'gateway'
            return SimpleNamespace(stdout=json.dumps({'services':{service:{'build':{'context':str(tmp_path/key)}}}}))
        if args[:2] == ['docker','exec'] and 'print(json.dumps' in args[-1]:
            return SimpleNamespace(stdout=json.dumps({'STT_PROVIDER':'deepgram','TELEPHONY_PROVIDER':'plivo'}))
        if 'build' in args and fail_build:
            raise RuntimeError('build failed')
        if 'up' in args and 'project-gateway' in args and not fail_build:
            infos['plivo-gateway']['Config']['Env'] += ['DEBUG_CUSTOMER_AUDIO=true','DEBUG_AUDIO_CALLERS=+15551234567']
        return SimpleNamespace(stdout='')
    monkeypatch.setattr(m, 'run', run)
    monkeypatch.setattr('sys.argv', ['setup', '--callers', '+15551234567'])
    if fail_build:
        with pytest.raises(RuntimeError, match='build failed'):
            m.main()
        assert (tmp_path/'gateway/app.py').read_text() == 'previous source'
        assert not (tmp_path/'gateway/customer_audio.py').exists()
        assert 'DEBUG_CUSTOMER_AUDIO' not in (tmp_path/'gateway/.env').read_text()
        assert any(cmd[:3]==['docker','image','tag'] for cmd in commands)
    else:
        m.main()
        env = (tmp_path/'gateway/.env').read_text()
        assert 'STT_PROVIDER=deepgram' in env and 'KEEP=unchanged' in env
        assert 'DEBUG_CUSTOMER_AUDIO=true' in env
        assert (tmp_path/'chat/customer_audio.py').exists()
    assert list((tmp_path/'backups').glob('*/manifest.json'))


def test_payload_only_named_source_files():
    m = load()
    data = json.loads(base64.b64decode(m.PAYLOAD))
    assert all('.env' not in files for files in data.values())
    assert 'customer_audio.py' in data['gateway'] and 'customer_audio.py' in data['chat']
