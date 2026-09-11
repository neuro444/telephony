# Phone-call STT: Deepgram Nova-3

Implemented a provider switch for phone calls. This uses Deepgram's native
WebSocket API, equivalent to the SDK streaming example; no radio stream,
recording download, or batch transcription is involved.

## Configuration

The local `.env` selects `STT_PROVIDER=deepgram` and `DEEPGRAM_MODEL=nova-3`.
`DEEPGRAM_API_KEY` is read from the existing environment entry and is never
embedded in source or sent to Plivo. The default for other installations is
`STT_PROVIDER=plivo`.

Optional settings:

- `DEEPGRAM_ENDPOINTING_MS=700`: silence duration that ends an utterance.
- `DEEPGRAM_TURN_TIMEOUT=30`: maximum listening time per turn.
- `SPEECH_LANGUAGE=en-US`: transcription language.
- `SPEECH_HINTS`: comma-separated menu terms, sent as Deepgram keyterms.

After installing requirements, rebuild/restart the gateway to apply settings:

```sh
cd telephony_repo
docker compose up -d --build
```

To switch back, set `STT_PROVIDER=plivo` in `.env` and restart/recreate the
gateway. Provider selection is captured at call answer, so it remains stable
through a call. A Deepgram connection/protocol failure plays an apology and transfers the
call to the manager. It never enables Plivo built-in STT automatically.

## Call flow

1. The existing brain produces the greeting, with existing ElevenLabs/Plivo TTS.
2. XML plays the prompt, then opens an inbound mu-law/8 kHz Plivo stream.
3. The gateway forwards binary audio to Deepgram Nova-3. It ignores interim
   results and joins finalized segments until `speech_final`.
4. The gateway saves the transcript before closing the stream. Plivo continues
   to a signed POST redirect, which consumes that turn's stored transcript.
5. The existing brain, order emission, TTS, manager transfer and hangup logic
   process the turn. A normal reply opens a fresh listening stream.

Silence triggers the existing reprompt. At the listening time limit, only
finalized text is retained. Each stream URL has a random single-use capability;
start metadata must match the call and audio format. Result callbacks require
Plivo signature verification and the current turn token. Old result tokens
cannot replay a completed turn. Pending tasks are cancelled on completion,
timeout, or disconnect. Brain/TTS calls run in the thread pool so they do not
block concurrent audio streams.

## Deployment and validation

The public gateway/proxy must accept WebSocket upgrades on `/voice/stream/`.
Use HTTPS for `PLIVO_PUBLIC_BASE_URL` in production (stream URLs become WSS).
Use one gateway worker, as the existing call registry and transcript handoff
are in memory. Multiple workers/replicas need shared state and stream routing.
Treat stream URLs as credentials; avoid retaining full stream paths in proxy
access logs. No raw audio or transcript is logged by the streaming adapter.

This preserves turn-based playback/listening: caller interruption during a
prompt is not implemented. Deepgram STT cost emission is not yet integrated
into the existing cost feed. The existing Plivo/LLM/TTS accounting is retained.

Automated tests cover audio forwarding, final-segment assembly, session
continuity, token rejection/reuse, signed result callbacks, provider failure,
manager transfer, hangup, and existing Plivo behavior. A real phone-call smoke
test is still required to confirm public proxy upgrades, credential validity,
latency, recognition quality and Plivo's stream-disconnect continuation.
No live phone call or deployment was performed during implementation.

## Protocol references

- [Plivo Stream XML](https://www.plivo.com/docs/voice-agents/audio-streaming/xml/stream)
- [Plivo audio events](https://www.plivo.com/docs/voice-agents/audio-streaming/concepts/audio-streaming-reference)
- [Deepgram streaming API](https://developers.deepgram.com/reference/speech-to-text/listen-streaming)
- [Deepgram endpointing and final results](https://developers.deepgram.com/docs/understand-endpointing-interim-results)
