"""Normalize Twilio metadata to the established STT adapter input contract."""
class TwilioStream:
    def __init__(self, socket):
        self.socket = socket

    async def receive_json(self):
        event = await self.socket.receive_json()
        if event.get("event") == "start":
            start = event["start"]
            if start.get("mediaFormat", {}).get("channels") != 1:
                raise ValueError("Expected mono Twilio audio")
            return {**event, "start": {**start, "callId": start.get("callSid")}}
        # connected is ignored by adapters; media.payload is already base64 mu-law.
        return event
