"""XML builders. A malformed response drops the call with no useful error,
so every builder is checked as real, parseable XML — not by string matching.
"""
from xml.dom.minidom import parseString

import pytest

import config
import plivo_xml


def parse(xml: str):
    """Assert well-formedness and return the document."""
    return parseString(xml)


BUILDERS = [
    ("speak_and_continue", lambda: plivo_xml.speak_and_continue("hello")),
    ("speak_and_hangup", lambda: plivo_xml.speak_and_hangup("bye")),
    ("speak_and_transfer", lambda: plivo_xml.speak_and_transfer("hold", "+15551234567")),
    ("play_and_continue", lambda: plivo_xml.play_and_continue("https://a/b.mp3")),
    ("play_and_hangup", lambda: plivo_xml.play_and_hangup("https://a/b.mp3")),
    ("play_and_transfer", lambda: plivo_xml.play_and_transfer("https://a/b.mp3", "+15551234567")),
]


@pytest.mark.parametrize("name,build", BUILDERS)
def test_builder_emits_wellformed_xml(name, build):
    doc = parse(build())
    assert doc.documentElement.tagName == "Response"


@pytest.mark.parametrize("name,build", BUILDERS)
def test_builder_survives_ampersand_and_quotes(name, build, monkeypatch):
    """A caller named 'Bob & Sons' or a menu item with " must not break XML."""
    monkeypatch.setattr(config, "SPEECH_HINTS", 'Gobi "Special" & Sons')
    monkeypatch.setattr(config, "PLIVO_PHONE_NUMBER", '+1"404"')
    parse(build())


def test_speak_text_is_escaped_not_injected():
    """Caller-influenced text must never become markup."""
    xml = plivo_xml.speak_and_hangup("</Speak><Hangup/><Speak>pwned")
    doc = parse(xml)
    speaks = doc.getElementsByTagName("Speak")
    assert len(speaks) == 1
    assert len(doc.getElementsByTagName("Hangup")) == 1


def test_get_input_targets_the_turn_endpoint():
    doc = parse(plivo_xml.speak_and_continue("hi"))
    gi = doc.getElementsByTagName("GetInput")[0]
    assert gi.getAttribute("action") == f"{config.PLIVO_PUBLIC_BASE_URL}/voice/turn"
    assert gi.getAttribute("inputType") == "speech"
    # phone_call is the model tuned for 8kHz phone audio.
    assert gi.getAttribute("speechModel") == "phone_call"


def test_base_url_trailing_slash_does_not_double_up():
    """A stray trailing slash would make the signed URL not match Plivo's."""
    import importlib

    original = config.PLIVO_PUBLIC_BASE_URL
    try:
        config.PLIVO_PUBLIC_BASE_URL = "https://voice.test/"
        importlib.reload(plivo_xml)
        doc = parse(plivo_xml.speak_and_continue("hi"))
        action = doc.getElementsByTagName("GetInput")[0].getAttribute("action")
        assert action == "https://voice.test/voice/turn"
    finally:
        config.PLIVO_PUBLIC_BASE_URL = original
        importlib.reload(plivo_xml)


def test_hints_are_passed_to_plivo():
    doc = parse(plivo_xml.speak_and_continue("hi"))
    assert "Samosa" in doc.getElementsByTagName("GetInput")[0].getAttribute("hints")


def test_transfer_dial_has_action_and_callerid():
    """Without action=, a manager who doesn't answer leaves the caller in silence."""
    doc = parse(plivo_xml.play_and_transfer("https://a/b.mp3", "+15551234567"))
    dial = doc.getElementsByTagName("Dial")[0]
    assert dial.getAttribute("action") == f"{config.PLIVO_PUBLIC_BASE_URL}/voice/transfer_done"
    assert dial.getAttribute("callerId") == config.PLIVO_PHONE_NUMBER
    assert dial.getAttribute("dialMusic") == "real"
    assert doc.getElementsByTagName("Number")[0].firstChild.data == "+15551234567"


def test_hangup_comes_after_play_so_goodbye_is_heard():
    """<Hangup/> before <Play> would cut the caller off mid-sentence."""
    doc = parse(plivo_xml.play_and_hangup("https://a/b.mp3"))
    kids = [n.tagName for n in doc.documentElement.childNodes]
    assert kids == ["Play", "Hangup"]


def test_continue_does_not_hang_up():
    doc = parse(plivo_xml.play_and_continue("https://a/b.mp3"))
    assert not doc.getElementsByTagName("Hangup")
    assert doc.getElementsByTagName("GetInput")
