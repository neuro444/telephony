"""Order emission.

Per the plan's decision (3): produce the artifact, do not deliver it. A
future central repo consumes this log (or the emitter gains an HTTP sink
later — one file changes, callers of emit() do not).

Rules, taken directly from chat_manager's contract:
- Emit only when order_ready is True and order is not None.
- Never parse order details out of `answer` — the text is for the human
  ear, the object is the data.
- idempotency_key = session_id, so a retried turn can be deduped.
- Emit once per session — callers.py enforces the "once" part via
  calls.state.mark_order_emitted() before calling this.
"""
import json
import logging
import os
import threading
from datetime import datetime, timezone

import config

logger = logging.getLogger(__name__)
_lock = threading.Lock()


def emit(reply: dict, *, call_uuid: str, user_id: str) -> dict:
    """Append one order_ready event to the JSONL log. Returns the record written."""
    order = reply.get("order")
    session_id = reply.get("session_id")
    record = {
        "event": "order_ready",
        "emitted_at": datetime.now(timezone.utc).isoformat(),
        "idempotency_key": session_id,
        "call_uuid": call_uuid,
        "user_id": user_id,
        "session_id": session_id,
        "order": order,
    }
    os.makedirs(os.path.dirname(config.ORDERS_LOG_PATH), exist_ok=True)
    with _lock:
        with open(config.ORDERS_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    logger.info(
        "order emitted call_uuid=%s session_id=%s total=%s",
        call_uuid,
        session_id,
        (order or {}).get("total"),
    )
    return record


def recent(limit: int = 50) -> list[dict]:
    """Return the most recent emitted orders, newest first."""
    if not os.path.exists(config.ORDERS_LOG_PATH):
        return []
    with _lock:
        with open(config.ORDERS_LOG_PATH, "r", encoding="utf-8") as fh:
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
