"""phrase_cache.py -- the persistent, cross-call cache for fixed TTS phrases."""
import config
import phrase_cache

KW = dict(voice_id="v1", model_id="m1", voice_settings={"stability": 0.5})


def test_same_inputs_produce_the_same_id():
    a = phrase_cache.audio_id_for("hello there", **KW)
    b = phrase_cache.audio_id_for("hello there", **KW)
    assert a == b


def test_different_text_produces_a_different_id():
    a = phrase_cache.audio_id_for("hello there", **KW)
    b = phrase_cache.audio_id_for("goodbye now", **KW)
    assert a != b


def test_different_voice_settings_produce_a_different_id():
    """voice_settings shapes the audio too -- a stale clip must never be
    served just because the text and voice happened to match."""
    a = phrase_cache.audio_id_for("hello there", **KW)
    b = phrase_cache.audio_id_for(
        "hello there", voice_id="v1", model_id="m1",
        voice_settings={"stability": 0.9},
    )
    assert a != b


def test_id_is_prefixed_for_purge_expired_to_recognize():
    audio_id = phrase_cache.audio_id_for("hello there", **KW)
    assert audio_id.startswith(phrase_cache.PREFIX)


def test_put_then_exists_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "AUDIO_DIR", str(tmp_path / "audio"))
    audio_id = phrase_cache.audio_id_for("hello there", **KW)
    assert not phrase_cache.exists(audio_id)
    phrase_cache.put(audio_id, b"FAKEMP3")
    assert phrase_cache.exists(audio_id)
