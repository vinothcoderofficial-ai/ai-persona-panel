"""The live engine: a running fusion of one shopper's stream against their lock.

`api/app/routers/ws.py` owns the sockets; this module owns the state and the
numbers. One `LiveState` per session, held in memory, holding everything a
SPEC 4.7 message needs. Nothing here reads the database: the session's mode,
its locked prediction and the resolved planogram it was locked over are handed
in once, when the state is opened, and every batch after that is pure
in-memory work.

Three rules from CLAUDE.md shape everything below.

**The fusion formula is not here.** `analytics/fusion.py` is the single
implementation and this module imports `fuse_session` and `fuse_synthetic`;
`analytics/metrics.py` is the single implementation of Spearman and this module
imports `attention_spearman`. Neither the 0.5/0.3/0.2 webcam weights, the
0.7/0.3 cursor-only weights, the synthetic weights, the interaction weights,
nor the normalisation appear in this file, and they must not.

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

**Both sides of the live comparison are fused.** The real side goes through
`fuse_session`; the synthetic side goes through `fuse_synthetic`, exactly as
`scripts/eval.py` does it. It did not always: the live meter used to compare
against the lock's `population_fixation_prob` *raw*, which meant the rho on
the spectator screen and the rho in RESULTS.md were correlations against two
different vectors and need not have agreed. `synthetic_vector()` below closes
that, and `test_live_spearman_equals_the_offline_evaluation_spearman` holds it
closed.

Fusing does not weaken the pre-registration, and the lock file is untouched by
this. `fuse_synthetic` is a deterministic transform of the locked run - the
locked vector is its looking channel - plus the resolved planogram and the
SimResult's `purchase_share`. What the lock's `sha256` covers is unchanged:
`population_fixation_prob` + `sim_run_id` + `created_at`, and nothing else was
added to the hashed payload or to the document. What *is* verified, loudly, is
that the simulator still produces the run the lock names: see
`synthetic_vector`.


`meaningful`, and a deliberate deviation from SPEC 4.7
------------------------------------------------------
SPEC 4.7 says *"`meaningful` is false until `n_fixations >= 15`"*. That was
written for a webcam session. There is no webcam panel: `data/sessions/anon/`
is empty, the S9 pilot was never run, and every session the demo can actually
produce is `cursor_only` - a mode whose gaze trail is empty by construction
and whose fixation count is therefore permanently 0. Taken literally, SPEC 4.7
leaves the agreement meter reading "warming up" for the entire duration of
every session that exists, which is not a conservative safeguard, it is a dead
readout.

So `meaningful` counts the channel that actually carries the session's
attention signal, per capture mode:

  * `webcam`      -> `fixation` events, exactly as SPEC 4.7 says;
  * `cursor_only` -> `cursor_dwell` events, which are the 0.7 term of
    `fuse_session`'s cursor-only formula and the only looking-like evidence
    such a session produces.

The threshold is 15 in both modes, unchanged. The two are comparable units of
evidence rather than an arbitrary reuse of the number: a fixation must last
`MIN_FIXATION_MS` = 100 ms to be emitted at all
(`web/src/capture/FixationFilter.ts`), and a cursor dwell must last
`CURSOR_DWELL_MIN_MS` = 300 ms (`web/src/capture/CursorTracker.ts`), so 15
dwells is if anything the stricter bar in elapsed attention.

The message says which is which rather than relabelling anything. `n_fixations`
still counts `fixation` events and only those, `n_cursor_dwells` counts
`cursor_dwell` events, and `evidence_count`/`evidence_kind` name the one the
threshold is applied to - so no count on the spectator screen is ever labelled
as something it is not. `docs/METHODOLOGY.md` 3.3 records the deviation.
"""
from __future__ import annotations

import threading
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from analytics.fusion import fuse_session, fuse_synthetic
from analytics.metrics import attention_spearman
from api.app import prediction, simcache

# The agreement meter reads "warming up" below this many units of evidence.
# Below it the Spearman is still reported - it is simply not yet worth
# believing, and `meaningful` is how the spectator view knows.
MEANINGFUL_MIN_EVIDENCE = 15

# mode -> (the event type that counts as evidence, the name for it in the
# message). The keys are schemas/session.schema.json's `mode` enum and match
# `analytics/fusion.py:_MODE_WEIGHTS`, so a mode that can be fused can be
# metered and vice versa.
_EVIDENCE_BY_MODE: Mapping[str, Tuple[str, str]] = {
    "webcam": ("fixation", "fixations"),
    "cursor_only": ("cursor_dwell", "cursor_dwells"),
}

# How far the freshly simulated population may drift from the locked one before
# the lock is declared stale. Not zero: the same seeded simulation can land a
# last-bit apart across numpy builds, and refusing a demo over 1e-16 would be
# theatre. Not loose either: the population result is an average over 40,000
# simulated shoppers, so any *real* change to the simulator, the personas, the
# policies or the planogram moves these probabilities by orders of magnitude
# more than this.
LOCK_DRIFT_TOLERANCE = 1e-12

