# Customer audio server update

`setup_customer_audio_server.py` bundles the matching gateway and Chat Manager
source changes. Copy to the server and run `sudo python3 setup_customer_audio_server.py`
between calls. It discovers the two Compose projects through the existing
`plivo-gateway` and `chat-manager-api` containers (override their names with
`--gateway-container` / `--chat-container`). It enables recording for all caller numbers,
backs up source/settings, builds both images, checks health/storage, and restores
previous images/files on failure. No credentials are bundled, and no separate
Git pull or Nginx change is needed. Carrier/STT/hints selection is preserved.

New transcribed streaming turns from all caller numbers are recorded automatically.
Open the call in Chat Manager: the player loads automatically beneath each
customer message. Press Play to listen; audio does not autoplay. WAV, original mu-law and diagnostics
can be downloaded there. Retention is seven days; storage quota is 512 MiB.
Backend API-key authorization and a persistent Chat Manager `/data` mount are
required. Audio inherits the dashboard's existing staff ingress access control.

To replay a downloaded mu-law clip without creating an order:

```bash
docker cp clip.mulaw plivo-gateway:/tmp/clip.mulaw
docker cp clip.json plivo-gateway:/tmp/clip.json
docker exec plivo-gateway python scripts/replay_customer_audio.py /tmp/clip.mulaw --provider deepgram --hints-file /tmp/clip.json
```

Provider requests are billed. Explicit alternatives: assemblyai, sarvam,
elevenlabs. No carrier/STT fallback occurs. The replay uses current model
settings and saved hints, fixed 20 ms chunks, plus five seconds of synthetic
trailing silence. The source hash identifies identical audio across runs.
