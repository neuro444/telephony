"""Tests for the telephony JSONL log purge script.

All tests operate on in-memory temp files — no permanent disk changes.
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import scripts.purge_old_logs as purge_module  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _write_jsonl(path: str, records: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")


def _read_jsonl(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _ts(days_ago: int) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return dt.isoformat()


def _order_record(days_ago: int, event: str = "order_ready") -> dict:
    return {
        "event": event,
        "emitted_at": _ts(days_ago),
        "call_uuid": "uuid-123",
        "user_id": "+1404***1333",
        "session_id": "sess-abc",
        "order": {"items": ["Samosa"]},
    }


def _cost_record(days_ago: int) -> dict:
    return {
        "event": "call_ended",
        "emitted_at": _ts(days_ago),
        "call_uuid": "uuid-456",
        "caller": "+1404***1333",
        "duration_seconds": 120,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestPurgeJsonlFile:
    def test_old_lines_removed(self, tmp_path):
        """Lines with emitted_at older than cutoff must be purged."""
        log_file = str(tmp_path / "orders.jsonl")
        _write_jsonl(log_file, [
            _order_record(days_ago=35),
            _order_record(days_ago=5),
        ])

        purge_module._purge_file(log_file, cutoff=datetime.now(timezone.utc) - timedelta(days=30), dry_run=False)

        remaining = _read_jsonl(log_file)
        assert len(remaining) == 1
        assert remaining[0]["event"] == "order_ready"

    def test_recent_lines_kept(self, tmp_path):
        """Lines within the retention window must survive."""
        log_file = str(tmp_path / "costs.jsonl")
        _write_jsonl(log_file, [
            _cost_record(days_ago=10),
            _cost_record(days_ago=15),
        ])

        purge_module._purge_file(log_file, cutoff=datetime.now(timezone.utc) - timedelta(days=30), dry_run=False)

        remaining = _read_jsonl(log_file)
        assert len(remaining) == 2

    def test_dry_run_leaves_file_unchanged(self, tmp_path):
        """--dry-run must not modify the file even when records would be purged."""
        log_file = str(tmp_path / "orders.jsonl")
        records = [_order_record(days_ago=60), _order_record(days_ago=5)]
        _write_jsonl(log_file, records)
        original_content = open(log_file, "rb").read()

        purge_module._purge_file(log_file, cutoff=datetime.now(timezone.utc) - timedelta(days=30), dry_run=True)

        assert open(log_file, "rb").read() == original_content

    def test_missing_file_is_handled_gracefully(self, tmp_path):
        """A missing JSONL file must not raise an exception."""
        missing = str(tmp_path / "nonexistent.jsonl")
        total, kept = purge_module._purge_file(
            missing, cutoff=datetime.now(timezone.utc) - timedelta(days=30), dry_run=False
        )
        assert total == 0
        assert kept == 0

    def test_malformed_lines_are_kept(self, tmp_path):
        """Lines with missing or unparseable emitted_at must be preserved."""
        log_file = str(tmp_path / "orders.jsonl")
        with open(log_file, "w", encoding="utf-8") as fh:
            fh.write('{"event": "order_ready"}\n')   # missing emitted_at
            fh.write('not valid json\n')              # completely broken

        total, kept = purge_module._purge_file(
            log_file, cutoff=datetime.now(timezone.utc) - timedelta(days=30), dry_run=False
        )
        assert kept == 2   # both kept; neither purged

    def test_empty_file_is_handled(self, tmp_path):
        """An empty JSONL file must not raise and must stay empty."""
        log_file = str(tmp_path / "empty.jsonl")
        open(log_file, "w").close()

        total, kept = purge_module._purge_file(
            log_file, cutoff=datetime.now(timezone.utc) - timedelta(days=30), dry_run=False
        )
        assert total == 0
        assert kept == 0

    def test_atomic_write_uses_temp_then_rename(self, tmp_path, monkeypatch):
        """Verify that os.replace is called (atomic rename), not a direct overwrite."""
        log_file = str(tmp_path / "orders.jsonl")
        _write_jsonl(log_file, [_order_record(days_ago=60)])

        replaced = []
        original_replace = os.replace

        def mock_replace(src, dst):
            replaced.append((src, dst))
            original_replace(src, dst)

        monkeypatch.setattr(os, "replace", mock_replace)

        purge_module._purge_file(log_file, cutoff=datetime.now(timezone.utc) - timedelta(days=30), dry_run=False)

        assert len(replaced) == 1
        src, dst = replaced[0]
        assert src.endswith(".tmp")
        assert dst == log_file

    def test_naive_emitted_at_treated_as_utc(self, tmp_path):
        """Timezone-naive emitted_at strings are assumed UTC (old data compatibility)."""
        log_file = str(tmp_path / "costs.jsonl")
        naive_old = (datetime.utcnow() - timedelta(days=40)).isoformat()  # no tz suffix
        naive_new = (datetime.utcnow() - timedelta(days=5)).isoformat()
        _write_jsonl(log_file, [
            {"event": "call_ended", "emitted_at": naive_old, "call_uuid": "a"},
            {"event": "call_ended", "emitted_at": naive_new, "call_uuid": "b"},
        ])

        purge_module._purge_file(log_file, cutoff=datetime.now(timezone.utc) - timedelta(days=30), dry_run=False)

        remaining = _read_jsonl(log_file)
        assert len(remaining) == 1
        assert remaining[0]["call_uuid"] == "b"
