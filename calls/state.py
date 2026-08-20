"""In-memory call state.

The gateway holds NO conversation state — session_id is kept only long
enough to route the next turn back to the right chat_manager session.
Keyed on Plivo's CallUUID, which is stable for the life of one call.

Not persisted: a gateway restart mid-call loses the mapping, and the next
turn from that call would start a fresh chat_manager session. Acceptable
for v1 — a restart mid-call is already a degraded experience regardless.
"""
import threading
import time
from dataclasses import dataclass, field

_TTL_SECONDS = 60 * 60 * 2  # 2h ceiling matches the max-call-duration guard


@dataclass
class CallState:
    call_uuid: str
    caller_number: str
    session_id: str | None = None
    created_at: float = field(default_factory=time.monotonic)
    last_seen: float = field(default_factory=time.monotonic)
    finalized: bool = False
    duration: int | None = None
    hangup_cause: str | None = None
    # Idempotency guards — each action fires at most once per call.
    order_emitted_for_session: str | None = None
    manager_handoff_emitted_for_session: str | None = None


class _CallRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._calls: dict[str, CallState] = {}

    def start(self, call_uuid: str, caller_number: str) -> CallState:
        with self._lock:
            self._evict_expired()
            state = self._calls.get(call_uuid)
            if state is None:
                state = CallState(call_uuid=call_uuid, caller_number=caller_number)
                self._calls[call_uuid] = state
            state.last_seen = time.monotonic()
            return state

    def get(self, call_uuid: str) -> CallState:
        with self._lock:
            state = self._calls.get(call_uuid)
            if state is None:
                # Turn arrived before/without an answer callback (shouldn't
                # normally happen, but never crash the call over it).
                state = CallState(call_uuid=call_uuid, caller_number="")
                self._calls[call_uuid] = state
            state.last_seen = time.monotonic()
            return state

    def bind_session(self, call_uuid: str, session_id: str) -> None:
        with self._lock:
            state = self._calls.get(call_uuid)
            if state is not None:
                state.session_id = session_id

    def already_finalized(self, call_uuid: str) -> bool:
        with self._lock:
            state = self._calls.get(call_uuid)
            return bool(state and state.finalized)

    def finalize(self, call_uuid: str, duration=None, cause=None) -> None:
        with self._lock:
            state = self._calls.get(call_uuid)
            if state is not None:
                state.finalized = True
                state.duration = duration
                state.hangup_cause = cause

    def mark_order_emitted(self, call_uuid: str, session_id: str) -> bool:
        """Returns True if this is the first emission for this session (i.e.
        the caller should proceed), False if already emitted (skip)."""
        with self._lock:
            state = self._calls.get(call_uuid)
            if state is None:
                return True
            if state.order_emitted_for_session == session_id:
                return False
            state.order_emitted_for_session = session_id
            return True

    def mark_handoff_emitted(self, call_uuid: str, session_id: str) -> bool:
        with self._lock:
            state = self._calls.get(call_uuid)
            if state is None:
                return True
            if state.manager_handoff_emitted_for_session == session_id:
                return False
            state.manager_handoff_emitted_for_session = session_id
            return True

    def _evict_expired(self) -> None:
        now = time.monotonic()
        expired = [
            uuid
            for uuid, state in self._calls.items()
            if now - state.last_seen > _TTL_SECONDS
        ]
        for uuid in expired:
            del self._calls[uuid]


registry = _CallRegistry()

# Module-level convenience wrappers so call sites read as `calls.start(...)`
# when imported as `from calls import state as calls`.
start = registry.start
get = registry.get
bind_session = registry.bind_session
already_finalized = registry.already_finalized
finalize = registry.finalize
mark_order_emitted = registry.mark_order_emitted
mark_handoff_emitted = registry.mark_handoff_emitted
