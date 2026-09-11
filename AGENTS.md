# Phone-call STT work

Read docs/STT.md before changing STT providers or troubleshooting stream setup.
It records the user's confirmed Deepgram stream-start fix and the diagnostic
method from ../call_forwards_deepgram_lesson.md.

Preserve the tested Plivo Stream attributes bidirectional="true" and
keepCallAlive="true". Distinguish Plivo Voice XML from hosted AI Studio settings.
Prove individual layers with minimal probes instead of repeatedly changing
Nginx when real call logs show no attempted stream. Verify actual provider
transcription with synthetic audio locally before recommending rollout;
report separately what was mocked, live-tested, and deployed. Never print keys.
