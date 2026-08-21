"""The turn loop and flag routing — the gateway's whole reason to exist.

chat_manager decides; the gateway executes. These tests stub the brain and
TTS entirely, so the full call flow is verified without a phone, an API key,
or a network.
"""
import json
from xml.dom.minidom import parseString

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

import app as gateway
import config
import security


@pytest.fixture
def client(monkeypatch, orders_log, tmp_path):
    """A TestClient with signature verification and TTS stubbed out."""
    monkeypatch.setattr(config, "AUDIO_DIR", str(tmp_path / "audio"))

    async def _no_verify(request: Request):
        return {k: v for k, v in (await request.form()).multi_items()}

    gateway.app.dependency_overrides[security.verify_plivo] = _no_verify
    monkeypatch.setattr(gateway, "synthesize", lambda text: b"FAKEMP3")
    yield TestClient(gateway.app)
    gateway.app.dependency_overrides.clear()


def stub_brain(monkeypatch, reply, capture=None):
    def _chat(user_id, session_id, message):
        if capture is not None:
            capture.append({"user_id": user_id, "session_id": session_id,
                            "message": message})
        return reply
    monkeypatch.setattr(gateway, "brain_chat", _chat)


BASE = {"answer": "Sure thing.", "session_id": "s1", "call_ended": False,
        "order_ready": False, "order": None, "order_type": None,
        "To_manager": False, "summary": "", "verbatim_user_chat": []}

ANSWER_FORM = {"CallUUID": "cu1", "From": "+15551234567"}
TURN_FORM = {**ANSWER_FORM, "Speech": "two samosas"}


def tags(xml):
    doc = parseString(xml)
    return [n.tagName for n in doc.documentElement.childNodes]


# ── greeting ──────────────────────────────────────────────────────────────

def test_answer_asks_the_brain_for_the_greeting(client, monkeypatch):
    """The greeting is the brain's — it greets returning callers by name."""
    seen = []
    stub_brain(monkeypatch, {**BASE, "answer": "Hi Priya, welcome back!"}, seen)
    r = client.post("/voice/answer", data=ANSWER_FORM)
    assert r.status_code == 200
    assert seen[0]["message"] == config.GREETING_PROMPT
    assert seen[0]["session_id"] is None  # a new call starts a new session
    assert seen[0]["user_id"] == "+15551234567"  # caller id IS the user id
    assert tags(r.text) == ["GetInput"]


def test_answer_binds_the_session_for_later_turns(client, monkeypatch):
    stub_brain(monkeypatch, BASE)
    client.post("/voice/answer", data=ANSWER_FORM)
    seen = []
    stub_brain(monkeypatch, BASE, seen)
    client.post("/voice/turn", data=TURN_FORM)
    assert seen[0]["session_id"] == "s1"  # continued, not restarted


# ── flag routing ──────────────────────────────────────────────────────────

def test_normal_reply_plays_and_listens_again(client, monkeypatch):
    stub_brain(monkeypatch, BASE)
    r = client.post("/voice/turn", data=TURN_FORM)
    assert tags(r.text) == ["GetInput"]
    assert "Hangup" not in r.text and "Dial" not in r.text


def test_call_ended_plays_then_hangs_up(client, monkeypatch):
    stub_brain(monkeypatch, {**BASE, "call_ended": True})
    r = client.post("/voice/turn", data=TURN_FORM)
    assert tags(r.text) == ["Play", "Hangup"]  # goodbye heard before drop


def test_transfer_to_manager_dials_the_manager(client, monkeypatch):
    stub_brain(monkeypatch, {**BASE, "Transfer_to_Manager": True})
    r = client.post("/voice/turn", data=TURN_FORM)
    doc = parseString(r.text)
    assert doc.getElementsByTagName("Number")[0].firstChild.data == (
        config.PLIVO_TRANSFER_NUMBER
    )
    assert not doc.getElementsByTagName("Hangup")  # call stays live


