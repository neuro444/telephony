"""Saaras v3 streaming, using the same one-utterance Plivo transport."""
import asyncio
import base64
import json
import struct
from urllib.parse import urlencode

from websockets.asyncio.client import connect
import config


def _sample(value: int) -> bytes:
    value = (~value) & 255
    magnitude = (((value & 15) << 3) + 132) << ((value >> 4) & 7)
    return struct.pack('<h', 132 - magnitude if value & 128 else magnitude - 132)


_MULAW_PCM = tuple(_sample(value) for value in range(256))


def mulaw_to_pcm(audio: bytes) -> bytes:
    return b''.join(_MULAW_PCM[value] for value in audio)


def connection_url() -> str:
    return 'wss://api.sarvam.ai/speech-to-text/ws?' + urlencode({
        'model': config.SARVAM_MODEL, 'mode': 'transcribe',
        'language-code': config.SARVAM_LANGUAGE,
        'sample_rate': 8000, 'input_audio_codec': 'pcm_s16le',
        'vad_signals': 'true',
    })


async def stream_utterance(plivo, call_uuid: str) -> str:
    if not config.SARVAM_API_KEY:
        raise RuntimeError('SARVAM_API_KEY is required')
    async with connect(connection_url(),
                       additional_headers={'api-subscription-key': config.SARVAM_API_KEY},
                       open_timeout=10, close_timeout=2, max_size=1024 * 1024) as sarvam:
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
                    pcm = mulaw_to_pcm(base64.b64decode(event['media']['payload'], validate=True))
                    # Live API requires this per-message literal even with raw PCM
                    # selected by input_audio_codec. Verified against saaras:v3.
                    await sarvam.send(json.dumps({'audio': {
                        'data': base64.b64encode(pcm).decode(),
                        'sample_rate': 8000, 'encoding': 'audio/wav',
                    }}))
                elif event.get('event') == 'stop':
                    return ''

        async def receive():
            async for raw in sarvam:
                event = json.loads(raw)
                if event.get('type') == 'error':
                    raise RuntimeError('Sarvam rejected the audio stream')
                # END_SPEECH can precede the actual transcription: don't close
                # on a VAD event. This API emits completed utterance transcripts.
                if event.get('type') == 'data':
                    transcript = event.get('data', {}).get('transcript', '').strip()
                    if transcript:
                        return transcript
            raise RuntimeError('Sarvam closed before transcription')

        sender, receiver = asyncio.create_task(forward()), asyncio.create_task(receive())
        try:
            done, _ = await asyncio.wait([sender, receiver],
                timeout=config.SARVAM_TURN_TIMEOUT, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()  # propagate transport/provider errors
            return receiver.result() if receiver in done else ''
        finally:
            sender.cancel()
            receiver.cancel()
            await asyncio.gather(sender, receiver, return_exceptions=True)
