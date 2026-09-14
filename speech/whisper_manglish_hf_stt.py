"""One live caller utterance transcribed by a self-hosted Whisper fine-tune
on a Hugging Face Inference Endpoint. Unlike the other adapters, this is a
single HTTP call per turn, not a streaming vendor session; end-of-turn is
Plivo's own "stop" event."""
import asyncio
import base64
import csv
import struct
from pathlib import Path

import httpx

import config

MENU_CSV = Path(__file__).resolve().parent.parent / 'menu' / 'menu.csv'
# Rough safeguard: the endpoint's tokenizer isn't available here to measure
# exactly, but testing showed ~28 of this menu's ~150 phrases fit Whisper's
# 448-token decoder budget. Send only the first N to avoid an HTTP 400 from
# an oversized prompt; revisit if the menu vocabulary shrinks or the
# endpoint's tokenizer changes.
_MAX_HINT_PHRASES = 28


def _menu_prompt() -> str:
    if not MENU_CSV.exists():
        return ''
    with MENU_CSV.open(newline='', encoding='utf-8') as f:
        names = [row[0].strip() for row in csv.reader(f) if row and row[0].strip()]
    return ', '.join(names[:_MAX_HINT_PHRASES])


def _sample(value: int) -> bytes:
    value = (~value) & 255
    magnitude = (((value & 15) << 3) + 132) << ((value >> 4) & 7)
    return struct.pack('<h', 132 - magnitude if value & 128 else magnitude - 132)


_MULAW_PCM = tuple(_sample(value) for value in range(256))


def mulaw_to_pcm(audio: bytes) -> bytes:
    return b''.join(_MULAW_PCM[value] for value in audio)


def _wav_bytes(pcm: bytes, sample_rate: int = 8000) -> bytes:
    data_size = len(pcm)
    header = (
        b'RIFF' + struct.pack('<I', 36 + data_size) + b'WAVEfmt '
        + struct.pack('<IHHIIHH', 16, 1, 1, sample_rate, sample_rate * 2, 2, 16)
        + b'data' + struct.pack('<I', data_size)
    )
    return header + pcm


async def stream_utterance(plivo, call_uuid: str) -> str:
    if not config.HF_WHISPER_ENDPOINT_URL or not config.HF_WHISPER_API_TOKEN:
        raise RuntimeError('HF_WHISPER_ENDPOINT_URL and HF_WHISPER_API_TOKEN are required')

    chunks = bytearray()

    async def forward():
        started = False
        while True:
            event = await plivo.receive_json()
            if event.get('event') == 'start':
                start = event['start']
                fmt = start.get('mediaFormat', {})
                if (start.get('callId') != call_uuid
                        or fmt.get('encoding') != 'audio/x-mulaw'
                        or int(fmt.get('sampleRate', 0)) != 8000):
                    raise ValueError('Unexpected Plivo stream metadata')
                started = True
            elif event.get('event') == 'media':
                if not started:
                    raise ValueError('Audio arrived before stream metadata')
                chunks.extend(base64.b64decode(event['media']['payload'], validate=True))
            elif event.get('event') == 'stop':
                return

    # DIAGNOSTIC: reading from Plivo immediately after accept() produced an
    # early clean disconnect (code 1000, zero media frames) in production
    # testing on 2026-09-14. The vendor adapters don't hit this because their
    # own websocket handshake to Deepgram/Sarvam naturally takes ~50-300ms
    # before their first plivo.receive_json(); this adapter has no equivalent
    # outbound call, so nothing delays it. This sleep imitates that delay to
    # test whether it's a race in Plivo's own stream setup. If calls still
    # disconnect immediately with this in place, the timing theory is wrong
    # and this should be removed rather than tuned larger.
    await asyncio.sleep(0.2)
    task = asyncio.create_task(forward())
    try:
        await asyncio.wait_for(task, timeout=config.HF_WHISPER_TURN_TIMEOUT)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    if not chunks:
        return ''

    wav = _wav_bytes(mulaw_to_pcm(bytes(chunks)))
    payload = {'inputs': base64.b64encode(wav).decode('ascii')}
    prompt = _menu_prompt()
    if prompt:
        payload['parameters'] = {'prompt': prompt}

    async with httpx.AsyncClient(timeout=config.HF_WHISPER_TURN_TIMEOUT) as client:
        response = await client.post(
            config.HF_WHISPER_ENDPOINT_URL,
            headers={'Authorization': 'Bearer ' + config.HF_WHISPER_API_TOKEN},
            json=payload,
        )
    if response.status_code != 200:
        raise RuntimeError(f'HF Whisper endpoint returned HTTP {response.status_code}: {response.text[:300]}')
    return response.json().get('text', '').strip()