def test_to_manager_does_not_transfer_the_live_call(client, monkeypatch, orders_log):
    """To_manager is an async follow-up. Dialling here hangs up on a customer."""
    printed = []
    monkeypatch.setattr(
        gateway.print_client, "print_manager_request",
        lambda reply, **kwargs: printed.append((reply, kwargs)),
    )
    stub_brain(monkeypatch, {**BASE, "To_manager": True, "order_type": "cake",
                             "summary": "wants a cake"})
    r = client.post("/voice/turn", data=TURN_FORM)
    assert "Dial" not in r.text
    assert tags(r.text) == ["GetInput"]  # conversation continues
    events = [json.loads(l) for l in orders_log.read_text().splitlines()]
    assert [e["event"] for e in events] == ["manager_handoff"]
    assert events[0]["order_type"] == "cake"
    assert events[0]["summary"] == "wants a cake"
    assert printed[0][1]["call_uuid"] == "cu1"


def test_delivery_redirect_never_prints_manager_sheet(client, monkeypatch, orders_log):
    monkeypatch.setattr(
        gateway.print_client, "print_manager_request",
        lambda *args, **kwargs: pytest.fail("delivery must not print a manager sheet"),
    )
    stub_brain(monkeypatch, {
        **BASE, "call_ended": True, "order_type": "delivery",
        "summary": "Directed to website.",
    })
    client.post("/voice/turn", data=TURN_FORM)


def test_completed_delivery_redirect_is_emitted_with_summary(client, monkeypatch, orders_log):
    stub_brain(monkeypatch, {
        **BASE,
        "answer": "Please order delivery on our website.",
        "call_ended": True,
        "order_type": "delivery",
        "summary": "Customer requested delivery and was directed to the website.",
        "verbatim_user_chat": ["I need delivery."],
    })
    client.post("/voice/turn", data=TURN_FORM)
    events = [json.loads(line) for line in orders_log.read_text().splitlines()]
    assert events[0]["event"] == "delivery_redirect"
    assert events[0]["order_type"] == "delivery"
    assert events[0]["summary"] == "Customer requested delivery and was directed to the website."


def test_silence_reprompts_instead_of_dropping_the_call(client, monkeypatch):
    called = []
    stub_brain(monkeypatch, BASE, called)
    r = client.post("/voice/turn", data={**ANSWER_FORM, "Speech": "   "})
    assert tags(r.text) == ["GetInput"]
    assert not called  # nothing to ask the brain about


def test_unknown_flags_are_ignored_not_fatal(client, monkeypatch):
    """chat_manager passes new prompt-defined fields through automatically."""
    stub_brain(monkeypatch, {**BASE, "some_future_flag": True, "nested": {"a": 1}})
    r = client.post("/voice/turn", data=TURN_FORM)
    assert r.status_code == 200
    assert tags(r.text) == ["GetInput"]


# ── order emission ────────────────────────────────────────────────────────

ORDER = {"customer_name": "Priya", "items": [{"name": "Samosa"}], "total": "25.83"}


def test_order_emitted_once_with_the_object_not_the_prose(client, monkeypatch, orders_log):
    stub_brain(monkeypatch, {**BASE, "order_ready": True, "order_type": "pickup",
                             "summary": "Pickup order for Priya.", "order": ORDER})
    client.post("/voice/turn", data=TURN_FORM)
    events = [json.loads(l) for l in orders_log.read_text().splitlines()]
    assert len(events) == 1
    assert events[0]["order"] == ORDER  # never parsed out of `answer`
    assert events[0]["idempotency_key"] == "s1"
    assert events[0]["order_type"] == "pickup"
    assert events[0]["summary"] == "Pickup order for Priya."


def test_duplicate_turn_does_not_emit_twice(client, monkeypatch, orders_log):
    """Plivo can deliver a callback more than once."""
    stub_brain(monkeypatch, {**BASE, "order_ready": True, "order": ORDER})
    client.post("/voice/turn", data=TURN_FORM)
    client.post("/voice/turn", data=TURN_FORM)
    assert len(orders_log.read_text().splitlines()) == 1


