"""Tests for analytics/fusion.py -- the single attention-fusion formula.

Every expected value below is hand-computed in the comment above its
assertion; the arithmetic is worked out independently of fusion.py's
implementation, per project rule (CLAUDE.md: tests before implementation,
paste real output, never "this should work").

Cursor-only formula under test (SPEC M5; the webcam fixation term is S16 and
out of scope here):

    att = 0.7 * cursor_dwell_norm + 0.3 * interaction_norm

Each component is normalised to sum to 1 over the session's slot vocabulary.
The interaction weight for a slot is the MAX of {hover: 0.5, pickup: 1.0,
add_to_cart: 1.0} observed for that slot -- not the sum, not the count.
"""

import math

import pytest

from analytics.fusion import fuse_session


def _event(type_: str, slot_id: str | None, dur_ms: int | None = None, t_ms: int = 0) -> dict:
    """Build a minimal event dict shaped like schemas/event.schema.json."""
    payload: dict = {}
    if slot_id is not None:
        payload["slot_id"] = slot_id
    if type_ in ("hover", "pickup", "add_to_cart"):
        payload["sku_id"] = "SKU_X"
    if dur_ms is not None:
        payload["dur_ms"] = dur_ms
    return {"t_ms": t_ms, "type": type_, "station_id": "B1", "payload": payload}


def test_worked_example_from_spec():
    """Hand-computed worked example (also given in the S5 session prompt):

    slot_ids = [S1, S2, S3]; cursor dwell 300/100/0 ms; pickup on S1; hover on S2.

    cursor totals   = [300, 100, 0]  -> sum 400 -> cursor_norm      = [0.75, 0.25, 0.0]
    interaction max = [1.0, 0.5, 0]  -> sum 1.5 -> interaction_norm = [2/3, 1/3, 0.0]

    att = 0.7*cursor_norm + 0.3*interaction_norm
        S1: 0.7*0.75 + 0.3*(2/3) = 0.525 + 0.2 = 0.725
        S2: 0.7*0.25 + 0.3*(1/3) = 0.175 + 0.1 = 0.275
        S3: 0.7*0.00 + 0.3*0.0   = 0.0
    """
    slot_ids = ["S1", "S2", "S3"]
    events = [
        _event("cursor_dwell", "S1", dur_ms=300),
        _event("cursor_dwell", "S2", dur_ms=100),
        _event("pickup", "S1"),
        _event("hover", "S2"),
    ]

    result = fuse_session(events, slot_ids)

    assert result == pytest.approx({"S1": 0.725, "S2": 0.275, "S3": 0.0})


def test_interaction_uses_max_not_sum_across_event_types():
    """A slot with hover AND pickup scores the same as a slot with pickup alone
    (max(0.5, 1.0) == max(1.0) == 1.0) -- it must NOT be 1.5 (summed).

    No cursor events anywhere in either scenario, so the fused value is pure
    0.3 * interaction_norm.

    Scenario A: S1 = pickup only (max 1.0), S2 = hover only (max 0.5).
    Scenario B: S1 = hover THEN pickup (max(0.5, 1.0) = 1.0, same as A), S2 = hover only (0.5).

    Both scenarios must produce an IDENTICAL interaction weight per slot,
    hence an identical fused vector:
      totals = [1.0, 0.5] -> sum 1.5 -> interaction_norm = [2/3, 1/3]
      fused  = [0.3*2/3, 0.3*1/3] = [0.2, 0.1]

    (If pickup+hover were summed instead of maxed, scenario B's S1 total would
    be 1.5 instead of 1.0, changing the split to [0.75, 0.25] and fused to
    [0.225, 0.075] -- different from scenario A. Asserting the exact numbers
    below, not just A == B, catches that bug.)
    """
    slot_ids = ["S1", "S2"]
    events_a = [_event("pickup", "S1"), _event("hover", "S2")]
    events_b = [_event("hover", "S1"), _event("pickup", "S1"), _event("hover", "S2")]

    result_a = fuse_session(events_a, slot_ids)
    result_b = fuse_session(events_b, slot_ids)

    expected = {"S1": pytest.approx(0.2), "S2": pytest.approx(0.1)}
    assert result_a == expected
    assert result_b == expected


def test_two_hovers_on_one_slot_score_same_as_one_hover():
    """Two `hover` events on S1 must score max(0.5, 0.5) = 0.5, not 1.0 (summed).

    S1 = two hover events (max 0.5), S2 = one hover event (max 0.5) -> tied.
    totals = [0.5, 0.5] -> sum 1.0 -> interaction_norm = [0.5, 0.5]
    fused (no cursor events) = [0.3*0.5, 0.3*0.5] = [0.15, 0.15]

    (If summed, S1 would total 1.0 vs S2's 0.5, giving fused [0.2, 0.1]
    instead of the tied [0.15, 0.15] asserted here.)
    """
    slot_ids = ["S1", "S2"]
    events = [_event("hover", "S1"), _event("hover", "S1"), _event("hover", "S2")]

    result = fuse_session(events, slot_ids)

    assert result == pytest.approx({"S1": 0.15, "S2": 0.15})


