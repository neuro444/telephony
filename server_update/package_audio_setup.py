#!/usr/bin/env python3
"""Package explicit source files only; no .env, credentials, DBs, or audio logs."""
import base64
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent if (HERE.parent / 'telephony_repo').is_dir() else HERE.parents[1]


def build():
    names = {
        'chat': ['api.py', 'service.py', 'customer_audio.py', 'web/app.js', 'web/style.css'],
        'gateway': ['app.py', 'audio_cache.py', 'config.py', 'customer_audio.py', 'phrase_cache.py',
                    'pii.py', 'plivo_xml.py', 'print_client.py', 'security.py', 'telephony_stream.py',
                    'twilio_xml.py', 'voice_xml.py', 'requirements.txt', 'Dockerfile', '.dockerignore',
                    'scripts/replay_customer_audio.py'],
    }
    gateway = ROOT / 'telephony_repo'
    for package in ('brain', 'calls', 'cost', 'orders', 'speech'):
        names['gateway'] += [str(p.relative_to(gateway)) for p in sorted((gateway/package).glob('*.py'))]
    payload = {}
    for key, files in names.items():
        repo = ROOT / ('chat_manager_repo' if key == 'chat' else 'telephony_repo')
        payload[key] = {name: base64.b64encode((repo/name).read_bytes()).decode() for name in files}
    raw = json.dumps(payload, sort_keys=True).encode()
    script = (HERE/'audio_installer_template.py').read_text().replace('PAYLOAD = ""', 'PAYLOAD = '+repr(base64.b64encode(raw).decode()), 1)
    script = script.replace('PAYLOAD_SHA256 = ""', 'PAYLOAD_SHA256 = '+repr(hashlib.sha256(raw).hexdigest()), 1)
    (HERE/'setup_customer_audio_server.py').write_text(script)
    print('Created setup_customer_audio_server.py; source only, no credentials')


if __name__ == '__main__':
    build()
