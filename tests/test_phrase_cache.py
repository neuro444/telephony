"""Unit tests for the persistent phrase cache.

All tests use a tmp_path so nothing is ever written to /app/phrase_cache or any
real directory. No ElevenLabs or Plivo credentials required.
"""
import hashlib
import os

import pytest

import config
import phrase_cache as pc


@pytest.fixture(autouse=True)
def phrase_cache_dir(tmp_path, monkeypatch):
    """Point phrase_cache at a throwaway directory for every test."""
    monkeypatch.setattr(config, "PHRASE_CACHE_DIR", str(tmp_path / "phrase_cache"))
    monkeypatch.setattr(config, "ELEVENLABS_VOICE_ID", "test-voice-id")
    monkeypatch.setattr(config, "ELEVENLABS_MODEL_ID", "test-model-id")
    monkeypatch.setattr(config, "PLIVO_PUBLIC_BASE_URL", "https://voice.test")


# ── get() ─────────────────────────────────────────────────────────────────────

def test_get_returns_none_on_miss():
    """Empty cache: nothing stored yet → None."""
    assert pc.get("Hello, how can I help you?") is None


def test_get_returns_url_after_put():
    """After storing, get() should return a public URL."""
    url = pc.put("Hello, how can I help you?", b"FAKEMP3")
    assert pc.get("Hello, how can I help you?") == url


def test_get_is_idempotent():
    """Two get() calls on the same text return the same URL."""
    pc.put("Same text.", b"FAKEMP3")
    assert pc.get("Same text.") == pc.get("Same text.")


# ── put() ─────────────────────────────────────────────────────────────────────

def test_put_writes_file_to_phrase_cache_dir():
    """put() must create PHRASE_CACHE_DIR and write the mp3 file there."""
    pc.put("Stored phrase.", b"AUDIODATA")
    files = os.listdir(config.PHRASE_CACHE_DIR)
    assert len(files) == 1
    assert files[0].endswith(".mp3")


def test_put_returns_public_url():
    url = pc.put("Testing URL.", b"FAKEMP3")
    assert url.startswith("https://voice.test/phrase/")
    assert url.endswith(".mp3")


def test_put_same_phrase_twice_is_idempotent():
    """Putting the same phrase twice should not create two separate files."""
    pc.put("Duplicate.", b"FAKEMP3")
    pc.put("Duplicate.", b"FAKEMP3")
    assert len(os.listdir(config.PHRASE_CACHE_DIR)) == 1


# ── read() ────────────────────────────────────────────────────────────────────

def test_read_returns_stored_bytes():
    """read() by hash key must return exactly the bytes that were stored."""
    audio = b"REAL_AUDIO_BYTES"
    url = pc.put("Read me back.", audio)
    key = url.split("/phrase/")[1].replace(".mp3", "")
    assert pc.read(key) == audio


def test_read_returns_none_for_unknown_key():
    assert pc.read("0" * 64) is None


# ── cache key behaviour ───────────────────────────────────────────────────────

def test_cache_key_differs_by_voice_id(monkeypatch):
    """Different voice IDs must produce different cache keys."""
    pc.put("Hello.", b"AUDIO_V1")
    url_v1 = pc.get("Hello.")

    monkeypatch.setattr(config, "ELEVENLABS_VOICE_ID", "different-voice-id")
    url_v2 = pc.get("Hello.")

    assert url_v2 is None  # cache miss after voice change
    assert url_v1 != url_v2


def test_cache_key_differs_by_model_id(monkeypatch):
    """Different model IDs must produce different cache keys."""
    pc.put("Hello.", b"AUDIO_M1")
    url_m1 = pc.get("Hello.")

    monkeypatch.setattr(config, "ELEVENLABS_MODEL_ID", "different-model-id")
    url_m2 = pc.get("Hello.")

    assert url_m2 is None  # cache miss after model change
    assert url_m1 != url_m2


def test_normalize_text_whitespace_matches():
    """Leading/trailing whitespace should not create separate cache entries."""
    pc.put("  Hello.  ", b"AUDIO")
    assert pc.get("Hello.") is not None
    assert pc.get("  Hello.  ") == pc.get("Hello.")


def test_normalize_text_case_matches():
    """Case differences should not create separate cache entries."""
    pc.put("HELLO.", b"AUDIO")
    assert pc.get("hello.") is not None
    assert pc.get("HELLO.") == pc.get("hello.")


def test_two_different_phrases_produce_different_keys():
    """Distinct phrases must have distinct keys — no accidental collisions."""
    pc.put("Phrase one.", b"A1")
    pc.put("Phrase two.", b"A2")
    assert len(os.listdir(config.PHRASE_CACHE_DIR)) == 2
    url1 = pc.get("Phrase one.")
    url2 = pc.get("Phrase two.")
    assert url1 != url2
