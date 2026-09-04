"""The live engine: a running fusion of one shopper's stream against their lock.

`api/app/routers/ws.py` owns the sockets; this module owns the state and the
numbers. One `LiveState` per session, held in memory, holding everything a
SPEC 4.7 message needs. Nothing here reads the database: the session's mode
and its locked prediction are handed in once, when the state is opened, and
every batch after that is pure in-memory work.

Two rules from CLAUDE.md shape everything below.

**The fusion formula is not here.** `analytics/fusion.py` is the single
implementation and this module imports `fuse_session`; `analytics/metrics.py`
is the single implementation of Spearman and this module imports
`attention_spearman`. Neither the 0.5/0.3/0.2 webcam weights, the 0.7/0.3
cursor-only weights, the interaction weights, nor the normalisation appear in
this file, and they must not.

**Running fusion equals offline fusion, by construction.** `LiveState` keeps
the session's accumulated events in arrival order and hands that whole list to
`fuse_session` on every batch. It is therefore not an approximation of the
offline result, or a streaming reformulation of it that happens to agree: it
is literally the same call, on the same list, in the same order, so the two
are the identical sequence of floating-point operations and compare equal
exactly. `api/tests/test_live.py::test_running_fusion_equals_offline_fusion`
is what holds that in place.

The cost of that choice is honest and bounded: a fold is O(events so far)
rather than O(batch), so the last batch of a session is the most expensive
one. Measured on a 3,000-event session (about three minutes of dense capture,
far past the 60-second demo) the worst batch folds in ~2 ms against a 20 ms
budget - see the budget test. Accumulating per-slot totals instead would make
it O(1) in the session length, but only by rewriting fusion's
event-to-accumulator mapping here, which is exactly the duplicated maths
CLAUDE.md forbids. Correctness by construction is worth the two milliseconds.
"""
from __future__ import annotations

import threading
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from analytics.fusion import fuse_session
from analytics.metrics import attention_spearman

# SPEC 4.7: the agreement meter reads "warming up" until the session has this
# many fixations. Below it the Spearman is still reported - it is simply not
# yet worth believing, and `meaningful` is how the spectator view knows.
MEANINGFUL_MIN_FIXATIONS = 15

# Event types that carry a screen position for the gaze trail. A fixation is
# the filtered, dwelling form of a gaze and carries the same x/y, so either
# updates the dot.
_POSITIONED_EVENT_TYPES = ("gaze", "fixation")

_states: Dict[str, "LiveState"] = {}
_states_lock = threading.Lock()


def slot_vocabulary(lock: Mapping[str, Any]) -> Tuple[str, ...]:
    """The slot ids a session is scored over, taken from its prediction lock.

    Sorted, so the vocabulary - and therefore the summation order inside
    `fuse_session` - is identical for every caller that starts from the same
    lock, whatever order the JSON happened to be written in.

    Taking it from the lock rather than from the planogram is deliberate: it is
    one fewer thing to load on the socket's connect path, and it guarantees the
    real vector and the locked vector are indexed by exactly the same keys.
    `api/app/prediction.py:occupied_slot_ids` documents which slots those are.
    """
    return tuple(sorted(lock["population_fixation_prob"]))


class LiveState:
    """Everything one running session needs, in memory.

    Not thread-safe on its own: `fold()` mutates. One websocket connection owns
    one state and folds batches sequentially, which is the only access pattern
    `routers/ws.py` creates.
    """

    __slots__ = ("session_id", "prediction_id", "mode", "slot_ids", "locked",
                 "_events", "n_fixations", "_stations", "latest_gaze", "t_ms")

    def __init__(self, session_id: str, *, mode: str, lock: Mapping[str, Any]) -> None:
        self.session_id = session_id
        self.prediction_id = lock["prediction_id"]
        self.mode = mode
        self.slot_ids: Tuple[str, ...] = slot_vocabulary(lock)
        # A plain dict copy: the lock file itself is immutable evidence and
        # nothing downstream should be able to reach back into the loaded
        # document through this state.
        self.locked: Dict[str, float] = {
            slot_id: float(value)
            for slot_id, value in lock["population_fixation_prob"].items()
        }
        self._events: List[Dict[str, Any]] = []
        self.n_fixations = 0
        self._stations: set[str] = set()
        self.latest_gaze: Optional[Dict[str, Any]] = None
        self.t_ms = 0

    # -- ingest ------------------------------------------------------------

    def fold(self, events: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
        """Add one batch to the session and return the new SPEC 4.7 message."""
        for event in events:
            self._events.append(dict(event))

            t_ms = event.get("t_ms")
            if isinstance(t_ms, (int, float)) and t_ms > self.t_ms:
                self.t_ms = int(t_ms)

            station_id = event.get("station_id")
            if isinstance(station_id, str) and station_id:
                self._stations.add(station_id)

            event_type = event.get("type")
            if event_type == "fixation":
                self.n_fixations += 1

            if event_type in _POSITIONED_EVENT_TYPES:
                payload = event.get("payload") or {}
                x, y = payload.get("x"), payload.get("y")
                if x is not None and y is not None:
                    self.latest_gaze = {"x": x, "y": y}

        return self.snapshot()

    # -- output ------------------------------------------------------------

    def snapshot(self) -> Dict[str, Any]:
        """The SPEC 4.7 message for the session as it stands right now.

        Also what a spectator joining mid-session is sent, so the heatmap and
        the badge are populated on their first frame instead of staying blank
        until the shopper's next batch.
        """
        attention = fuse_session(self._events, self.slot_ids, mode=self.mode)
        return {
            "session_id": self.session_id,
            "t_ms": self.t_ms,
            "n_fixations": self.n_fixations,
            "stations_visited": len(self._stations),
            "attention": attention,
            "latest_gaze": self.latest_gaze,
            # Real first, synthetic second - the argument order
            # analytics/metrics.py documents. The synthetic side is the LOCKED
            # vector, never a fresh simulation: comparing against anything
            # re-run now would silently drop the pre-registration.
            "spearman": attention_spearman(attention, self.locked, self.slot_ids),
            "meaningful": self.n_fixations >= MEANINGFUL_MIN_FIXATIONS,
            "prediction_id": self.prediction_id,
        }

    @property
    def events(self) -> Sequence[Mapping[str, Any]]:
        """The accumulated events, in arrival order (read-only view)."""
        return tuple(self._events)


# ---------------------------------------------------------------------------
# The per-session registry
# ---------------------------------------------------------------------------


def open_state(session_id: str, *, mode: str, lock: Mapping[str, Any]) -> LiveState:
    """Start (or restart) the live state for a session and register it.

    A reconnecting shopper gets a fresh state rather than resuming the old one:
    the browser holds the authoritative local buffer (docs/PLAN.md 13 replaced
    the SPEC's ack protocol with "plain WS + local buffer + REST fallback"), so
    it replays from the beginning of the session and folding the replay into a
    half-full state would double-count everything it re-sent.
    """
    state = LiveState(session_id, mode=mode, lock=lock)
    with _states_lock:
        _states[session_id] = state
    return state


def get_state(session_id: str) -> Optional[LiveState]:
    """The live state for a session, or None if none is running."""
    with _states_lock:
        return _states.get(session_id)


def forget(session_id: str) -> None:
    """Drop a session's live state. Idempotent."""
    with _states_lock:
        _states.pop(session_id, None)
