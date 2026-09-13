"""Bounded copy of inbound media; never a second WebSocket consumer."""
import base64
import time
import uuid
import config


class CaptureStream:
    def __init__(self, socket, state):
        self.socket = socket
        self.enabled = config.DEBUG_CUSTOMER_AUDIO and (
            not config.DEBUG_AUDIO_CALLERS or state.caller_number in config.DEBUG_AUDIO_CALLERS
        )
        self.data = bytearray()
        self.info = {"id": uuid.uuid4().hex, "carrier": state.telephony_provider,
                     "provider": state.stt_provider, "hints": config.SPEECH_HINTS,
                     "call_id": state.call_uuid, "codec": "audio/x-mulaw",
                     "sample_rate": 8000, "complete": True, "frames": 0}
        model_key = {"assemblyai": "ASSEMBLY_MODEL", "deepgram": "DEEPGRAM_MODEL",
                     "sarvam": "SARVAM_MODEL"}.get(state.stt_provider)
        self.info["model"] = getattr(config, model_key, "scribe_v2_realtime") if model_key else "scribe_v2_realtime"
        self.started = time.monotonic()
        self.frame_info = []
        self.expected_timestamp = None

    async def receive_json(self):
        event = await self.socket.receive_json()
        if self.enabled:
            try:
                if event.get("event") == "start":
                    fmt = event["start"].get("mediaFormat", {})
                    self.info["codec"] = fmt.get("encoding", "")
                    self.info["sample_rate"] = int(fmt.get("sampleRate", 0))
                    if self.info["codec"] not in {"audio/x-mulaw", "mulaw"} or self.info["sample_rate"] != 8000:
                        self.enabled = False
                elif event.get("event") == "media":
                    media = event.get("media", {})
                    if media.get("track", "inbound") not in {"inbound", "inbound_track"}:
                        return event
                    chunk = base64.b64decode(media["payload"], validate=True)
                    if len(self.data) + len(chunk) <= 480000 and len(self.frame_info) < 3000:
                        stamp = media.get("timestamp")
                        if stamp is not None:
                            stamp = float(stamp)
                            if self.expected_timestamp is not None and abs(stamp - self.expected_timestamp) > 2:
                                self.info["complete"] = False
                            self.expected_timestamp = stamp + len(chunk) / 8
                        self.frame_info.append({"offset": len(self.data), "size": len(chunk),
                                                "timestamp": stamp, "sequence": event.get("sequenceNumber")})
                        self.data.extend(chunk)
                        self.info["frames"] += 1
                    else:
                        self.info["complete"] = False
            except (ValueError, KeyError, TypeError):
                self.info["complete"] = False
        return event

    def result(self, failed=False):
        if not self.enabled or not self.data:
            return None
        return {**self.info, "complete": self.info["complete"] and not failed,
                "elapsed_ms": round((time.monotonic() - self.started) * 1000),
                "frame_info": self.frame_info,
                "mulaw_base64": base64.b64encode(self.data).decode("ascii")}
