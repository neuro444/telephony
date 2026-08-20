"""Call-cost emission.

Same pattern as orders/emitter.py: append-only JSONL, one writer, a
recent() reader for a dashboard to poll. Plivo's own /voice/hangup webhook
already delivers Duration (seconds) and HangupCause -- this is the only
place that data is persisted, everything else just discards it.

Sgopi is building LLM token-cost tracking inside chat_manager separately;
this covers the Plivo side of cost (call minutes). ElevenLabs TTS
character counts are a third, still-open cost driver -- not covered here.
"""
import json
import logging
import os
import threading
from datetime import datetime, timezone

import config

logger = logging.getLogger(__name__)
_lock = threading.Lock()


def _append(record: dict) -> None:
    """Append one JSON record to the log. The only writer."""
    os.makedirs(os.path.dirname(config.COST_LOG_PATH), exist_ok=True)
    with _lock:
        with open(config.COST_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def emit_call_duration(
    *, call_uuid: str, caller: str, duration_seconds: int | None, hangup_cause: str | None
) -> dict:
    """Append one call_ended cost event. Returns the record written.

    duration_seconds comes straight from Plivo's Duration hangup param --
    not computed locally, so it matches what Plivo actually bills for.
    """
    record = {
        "event": "call_ended",
        "emitted_at": datetime.now(timezone.utc).isoformat(),
        "call_uuid": call_uuid,
        "caller": caller,
        "duration_seconds": duration_seconds,
        "hangup_cause": hangup_cause,
    }
    _append(record)
    logger.info(
        "call cost emitted call_uuid=%s duration_seconds=%s cause=%s",
        call_uuid,
        duration_seconds,
        hangup_cause,
    )
    return record


def recent(limit: int = 50) -> list[dict]:
    """Return the most recent call-cost events, newest first."""
    if not os.path.exists(config.COST_LOG_PATH):
        return []
    with _lock:
        with open(config.COST_LOG_PATH, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    records = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return list(reversed(records))


def total_seconds(records: list[dict] | None = None) -> int:
    """Sum duration_seconds across all logged calls (or a given subset).
    Convenience for the dashboard -- avoids every consumer re-summing."""
    if records is None:
        records = recent(limit=100_000)
    return sum(r.get("duration_seconds") or 0 for r in records)