# Event types that carry a screen position for the gaze trail. A fixation is
# the filtered, dwelling form of a gaze and carries the same x/y, so either
# updates the dot.
_POSITIONED_EVENT_TYPES = ("gaze", "fixation")

_states: Dict[str, "LiveState"] = {}
_states_lock = threading.Lock()


class StalePredictionLock(RuntimeError):
    """The lock no longer describes what the simulator produces.

    Raised instead of quietly scoring the session against a vector its
    pre-registration never committed to. `routers/ws.py` turns this into a
    socket close, so a session with a stale lock records nothing rather than
    recording something that cannot honestly be evaluated later.
    """


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


def synthetic_vector(
    lock: Mapping[str, Any],
    resolved_planogram: Mapping[str, Any],
    slot_ids: Sequence[str],
    *,
    mode: str,
) -> Dict[str, float]:
    """The synthetic side of the live comparison: `fuse_synthetic` of the
    LOCKED run, verified against the lock before it is used.

    `scripts/eval.py` computes exactly this for its offline numbers. Comparing
    against anything else - the raw locked vector, or an unverified fresh
    simulation - would either put the live meter and the report on different
    footings or drop the pre-registration entirely.

    The lock stores neither the SimResult nor the planogram, only
    `population_fixation_prob` and `sim_run_id`, so the run is recomputed
    through `api/app/simcache.py:population` - the same cached, deterministic
    call `prediction.write_lock` made, at the same `N_SYNTH` and `SEED`, which
    is why the numbers are the identical ones and not merely similar ones. In
    the normal flow `POST /sessions` warmed that cache moments earlier and this
    is a dict lookup; on a cold process it re-runs the simulation once, at
    connect, never on the hot path.

    Two things are then checked, and a failure raises `StalePredictionLock`:

      * `sim_run_id` matches. This is cheap but coarse - `sim/simulator.py`
        derives it from `variant_id|persona_id|n_runs|seed` alone, so it
        catches a changed variant, seed, resolution or persona count and
        nothing else.
      * the freshly simulated `population_fixation_prob`, rebuilt over the same
        vocabulary `prediction.write_lock` used, still equals the locked one to
        `LOCK_DRIFT_TOLERANCE`. This is the check that has teeth: it is the
        exact vector the lock's `sha256` covers, so it catches a changed
        planogram, changed policies, or changed saliency maths - none of which
        move `sim_run_id` at all.
    """
    if mode not in _EVIDENCE_BY_MODE:
        raise ValueError(
            f"unknown capture mode {mode!r}; expected one of {sorted(_EVIDENCE_BY_MODE)}"
        )

    bundle = simcache.population(
        resolved_planogram,
        lock["variant_id"],
        n_synth=prediction.N_SYNTH,
        seed=prediction.SEED,
    )
    _verify_lock_still_describes(lock, bundle.population, resolved_planogram)
    return fuse_synthetic(bundle.population, resolved_planogram, slot_ids, mode=mode)


def _verify_lock_still_describes(
    lock: Mapping[str, Any],
    population: Mapping[str, Any],
    resolved_planogram: Mapping[str, Any],
) -> None:
    """Raise `StalePredictionLock` unless `population` is the locked run."""
    session_id = lock.get("session_id", "<unknown>")

    fresh_run_id = population["sim_run_id"]
    if fresh_run_id != lock["sim_run_id"]:
        raise StalePredictionLock(
            f"session {session_id}: lock sim_run_id {lock['sim_run_id']!r} but the "
            f"simulator now produces {fresh_run_id!r}"
        )

    locked = lock["population_fixation_prob"]
    fresh = {
        slot_id: float(population["fixation_prob"].get(slot_id, 0.0))
        for slot_id in prediction.occupied_slot_ids(resolved_planogram)
    }

    if set(fresh) != set(locked):
        missing = sorted(set(locked) - set(fresh))
        added = sorted(set(fresh) - set(locked))
        raise StalePredictionLock(
            f"session {session_id}: lock population_fixation_prob covers different "
            f"slots than the planogram now resolves to (gone: {missing[:5]}, "
            f"new: {added[:5]})"
        )

    drift = max(
        (abs(fresh[slot_id] - float(locked[slot_id])) for slot_id in fresh), default=0.0
    )
    if drift > LOCK_DRIFT_TOLERANCE:
        raise StalePredictionLock(
            f"session {session_id}: lock population_fixation_prob no longer matches "
            f"the simulator (max drift {drift:.3e} > {LOCK_DRIFT_TOLERANCE:.0e})"
        )


