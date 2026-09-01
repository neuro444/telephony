"""Tests for pii.mask_phone() — the shared phone-masking helper.

Covers:
- Normal E.164 masking (country-code prefix + last 4 kept)
- Short / empty / None edge cases
- MASK_PII_LOGS=False no-op path (simulates local dev / CI)
- Integration spot-check: cost_emitter and orders/emitter write masked values
  when masking is on, and raw values when it is off.
"""
import importlib
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


# ── pii.mask_phone() ──────────────────────────────────────────────────────────

def test_standard_e164_number_is_masked():
    import pii
    with patch.object(pii.config, "MASK_PII_LOGS", True):
        assert pii.mask_phone("+14042071333") == "+1404***1333"


def test_international_number_is_masked():
    import pii
    with patch.object(pii.config, "MASK_PII_LOGS", True):
        assert pii.mask_phone("+919876543210") == "+9198***3210"


def test_last_four_digits_are_preserved():
    import pii
    with patch.object(pii.config, "MASK_PII_LOGS", True):
        result = pii.mask_phone("+14042071333")
        assert result.endswith("1333")


def test_first_five_chars_are_preserved():
    import pii
    with patch.object(pii.config, "MASK_PII_LOGS", True):
        result = pii.mask_phone("+14042071333")
        assert result.startswith("+1404")


def test_short_number_returns_sentinel():
    import pii
    with patch.object(pii.config, "MASK_PII_LOGS", True):
        assert pii.mask_phone("+1") == "***"


def test_empty_string_returns_sentinel():
    import pii
    with patch.object(pii.config, "MASK_PII_LOGS", True):
        assert pii.mask_phone("") == "***"


def test_none_is_treated_as_empty():
    """mask_phone must not raise on None — callers pass empty strings in tests."""
    import pii
    with patch.object(pii.config, "MASK_PII_LOGS", True):
        # None is falsy, so the short-circuit returns "***"
        assert pii.mask_phone(None) == "***"  # type: ignore[arg-type]


def test_mask_disabled_returns_original():
    """When MASK_PII_LOGS is False, mask_phone is a pass-through."""
    import pii
    with patch.object(pii.config, "MASK_PII_LOGS", False):
        assert pii.mask_phone("+14042071333") == "+14042071333"


def test_mask_disabled_returns_original_for_short_number():
    import pii
    with patch.object(pii.config, "MASK_PII_LOGS", False):
        assert pii.mask_phone("+1") == "+1"


# ── cost_emitter integration ──────────────────────────────────────────────────

def _read_last_record(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        lines = [l.strip() for l in fh if l.strip()]
    return json.loads(lines[-1])


def test_cost_emitter_masks_caller_when_enabled(tmp_path):
    """emit_call_duration writes a masked caller to disk when MASK_PII_LOGS=True."""
    import pii
    import cost.cost_emitter as ce
    log = str(tmp_path / "costs.jsonl")
    with patch.object(ce.config, "COST_LOG_PATH", log), \
         patch.object(pii.config, "MASK_PII_LOGS", True):
        ce.emit_call_duration(
            call_uuid="uuid-1", caller="+14042071333",
            duration_seconds=42, hangup_cause="NORMAL_CLEARING",
        )
    record = _read_last_record(log)
    assert record["caller"] == "+1404***1333"
    assert "2071" not in record["caller"]


def test_cost_emitter_exposes_caller_when_disabled(tmp_path):
    """emit_call_duration writes the raw caller when MASK_PII_LOGS=False."""
    import pii
    import cost.cost_emitter as ce
    log = str(tmp_path / "costs.jsonl")
    with patch.object(ce.config, "COST_LOG_PATH", log), \
         patch.object(pii.config, "MASK_PII_LOGS", False):
        ce.emit_call_duration(
            call_uuid="uuid-2", caller="+14042071333",
            duration_seconds=10, hangup_cause="USER_BUSY",
        )
    record = _read_last_record(log)
    assert record["caller"] == "+14042071333"


# ── orders/emitter integration ────────────────────────────────────────────────

_SAMPLE_REPLY = {
    "session_id": "sess-abc",
    "order_type": "pickup",
    "answer": "Your order is ready.",
    "summary": "2 samosas",
    "verbatim_user_chat": ["two samosas please"],
    "order": {"items": [], "total": 12.50},
    "order_ready": True,
}

_HANDOFF_REPLY = {
    "session_id": "sess-xyz",
    "order_type": "cake",
    "answer": "A manager will call you back.",
    "summary": "Custom birthday cake",
    "verbatim_user_chat": ["I want a cake"],
    "To_manager": True,
}


def test_order_emitter_masks_user_id_when_enabled(tmp_path):
    import pii
    import orders.emitter as oe
    log = str(tmp_path / "orders.jsonl")
    with patch.object(oe.config, "ORDERS_LOG_PATH", log), \
         patch.object(pii.config, "MASK_PII_LOGS", True):
        oe.emit(_SAMPLE_REPLY, call_uuid="uuid-3", user_id="+14042071333")
    record = _read_last_record(log)
    assert record["user_id"] == "+1404***1333"


def test_order_emitter_exposes_user_id_when_disabled(tmp_path):
    import pii
    import orders.emitter as oe
    log = str(tmp_path / "orders.jsonl")
    with patch.object(oe.config, "ORDERS_LOG_PATH", log), \
         patch.object(pii.config, "MASK_PII_LOGS", False):
        oe.emit(_SAMPLE_REPLY, call_uuid="uuid-4", user_id="+14042071333")
    record = _read_last_record(log)
    assert record["user_id"] == "+14042071333"


def test_handoff_emitter_masks_user_id_when_enabled(tmp_path):
    import pii
    import orders.emitter as oe
    log = str(tmp_path / "orders.jsonl")
    with patch.object(oe.config, "ORDERS_LOG_PATH", log), \
         patch.object(pii.config, "MASK_PII_LOGS", True):
        oe.emit_handoff(_HANDOFF_REPLY, call_uuid="uuid-5", user_id="+14042071333")
    record = _read_last_record(log)
    assert record["user_id"] == "+1404***1333"


def test_handoff_emitter_exposes_user_id_when_disabled(tmp_path):
    import pii
    import orders.emitter as oe
    log = str(tmp_path / "orders.jsonl")
    with patch.object(oe.config, "ORDERS_LOG_PATH", log), \
         patch.object(pii.config, "MASK_PII_LOGS", False):
        oe.emit_handoff(_HANDOFF_REPLY, call_uuid="uuid-6", user_id="+14042071333")
    record = _read_last_record(log)
    assert record["user_id"] == "+14042071333"