def test_every_slot_id_appears_including_untouched_ones():
    """S3 and S4 have no events at all but must still be present, at 0.0."""
    slot_ids = ["S1", "S2", "S3", "S4"]
    events = [_event("cursor_dwell", "S1", dur_ms=100), _event("hover", "S2")]

    result = fuse_session(events, slot_ids)

    assert set(result.keys()) == set(slot_ids)
    assert result["S3"] == 0.0
    assert result["S4"] == 0.0


def test_cursor_dwell_with_zero_interactions_has_no_nan():
    """No interaction events at all: interaction totals to 0, normalises to an
    all-zero vector (guarded, not NaN), so the fused output is exactly
    0.7 * cursor_norm with nothing added for interaction.

    cursor totals = [200, 200] -> sum 400 -> cursor_norm = [0.5, 0.5]
    fused = [0.7*0.5, 0.7*0.5] = [0.35, 0.35]
    """
    slot_ids = ["S1", "S2"]
    events = [_event("cursor_dwell", "S1", dur_ms=200), _event("cursor_dwell", "S2", dur_ms=200)]

    result = fuse_session(events, slot_ids)

    assert result == pytest.approx({"S1": 0.35, "S2": 0.35})
    assert not any(math.isnan(v) for v in result.values())


def test_no_usable_events_returns_all_zero():
    """An empty event list must not raise and must not divide by zero."""
    slot_ids = ["S1", "S2", "S3"]

    result = fuse_session([], slot_ids)

    assert result == {"S1": 0.0, "S2": 0.0, "S3": 0.0}
    assert not any(math.isnan(v) for v in result.values())


def test_events_of_ignored_types_do_not_raise_or_contribute():
    """gaze, fixation, remove, station_enter, station_exit, checkout are not
    part of this formula and must be silently ignored, leaving an all-zero
    result when they are the only events present.
    """
    slot_ids = ["S1", "S2"]
    events = [
        {"t_ms": 0, "type": "gaze", "station_id": "B1", "payload": {"x": 0.1, "y": 0.2, "conf": 0.9}},
        {"t_ms": 10, "type": "fixation", "station_id": "B1",
         "payload": {"x": 0.1, "y": 0.2, "dur_ms": 50, "slot_id": "S1", "shelf_id": None}},
        {"t_ms": 20, "type": "remove", "station_id": "B1", "payload": {"sku_id": "SKU_X", "slot_id": "S1"}},
        {"t_ms": 30, "type": "station_enter", "station_id": "B1", "payload": {}},
        {"t_ms": 40, "type": "station_exit", "station_id": "B1", "payload": {}},
        {"t_ms": 50, "type": "checkout", "station_id": None, "payload": {}},
    ]

    result = fuse_session(events, slot_ids)

    assert result == {"S1": 0.0, "S2": 0.0}


def test_unknown_slot_id_events_are_ignored_and_do_not_skew_known_slots():
    """Events naming a slot_id outside slot_ids (e.g. a slot removed in a
    later planogram revision) must be dropped entirely, not counted in any
    total.

    Known-slot cursor dwell: S1 = 100ms. If the unknown slot's 500ms were
    wrongly folded into the normalisation total, S1 would come out as
    100/600 = 0.1667 (fused 0.1167) instead of the correct 100/100 = 1.0
    (fused 0.7), since the unknown slot itself is dropped from the output.
    Its pickup event is dropped the same way -- interaction totals to 0.
    """
    slot_ids = ["S1", "S2"]
    events = [
        _event("cursor_dwell", "S1", dur_ms=100),
        _event("cursor_dwell", "S999", dur_ms=500),
        _event("pickup", "S999"),
    ]

    result = fuse_session(events, slot_ids)

    assert result == pytest.approx({"S1": 0.7, "S2": 0.0})


def test_fused_vector_sums_to_one_when_both_components_present():
    """0.7 + 0.3 == 1 and each component itself sums to 1, so whenever both
    components have at least one non-zero contribution, the fused vector
    must sum to exactly 1.0 (up to float rounding).

    cursor totals   = [100, 300] -> sum 400 -> cursor_norm      = [0.25, 0.75]
    interaction max = [1.0, 0.5] -> sum 1.5 -> interaction_norm = [2/3, 1/3]
    (individual values aren't re-asserted here -- that arithmetic style is
    already covered by test_worked_example_from_spec above; this test is
    only about the sum-to-one property.)
    """
    slot_ids = ["S1", "S2"]
    events = [
        _event("cursor_dwell", "S1", dur_ms=100),
        _event("cursor_dwell", "S2", dur_ms=300),
        _event("add_to_cart", "S1"),
        _event("hover", "S2"),
    ]

    result = fuse_session(events, slot_ids)

    assert sum(result.values()) == pytest.approx(1.0)
