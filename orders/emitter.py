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
def _append(record: dict) -> None:
    """Append one JSON record to the log. The only writer."""
    os.makedirs(os.path.dirname(config.ORDERS_LOG_PATH), exist_ok=True)
    with _lock:
        with open(config.ORDERS_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
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
        "order_type": reply.get("order_type") or "pickup",
        "answer": reply.get("answer", ""),
        "summary": reply.get("summary", ""),
        "verbatim_user_chat": reply.get("verbatim_user_chat", []),
        "order": order,
    }
    _append(record)
    logger.info(
        "order emitted call_uuid=%s session_id=%s total=%s",
        call_uuid,
        session_id,
        (order or {}).get("total"),
    )
    return record
def emit_handoff(reply: dict, *, call_uuid: str, user_id: str) -> dict:
    """Append one manager_handoff event — the To_manager flag.
    This is the ASYNC cake/catering follow-up, not a live transfer. Nothing
    happens to the call; staff pick the lead up from this log later. Without
    it the flag is silently dropped and the lead is lost.
    """
    session_id = reply.get("session_id")
    order_type = reply.get("order_type")
    record = {
        "event": "delivery_redirect" if order_type == "delivery" else "manager_handoff",
        "emitted_at": datetime.now(timezone.utc).isoformat(),
        "idempotency_key": session_id,
        "call_uuid": call_uuid,
        "user_id": user_id,
        "session_id": session_id,
        "order_type": order_type,
        "answer": reply.get("answer", ""),
        "summary": reply.get("summary", ""),
        "verbatim_user_chat": reply.get("verbatim_user_chat", []),
    }
    _append(record)
    logger.info(
        "manager handoff emitted call_uuid=%s session_id=%s", call_uuid, session_id
    )
    return record
def _read_all(limit: int) -> list[dict]:
    """Read the raw log, newest first. Internal — callers should use recent()
    or recent_handoffs(), which filter by event type."""
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
def recent(limit: int = 50) -> list[dict]:
    """Return the most recent order_ready events, newest first.
    Filtered to order_ready only -- the log is shared with manager_handoff
    and delivery_redirect events from emit_handoff(), which would otherwise
    leak into /orders/recent unfiltered. Existing callers of this function
    keep the exact behavior they've always had: orders only.
    """
    return [r for r in _read_all(limit * 4) if r.get("event") == "order_ready"][:limit]
def recent_handoffs(limit: int = 50) -> list[dict]:
    """Return the most recent manager_handoff and delivery_redirect events,
    newest first. The read-side counterpart to emit_handoff() -- until now
    there was no way to see these events back out again."""
    return [
        r for r in _read_all(limit * 4)
        if r.get("event") in ("manager_handoff", "delivery_redirect")
    ][:limit]
