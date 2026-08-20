"""Shared fixtures.

Config is read at import time from the environment, so every value the XML
builders interpolate must be set before `config` is first imported.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("PLIVO_PUBLIC_BASE_URL", "https://voice.test")
os.environ.setdefault("PLIVO_PHONE_NUMBER", "+14042071333")
os.environ.setdefault("PLIVO_TRANSFER_NUMBER", "+16468753366")
os.environ.setdefault("PLIVO_AUTH_TOKEN", "test-auth-token")
os.environ.setdefault("SPEECH_HINTS", "Samosa, Gobi Manchurian")

import pytest  # noqa: E402


@pytest.fixture
def orders_log(tmp_path, monkeypatch):
    """Point the emitter at a throwaway JSONL and return its path."""
    import config

    path = tmp_path / "orders.jsonl"
    monkeypatch.setattr(config, "ORDERS_LOG_PATH", str(path))
    return path


@pytest.fixture(autouse=True)
def clean_call_registry():
    """Call state is a module-level singleton — reset it between tests."""
    from calls import state

    state.registry._calls.clear()
    yield
    state.registry._calls.clear()
