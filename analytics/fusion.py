"""Attention fusion -- the single formula for turning one session's raw
behavioural events into a per-slot attention score.

Per CLAUDE.md, this is the *only* place the fusion maths may live:
`api/app/live.py` imports `fuse_session` directly on its hot path, so this
module stays pure (no I/O, no database, no HTTP, no globals) and dependency
free.

Cursor-only formula (SPEC M5). The webcam formula that adds a fixation-dwell
term is session S16 and out of scope here; this module implements only the
mode below, in full, with no placeholder for the other mode:

    att = 0.7 * cursor_dwell_norm + 0.3 * interaction_norm

where, per slot:
  - cursor_dwell_norm is the summed `cursor_dwell` duration (ms), normalised
    to sum to 1 across the session's slot vocabulary.
  - interaction_norm is the MAX interaction weight observed for that slot
    (hover=0.5, pickup=1.0, add_to_cart=1.0 -- not the sum, not the count),
    normalised to sum to 1 across the session's slot vocabulary.
"""

from typing import Mapping, Sequence

# hover < pickup == add_to_cart. A slot's interaction score is the MAX of
# these observed for it, never a sum or a count.
_INTERACTION_WEIGHTS: Mapping[str, float] = {
    "hover": 0.5,
    "pickup": 1.0,
    "add_to_cart": 1.0,
}

_CURSOR_WEIGHT = 0.7
_INTERACTION_WEIGHT = 0.3


def fuse_session(events: list[dict], slot_ids: Sequence[str]) -> dict[str, float]:
    """Fuse one session's raw events into a per-slot attention vector.

    `slot_ids` is the full slot vocabulary of the resolved planogram: every
    id in it is a key in the returned dict, even when its attention is 0.0.
    This keeps the output aligned with whatever vector `metrics.py` compares
    it against.

    Events follow schemas/event.schema.json. Only these types feed the
    formula; every other type (gaze, fixation, remove, station_enter,
    station_exit, checkout) is ignored:
      - cursor_dwell -> payload {slot_id, dur_ms}, summed per slot.
      - hover, pickup, add_to_cart -> payload {sku_id, slot_id}, max weight
        per slot (see _INTERACTION_WEIGHTS).

    An event naming a slot_id outside `slot_ids` is dropped rather than
    raising: a slot can disappear between planogram revisions, and this runs
    on the live engine's hot path where a crash is worse than a dropped
    sample.

    Every division is guarded: a component whose raw total is 0 (e.g. a
    session with no interaction events at all) normalises to an all-zero
    vector rather than NaN, and the fused output is simply the other
    component scaled by its own weight -- the two weights are not
    renormalised to compensate for the missing component.
    """
    known_slots = set(slot_ids)
    cursor_dwell_ms: dict[str, float] = {slot_id: 0.0 for slot_id in slot_ids}
    interaction_weight: dict[str, float] = {slot_id: 0.0 for slot_id in slot_ids}

    for event in events:
        payload = event.get("payload") or {}
        slot_id = payload.get("slot_id")
        if slot_id not in known_slots:
            continue  # unknown (or absent) slot_id -- ignore, never raise

        event_type = event.get("type")
        if event_type == "cursor_dwell":
            cursor_dwell_ms[slot_id] += payload.get("dur_ms", 0)
        elif event_type in _INTERACTION_WEIGHTS:
            weight = _INTERACTION_WEIGHTS[event_type]
            if weight > interaction_weight[slot_id]:
                interaction_weight[slot_id] = weight
        # every other event type is not part of this formula -- ignore

    cursor_norm = _normalise(cursor_dwell_ms, slot_ids)
    interaction_norm = _normalise(interaction_weight, slot_ids)

    return {
        slot_id: _CURSOR_WEIGHT * cursor_norm[slot_id] + _INTERACTION_WEIGHT * interaction_norm[slot_id]
        for slot_id in slot_ids
    }


def _normalise(totals: Mapping[str, float], slot_ids: Sequence[str]) -> dict[str, float]:
    """Scale `totals` so it sums to 1 over `slot_ids`.

    Guards the division: when the raw total is 0 or less (nothing observed
    for any slot), returns an all-zero vector instead of dividing by zero.
    """
    grand_total = sum(totals[slot_id] for slot_id in slot_ids)
    if grand_total <= 0:
        return {slot_id: 0.0 for slot_id in slot_ids}
    return {slot_id: totals[slot_id] / grand_total for slot_id in slot_ids}
