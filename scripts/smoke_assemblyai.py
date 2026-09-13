"""Live-test the production adapter using an 8 kHz mono PCM16 synthetic WAV.

Usage: python scripts/smoke_assemblyai.py /tmp/test.wav [--model MODEL]
Loads key fields from the repository .env if not already in the environment.
Never uses caller recordings or prints credentials. Prints the test transcript.
"""
import argparse
import asyncio
import base64
import os
from pathlib import Path
import sys
import wave
import warnings
warnings.filterwarnings('ignore', category=DeprecationWarning)
import audioop  # test fixture conversion; production adapter forwards mu-law unchanged

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
for line in (ROOT / '.env').read_text().splitlines() if (ROOT / '.env').exists() else []:
    name, sep, value = line.partition('=')
    if name.strip() in {'ASSEMBLY_API_KEY', 'ASSEMBLYAI_API_KEY'}:
        os.environ.setdefault(name.strip(), value.strip().strip('\"\''))
import config
from speech.assemblyai_stt import MODELS, stream_utterance
from telephony_stream import TwilioStream


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('wav', nargs='?', default=str(ROOT / 'tests/fixtures/stt_smoke.wav'))
    parser.add_argument('--model', choices=MODELS, default='universal-streaming-english')
    parser.add_argument('--carrier', choices=['plivo', 'twilio'], default='plivo')
    args = parser.parse_args()
    config.ASSEMBLY_MODEL = args.model
    with wave.open(args.wav) as wav:
        if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (8000, 1, 2):
            parser.error('Use an 8000 Hz mono PCM16 WAV')
        if not wav.getnframes(): parser.error('WAV contains no audio')
        audio = audioop.lin2ulaw(wav.readframes(wav.getnframes()), 2) + b'\xff' * 40000
    class Plivo:
        offset = -1
        async def receive_json(self):
            if self.offset == -1:
                self.offset = 0
                return {'event': 'start', 'start': {('callSid' if args.carrier == 'twilio' else 'callId'): 'synthetic-test',
                    'mediaFormat': {'encoding': 'audio/x-mulaw', 'sampleRate': 8000, 'channels': 1}}}
            if self.offset >= len(audio):
                await asyncio.Future()
            await asyncio.sleep(.02)
            chunk = audio[self.offset:self.offset+160]
            self.offset += len(chunk)
            return {'event': 'media', 'media': {'payload': base64.b64encode(chunk).decode()}}
    try:
        socket = Plivo()
        transcript = await stream_utterance(TwilioStream(socket) if args.carrier == 'twilio' else socket, 'synthetic-test')
    except Exception as exc:
        print('FAIL:', type(exc).__name__, 'HTTP:', getattr(getattr(exc, 'response', None), 'status_code', None))
        return 1
    print('Synthetic carrier format:', args.carrier)
    print('Model:', args.model)
    print('Synthetic test transcript:', transcript)
    return 0 if transcript else 1


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
