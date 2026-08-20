"""Speech service protocols.

v1 only uses synthesize() — Plivo's <GetInput> does ASR natively, so no
transcribe() implementation is wired into the turn loop. The Protocol is
kept so a self-hosted or managed STT (Deepgram, Whisper) can be swapped in
later — e.g. if SpeechConfidenceScore on real calls shows Plivo's ASR
mishearing menu items like "gulab jamun" too often — without touching
app.py's routing logic.
"""
from typing import Protocol


class TextToSpeech(Protocol):
    def synthesize(self, text: str) -> bytes:
        """Return audio bytes (mp3) for the given text."""
        ...


class SpeechToText(Protocol):
    def transcribe(self, audio: bytes) -> str:
        """Return the transcript for the given audio bytes. Not used in v1."""
        ...
