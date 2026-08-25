"""Call-cost emission.

Same pattern as orders/emitter.py: append-only JSONL, one writer, a
recent() reader for a dashboard to poll. Plivo's own /voice/hangup webhook
already delivers Duration (seconds) and HangupCause -- this is the only
place that data is persisted, everything else just discards it.

Two record types share this same file/reader (GET /cost/calls already
returns every record with no type filter, so no endpoint change needed):
  - "call_ended": Plivo's call duration, from /voice/hangup.
  - "llm_turn": chat_manager's /chat response data (tokens, tts_chars) for
    one turn -- previously nothing forwarded this anywhere at all. See
    docs/chat_manager_telephony_cost_integration.md (cost-monitoring repo)
    for the full spec this was built against.
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


def emit_llm_turn(
    *,
    call_uuid: str,
    turn_seq: int,
    model: str,
    input_tokens: int,
    output_tokens: int,
    tts_chars: int,
    latency_ms: float | None = None,
) -> dict:
    """Append one llm_turn cost event -- one per /chat response, so a call
    with N turns produces N of these plus the one call_ended record from
    /voice/hangup. Fields pulled straight from chat_manager's /chat
    response (model_used, input_tokens, output_tokens, tts_chars,
    latency_ms) -- nothing computed here, this only reports what
    chat_manager already told us.

    turn_seq makes each call's records orderable/unique (1, 2, 3...) --
    caller keeps its own per-call counter, see calls/state.py.
    """
    record = {
        "event": "llm_turn",
        "emitted_at": datetime.now(timezone.utc).isoformat(),
        "call_uuid": call_uuid,
        "turn_seq": turn_seq,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "tts_chars": tts_chars,
        "latency_ms": latency_ms,
    }
    _append(record)
    logger.info(
        "llm turn cost emitted call_uuid=%s turn_seq=%s model=%s input_tokens=%s "
        "output_tokens=%s tts_chars=%s",
        call_uuid,
        turn_seq,
        model,
        input_tokens,
        output_tokens,
        tts_chars,
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
