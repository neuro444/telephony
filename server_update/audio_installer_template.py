#!/usr/bin/env python3
"""One-shot customer audio rollout. Uses existing Compose paths and credentials."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

PAYLOAD = ""
PAYLOAD_SHA256 = ""


def run(args, **kwargs):
    return subprocess.run(args, check=True, text=True, **kwargs)


def inspect(name):
    return json.loads(run(['docker', 'inspect', name], capture_output=True).stdout)[0]


def compose(info):
    labels = info['Config']['Labels']
    files = labels.get('com.docker.compose.project.config_files', '').split(',')
    project = labels.get('com.docker.compose.project')
    service = labels.get('com.docker.compose.service')
    cwd = Path(labels.get('com.docker.compose.project.working_dir', ''))
    if not project or not service or not cwd.is_dir() or not all(Path(f).is_file() for f in files):
        raise RuntimeError('Cannot discover Compose setup; no files changed')
    cmd = ['docker', 'compose', '--project-directory', str(cwd), '-p', project]
    for f in files:
        cmd += ['-f', f]
    return cwd, cmd, service


def update_env(path, values):
    lines = path.read_text().splitlines()
    for key, value in values.items():
        lines = [line for line in lines if not re.match(r'^\s*(?:export\s+)?'+re.escape(key)+r'\s*=', line)]
        lines.append(f'{key}={value}')
    path.write_text('\n'.join(lines)+'\n')
    path.chmod(0o600)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gateway-container', default='plivo-gateway')
    parser.add_argument('--chat-container', default='chat-manager-api')
    parser.add_argument('--callers', help='Comma-separated E.164 test callers')
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Run with sudo python3')
    callers = args.callers or input('Test caller number(s), comma-separated E.164: ').strip()
    if not callers or not all(re.fullmatch(r'\+[1-9][0-9]{7,14}', x.strip()) for x in callers.split(',')):
        parser.error('Provide valid test caller numbers, e.g. +15551234567')
    callers = ','.join(x.strip() for x in callers.split(','))
    raw = base64.b64decode(PAYLOAD)
    if hashlib.sha256(raw).hexdigest() != PAYLOAD_SHA256:
        raise RuntimeError('Payload checksum mismatch')
    payload = json.loads(raw)
    targets = {}
    for key, container in [('chat', args.chat_container), ('gateway', args.gateway_container)]:
        info = inspect(container)
        cwd, cmd, service = compose(info)
        effective = json.loads(run(cmd + ['config', '--format', 'json'], capture_output=True).stdout)
        build = effective['services'][service].get('build', {})
        context = Path(build.get('context', str(cwd))) if isinstance(build, dict) else Path(build)
        if not context.is_absolute():
            context = cwd / context
        if not context.is_dir() or not (cwd / '.env').is_file():
            raise RuntimeError('Missing build context or .env; no files changed')
        env = dict(x.split('=', 1) for x in info['Config'].get('Env', []) if '=' in x)
        if key == 'chat':
            if not env.get('API_KEY'):
                raise RuntimeError('Chat Manager API_KEY must be configured before audio rollout')
            if not any(m['Destination'] == '/data' for m in info.get('Mounts', [])):
                raise RuntimeError('Chat Manager requires persistent /data storage before rollout')
        selection = None
        if key == 'gateway':
            probe = "import config,json; print(json.dumps({'STT_PROVIDER':config.STT_PROVIDER,'TELEPHONY_PROVIDER':getattr(config,'TELEPHONY_PROVIDER','plivo')}))"
            selection = json.loads(run(['docker', 'exec', container, 'python', '-c', probe], capture_output=True).stdout)
            if selection['STT_PROVIDER'] not in {'deepgram','assemblyai','sarvam','elevenlabs'}:
                raise RuntimeError('Customer stream audio requires external STT; current STT is preserved. No files changed.')
        targets[key] = dict(info=info, cwd=cwd, context=context, cmd=cmd, service=service, container=container, selection=selection)
    backup = Path('/var/backups/customer-audio') / time.strftime('%Y%m%d-%H%M%S')
    backup.mkdir(parents=True, mode=0o700)
    original = []
    try:
        for key, target in targets.items():
            files = {target['context'] / name: base64.b64decode(data) for name, data in payload[key].items()}
            files[target['cwd'] / '.env'] = (target['cwd'] / '.env').read_bytes()
            for path, content in files.items():
                if path.is_symlink():
                    raise RuntimeError('Refusing to replace symlink')
                saved = backup / f'{len(original)}.bak'
                existed = path.exists()
                if existed:
                    shutil.copy2(path, saved)
                original.append((path, saved, existed))
                (backup / 'manifest.json').write_text(json.dumps([
                    {'path': str(p), 'backup': str(b), 'existed': e} for p,b,e in original]))
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            if key == 'gateway':
                update_env(target['cwd'] / '.env', {**target['selection'], 'DEBUG_CUSTOMER_AUDIO': 'true', 'DEBUG_AUDIO_CALLERS': callers})
        for target in targets.values():
            run(target['cmd'] + ['build', target['service']])
        for key, target in targets.items():
            run(target['cmd'] + ['up', '-d', '--no-deps', '--no-build', '--force-recreate', target['service']])
            port = 8000 if key == 'chat' else 8080
            probe = f"import urllib.request; import customer_audio; urllib.request.urlopen('http://127.0.0.1:{port}/health', timeout=3)"
            for attempt in range(30):
                try:
                    run(['docker', 'exec', target['container'], 'python', '-c', probe], capture_output=True)
                    break
                except subprocess.CalledProcessError:
                    if attempt == 29:
                        raise RuntimeError(key + ' health failed')
                    time.sleep(2)
        # Prove persistent storage is writable by the actual application user.
        check = "import customer_audio as a; m=a.save({'codec':'audio/x-mulaw','sample_rate':8000,'mulaw_base64':'//8=','complete':True},'installer-probe'); assert m and 'id' in m; a.read('installer-probe',m['id']); a.delete_session('installer-probe')"
        run(['docker', 'exec', args.chat_container, 'python', '-c', check], capture_output=True)
        old_env = dict(x.split('=', 1) for x in targets['gateway']['info']['Config']['Env'] if '=' in x)
        new_env = dict(x.split('=', 1) for x in inspect(args.gateway_container)['Config']['Env'] if '=' in x)
        expected = {**targets['gateway']['selection'], 'SPEECH_HINTS': old_env.get('SPEECH_HINTS')}
        for name, value in expected.items():
            if new_env.get(name) != value:
                raise RuntimeError(name + ' unexpectedly changed; rolling back')
        if new_env.get('DEBUG_CUSTOMER_AUDIO') != 'true' or new_env.get('DEBUG_AUDIO_CALLERS') != callers:
            raise RuntimeError('Compose overrides prevented audio enablement')
        print('SUCCESS. Audio enabled for designated test callers. Backup:', backup)
        print('Open a NEW call in Chat Manager; customer messages now include Load audio.')
    except Exception:
        for path, saved, existed in reversed(original):
            if existed:
                shutil.copy2(saved, path)
            else:
                path.unlink(missing_ok=True)
        for target in targets.values():
            try:
                run(['docker', 'image', 'tag', target['info']['Image'], target['info']['Config']['Image']])
                run(target['cmd'] + ['up', '-d', '--no-deps', '--no-build', '--force-recreate', target['service']])
            except Exception:
                print('Rollback restart failed for', target['container'], '; backup:', backup)
        raise


if __name__ == '__main__':
    main()