def test_order_still_emitted_when_the_same_turn_ends_the_call(client, monkeypatch, orders_log):
    """The confirming turn both completes the order AND ends the call."""
    stub_brain(monkeypatch, {**BASE, "order_ready": True, "order": ORDER,
                             "call_ended": True})
    r = client.post("/voice/turn", data=TURN_FORM)
    assert tags(r.text) == ["Play", "Hangup"]
    assert len(orders_log.read_text().splitlines()) == 1


def test_order_survives_a_tts_outage(client, monkeypatch, orders_log):
    """A completed order must not be lost because ElevenLabs was down."""
    from speech.elevenlabs_tts import TTSUnavailable

    def _boom(text):
        raise TTSUnavailable("down")

    monkeypatch.setattr(gateway, "synthesize", _boom)
    stub_brain(monkeypatch, {**BASE, "order_ready": True, "order": ORDER})
    r = client.post("/voice/turn", data=TURN_FORM)
    assert r.status_code == 200
    assert "<Speak>" in r.text  # degraded to Plivo TTS, call continues
    assert len(orders_log.read_text().splitlines()) == 1


def test_nothing_emitted_when_pricing_did_not_run(client, monkeypatch, orders_log):
    """order_ready False, or order None, means no order exists."""
    stub_brain(monkeypatch, {**BASE, "order_ready": True, "order": None})
    client.post("/voice/turn", data=TURN_FORM)
    assert not orders_log.exists() or orders_log.read_text() == ""


# ── failure paths ─────────────────────────────────────────────────────────

def test_brain_down_transfers_to_a_human_never_a_dead_line(client, monkeypatch):
    from brain.client import BrainUnavailable

    def _down(**kw):
        raise BrainUnavailable("connection refused")

    monkeypatch.setattr(gateway, "brain_chat", lambda **kw: _down())
    r = client.post("/voice/turn", data=TURN_FORM)
    assert r.status_code == 200
    assert "<Dial" in r.text  # handed to staff, not dropped


def test_brain_down_on_answer_still_reaches_a_human(client, monkeypatch):
    from brain.client import BrainUnavailable

    def _down(**kw):
        raise BrainUnavailable("connection refused")

    monkeypatch.setattr(gateway, "brain_chat", lambda **kw: _down())
    r = client.post("/voice/answer", data=ANSWER_FORM)
    assert r.status_code == 200
    assert "<Dial" in r.text


def test_tts_outage_degrades_to_plivo_speak(client, monkeypatch):
    from speech.elevenlabs_tts import TTSUnavailable

    def _boom(text):
        raise TTSUnavailable("down")

    monkeypatch.setattr(gateway, "synthesize", _boom)
    stub_brain(monkeypatch, BASE)
    r = client.post("/voice/turn", data=TURN_FORM)
    assert "<Speak>" in r.text and "<Play>" not in r.text


def test_transfer_done_no_answer_speaks_a_fallback(client):
    r = client.post("/voice/transfer_done",
                    data={"CallUUID": "cu1", "DialStatus": "no-answer"})
    assert "<Speak>" in r.text and "<Hangup" in r.text


def test_transfer_done_completed_just_hangs_up(client):
    r = client.post("/voice/transfer_done",
                    data={"CallUUID": "cu1", "DialStatus": "completed"})
    assert tags(r.text) == ["Hangup"]


def test_hangup_is_idempotent(client, monkeypatch):
    """Plivo's own guidance: callbacks can be delivered more than once."""
    stub_brain(monkeypatch, BASE)
    client.post("/voice/answer", data=ANSWER_FORM)
    assert client.post("/voice/hangup", data=ANSWER_FORM).status_code == 200
    assert client.post("/voice/hangup", data=ANSWER_FORM).status_code == 200


def test_fallback_dials_the_restaurant(client):
    """Reached only if /voice/answer itself is unreachable."""
    r = client.post("/voice/fallback", data=ANSWER_FORM)
    assert "<Dial" in r.text
