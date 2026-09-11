# Switch phone-call speech recognition

Run in the telephony checkout on the machine you want to configure:

```sh
./stt assemblyai      # default: universal-streaming-english
./stt                 # show .env provider, model, key presence (never the key)
./stt sarvam          # Saaras v3
./stt deepgram        # Nova-3
./stt plivo           # Plivo GetInput
```

The switch writes only provider selection and a missing model default to the
checkout's `.env`. It preserves other settings and requires a key before
selecting Sarvam or Deepgram. It does not restart the gateway or change a
remote server. On the server, apply code changes with:

```sh
docker compose up -d --build gateway
```

For later configuration-only switches, recreate the gateway:

```sh
docker compose up -d --force-recreate gateway
```

Recreating a gateway interrupts active calls; do this between calls. `.env` is
not committed, so set the key on the server separately. AssemblyAI is the default when STT_PROVIDER is unset, and is selected locally.
An existing server .env overrides the default; run ./stt assemblyai to switch it.

| Provider | Selection | Model setting | Credential |
| --- | --- | --- | --- |
| AssemblyAI direct streaming | `STT_PROVIDER=assemblyai` | `ASSEMBLY_MODEL=universal-streaming-english` | `ASSEMBLY_API_KEY` (or `ASSEMBLYAI_API_KEY`) |
| Deepgram direct streaming | `STT_PROVIDER=deepgram` | `DEEPGRAM_MODEL=nova-3` | `DEEPGRAM_API_KEY` |
| Sarvam direct streaming | `STT_PROVIDER=sarvam` | `SARVAM_MODEL=saaras:v3` | `SARVAM_API_KEY` |
| Plivo built-in GetInput | `STT_PROVIDER=plivo` | `PLIVO_SPEECH_MODEL=phone_call` | Existing Plivo credentials |

Config is loaded in `config.py`. The adapters are `speech/deepgram_stt.py` and
`speech/sarvam_stt.py`. The Sarvam adapter uses `mode=transcribe`, defaults to
`SARVAM_LANGUAGE=en-IN`, and converts Plivo mu-law into PCM at the same 8000 Hz.
Its WebSocket sends `input_audio_codec=pcm_s16le` in the connection URL and
`audio/wav` in the per-message encoding field: the latter literal is required
by the live service's message validator. It waits for transcription after VAD,
not merely END_SPEECH. Sarvam's SDK file-upload example alone cannot replace
this streaming path. No Sarvam SDK dependency is needed for the native socket.

Both external providers retain the existing greeting, reply playback, order,
manager transfer and hangup paths. A provider failure transfers to the manager;
it never silently enables Plivo built-in recognition. Existing turn-based
playback/listening, single-worker memory state, and no STT cost emission remain.

## Plivo AI Studio is a different configuration surface

The AI Studio provider menu belongs to Plivo's hosted agent product. Our
application uses Voice XML and our own brain/TTS pipeline. Its GetInput
`speechModel` choices are `phone_call`, `default`, and `command_and_search`.
The AI Studio Deepgram/Groq/Cartesia/Sarvam names cannot simply be assigned to
that XML attribute. Our Deepgram and Sarvam options connect to those providers
directly, using our own API keys.

## Local validation, September 11, 2026

Live Sarvam `saaras:v3` authentication and actual transcription passed. A locally
synthesized voice saying "Hello, I would like two samosas please" was encoded
as Plivo-format 8 kHz mu-law, fed through the production Sarvam adapter, and
returned as "Hello, I would like two samosas please." This is more than an
API-key handshake, but is not a real PSTN call. Tests also cover provider
selection, error handoff without Plivo STT, VAD-before-transcript ordering,
audio conversion, environment editing, and the existing Deepgram/Plivo flows.
A server call is still required after deploying Sarvam.

## Remember the stream-start lesson

Preserve `bidirectional="true"` AND `keepCallAlive="true"` in Plivo Stream XML.
The earlier missing bidirectional attribute caused a 3 ms Stream event,
immediate Redirect, and "No audio streams initiated" in Plivo's call logs.
The user confirmed fixing this resolved the issue. Status callbacks and a
regression assertion now protect this setup.

When a layer has no activity, prove reachability with a minimal probe before
changing infrastructure. A deliberately invalid WebSocket token should yield
403 *and* a matching gateway WebSocket log. Compare that with the real call;
if the real call has no connection, inspect Plivo's XML and call diagnostics.
Do not mistake `/stream_result` HTTP 200 for successful transcription. Verify
the running container's actual generated XML, not just its provider variable.
Test provider credentials and real synthetic audio locally before rollout.

References:
- https://docs.sarvam.ai/api/api-guides-tutorials/speech-to-text/streaming-api
- https://www.plivo.com/docs/voice/xml/input
- https://www.plivo.com/docs/aiagent/aistudio/agentconfiguration/stt
- https://www.plivo.com/docs/voice-agents/audio-streaming/xml/stream

## AssemblyAI models and live validation

```sh
./stt assemblyai --model universal-streaming-english
./stt assemblyai --model universal-streaming-multilingual
./stt assemblyai --model universal-3-5-pro
```

The production adapter (`speech/assemblyai_stt.py`) connects to the v3 socket
with `speech_model`, `encoding=pcm_mulaw`, and `sample_rate=8000`. It aggregates
Plivo's 20 ms packets into 100 ms messages without resampling. It waits for
Begin and then a Turn with end_of_turn=true, ignores partials, and explicitly
sends Terminate and drains the acknowledgement before closing. No legacy
format_turns/confidence parameters are sent, including for Universal-3.5 Pro.
Failures transfer to the manager without switching to Plivo STT.

All three models were tested live locally through this adapter using synthetic
Plivo-format audio, not just a handshake. English and multilingual returned
"hello i would like two samosas please"; Pro returned
"Hello, I would like 2 samosas please." This confirms the wire format and
credentials work locally, not real-call/menu accuracy on a server.

`tests/fixtures/stt_smoke.wav` is synthetic speech generated locally with macOS
speech synthesis, converted to mono PCM16 at 8 kHz; it contains no caller audio.
Repeat the live test (billed provider session; key required):

```sh
python scripts/smoke_assemblyai.py
python scripts/smoke_assemblyai.py --model universal-3-5-pro
```

Before replacing a server container, build and test the new image against the
server's key, then select and start it:

```sh
docker compose build gateway
docker compose run --rm --no-deps gateway python scripts/smoke_assemblyai.py
./stt assemblyai --model universal-streaming-english
docker compose up -d --no-build gateway
```

Startup logs show provider and model. AssemblyAI logs session readiness,
termination acknowledgement, and whether a transcript was produced, without
logging caller transcripts or API keys. A real call still needs validation.

Sources:
- https://www.assemblyai.com/docs/streaming/api-spec/streaming-websocket
- https://www.assemblyai.com/docs/streaming/migration-guides/universal-to-universal-3-5-pro-streaming
