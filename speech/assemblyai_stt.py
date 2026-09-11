"""AssemblyAI v3 streaming for Plivo's native 8 kHz mu-law audio."""
import asyncio
import base64
import json
import logging
from urllib.parse import urlencode
from websockets.asyncio.client import connect
import config

logger = logging.getLogger(__name__)
MODELS = ('universal-streaming-english', 'universal-streaming-multilingual', 'universal-3-5-pro')


def connection_url():
    if config.ASSEMBLY_MODEL not in MODELS:
        raise ValueError('Unsupported AssemblyAI streaming model')
    # No format_turns or legacy confidence parameters: Pro doesn't use them.
    return 'wss://streaming.assemblyai.com/v3/ws?' + urlencode({
        'speech_model': config.ASSEMBLY_MODEL, 'sample_rate': 8000, 'encoding': 'pcm_mulaw',
    })


async def stream_utterance(plivo, call_uuid: str) -> str:
    if not config.ASSEMBLY_API_KEY:
        raise RuntimeError('ASSEMBLY_API_KEY is required')
    async with connect(connection_url(), additional_headers={'Authorization': config.ASSEMBLY_API_KEY},
                       open_timeout=10, close_timeout=2, max_size=1024 * 1024) as assembly:
        sender = receiver = None
        terminated = False
        transcript = ''
        try:
            first = json.loads(await asyncio.wait_for(assembly.recv(), timeout=10))
            if first.get('type') != 'Begin':
                raise RuntimeError('AssemblyAI did not begin a session')
            logger.info('AssemblyAI session ready model=%s encoding=pcm_mulaw sample_rate=8000', config.ASSEMBLY_MODEL)

            async def forward():
                started = False
                pending = bytearray()
                while True:
                    event = await plivo.receive_json()
                    if event.get('event') == 'start':
                        start = event['start']
                        fmt = start.get('mediaFormat', {})
                        if (start.get('callId') != call_uuid or fmt.get('encoding') != 'audio/x-mulaw'
                                or int(fmt.get('sampleRate', 0)) != 8000):
                            raise ValueError('Unexpected Plivo stream metadata')
                        started = True
                    elif event.get('event') == 'media':
                        if not started:
                            raise ValueError('Audio arrived before stream metadata')
                        pending.extend(base64.b64decode(event['media']['payload'], validate=True))
                        # Plivo sends ~20 ms. AssemblyAI requires 50–1000 ms:
                        # aggregate into 100 ms frames, without resampling.
                        while len(pending) >= 800:
                            await assembly.send(bytes(pending[:800]))
                            del pending[:800]
                    elif event.get('event') == 'stop':
                        if pending:
                            await assembly.send(bytes(pending).ljust(800, b'\xff'))
                        return

            async def receive():
                nonlocal transcript, terminated
                async for raw in assembly:
                    event = json.loads(raw)
                    if event.get('type') == 'Termination':
                        terminated = True
                        return
                    if event.get('type') == 'Error' or event.get('error'):
                        raise RuntimeError('AssemblyAI reported a streaming error')
                    if event.get('type') == 'Turn' and event.get('end_of_turn'):
                        text = event.get('transcript', '').strip()
                        if text:
                            transcript = text
                            return
                raise RuntimeError('AssemblyAI closed before a completed turn')

            sender = asyncio.create_task(forward())
            receiver = asyncio.create_task(receive())
            done, _ = await asyncio.wait([sender, receiver], timeout=config.ASSEMBLY_TURN_TIMEOUT,
                                         return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            # Stop audio first. Terminate on success, failure, timeout, and caller
            # disconnect so billed provider sessions don't remain open.
            for task in (sender, receiver):
                if task is not None:
                    task.cancel()
            await asyncio.gather(*(t for t in (sender, receiver) if t is not None), return_exceptions=True)
            if not terminated:
                try:
                    await asyncio.wait_for(assembly.send(json.dumps({'type': 'Terminate'})), timeout=2)
                    async with asyncio.timeout(3):
                        async for raw in assembly:
                            event = json.loads(raw)
                            if event.get('type') == 'Turn' and event.get('end_of_turn') and not transcript:
                                transcript = event.get('transcript', '').strip()
                            if event.get('type') == 'Termination':
                                logger.info('AssemblyAI session terminated')
                                break
                except Exception:
                    logger.warning('AssemblyAI termination acknowledgement unavailable; closing socket')
        logger.info('AssemblyAI completed turn has_transcript=%s', bool(transcript))
        return transcript
