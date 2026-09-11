"""Speech service protocols.

Phone calls select Plivo GetInput or Deepgram live streaming through
STT_PROVIDER. The streaming adapter lives in speech/deepgram_stt.py;
SpeechToText below describes a separate batch transcription interface.
"""
from typing import Protocol


class TextToSpeech(Protocol):
    def synthesize(self, text: str) -> bytes:
        """Return audio bytes (mp3) for the given text."""
        ...


class SpeechToText(Protocol):
    def transcribe(self, audio: bytes) -> str:
        """Return the transcript for the given audio bytes. Reserved for batch adapters."""
        ...