class LiveState:
    """Everything one running session needs, in memory.

    Not thread-safe on its own: `fold()` mutates. One websocket connection owns
    one state and folds batches sequentially, which is the only access pattern
    `routers/ws.py` creates.
    """

    __slots__ = ("session_id", "prediction_id", "mode", "slot_ids", "locked",
                 "synthetic", "evidence_kind", "_evidence_event_type", "_events",
                 "n_fixations", "n_cursor_dwells", "_stations", "latest_gaze", "t_ms")

    def __init__(self, session_id: str, *, mode: str, lock: Mapping[str, Any],
                 resolved_planogram: Mapping[str, Any]) -> None:
        if mode not in _EVIDENCE_BY_MODE:
            raise ValueError(
                f"unknown capture mode {mode!r}; expected one of "
                f"{sorted(_EVIDENCE_BY_MODE)}"
            )

        self.session_id = session_id
        self.prediction_id = lock["prediction_id"]
        self.mode = mode
        self.slot_ids: Tuple[str, ...] = slot_vocabulary(lock)
        # A plain dict copy: the lock file itself is immutable evidence and
        # nothing downstream should be able to reach back into the loaded
        # document through this state. Kept beside the fused vector because it
        # is the thing that was hashed - `GET /sessions/{id}/prediction` serves
        # it, and the spectator's locked heatmap column is drawn from it.
        self.locked: Dict[str, float] = {
            slot_id: float(value)
            for slot_id, value in lock["population_fixation_prob"].items()
        }
        # Computed once, here, off the hot path. Raises StalePredictionLock if
        # the lock no longer describes what the simulator produces.
        self.synthetic: Dict[str, float] = synthetic_vector(
            lock, resolved_planogram, self.slot_ids, mode=mode
        )

        self._evidence_event_type, self.evidence_kind = _EVIDENCE_BY_MODE[mode]
        self._events: List[Dict[str, Any]] = []
        self.n_fixations = 0
        self.n_cursor_dwells = 0
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
            elif event_type == "cursor_dwell":
                self.n_cursor_dwells += 1

            if event_type in _POSITIONED_EVENT_TYPES:
                payload = event.get("payload") or {}
                x, y = payload.get("x"), payload.get("y")
                if x is not None and y is not None:
                    self.latest_gaze = {"x": x, "y": y}

        return self.snapshot()

    # -- output ------------------------------------------------------------

    @property
    def evidence_count(self) -> int:
        """The count `meaningful` is applied to, for this session's mode.

        Always equal to whichever of `n_fixations` / `n_cursor_dwells`
        `evidence_kind` names, so the message is self-consistent and a reader
        can check it against the raw counts in the same frame.
        """
        if self._evidence_event_type == "fixation":
            return self.n_fixations
        return self.n_cursor_dwells

    def snapshot(self) -> Dict[str, Any]:
        """The SPEC 4.7 message for the session as it stands right now.

        Also what a spectator joining mid-session is sent, so the heatmap and
        the badge are populated on their first frame instead of staying blank
        until the shopper's next batch.
        """
        attention = fuse_session(self._events, self.slot_ids, mode=self.mode)
        evidence = self.evidence_count
        return {
            "session_id": self.session_id,
            "t_ms": self.t_ms,
            "n_fixations": self.n_fixations,
            "n_cursor_dwells": self.n_cursor_dwells,
            "evidence_count": evidence,
            "evidence_kind": self.evidence_kind,
            "stations_visited": len(self._stations),
            "attention": attention,
            "latest_gaze": self.latest_gaze,
            # Real first, synthetic second - the argument order
            # analytics/metrics.py documents. The synthetic side is
            # `fuse_synthetic` of the LOCKED run, computed once in __init__:
            # the same vector scripts/eval.py scores against, so the spectator
            # screen and RESULTS.md cannot disagree.
            "spearman": attention_spearman(attention, self.synthetic, self.slot_ids),
            "meaningful": evidence >= MEANINGFUL_MIN_EVIDENCE,
            "prediction_id": self.prediction_id,
        }

    @property
    def events(self) -> Sequence[Mapping[str, Any]]:
        """The accumulated events, in arrival order (read-only view)."""
        return tuple(self._events)


# ---------------------------------------------------------------------------
# The per-session registry
# ---------------------------------------------------------------------------


def open_state(session_id: str, *, mode: str, lock: Mapping[str, Any],
               resolved_planogram: Mapping[str, Any]) -> LiveState:
    """Start (or restart) the live state for a session and register it.

    `resolved_planogram` is the planogram the lock was computed over, resolved
    from the same database rows `POST /sessions` used. It is required, not
    optional: without it the comparison falls back to the raw locked vector,
    which is the defect this signature exists to make unrepresentable.

    This is where the session pays for its synthetic vector - a cached
    simulation lookup, or one simulation on a cold process. It runs once per
    session and never on the hot path. It raises `StalePredictionLock` if the
    lock no longer describes what the simulator produces, and registers nothing
    in that case.

    A reconnecting shopper gets a fresh state rather than resuming the old one:
    the browser holds the authoritative local buffer (docs/PLAN.md 13 replaced
    the SPEC's ack protocol with "plain WS + local buffer + REST fallback"), so
    it replays from the beginning of the session and folding the replay into a
    half-full state would double-count everything it re-sent.
    """
    state = LiveState(session_id, mode=mode, lock=lock,
                      resolved_planogram=resolved_planogram)
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
