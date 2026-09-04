"""Tests for analytics/fusion.py -- the single attention-fusion formula.

Every expected value below is hand-computed in the comment above its
assertion; the arithmetic is worked out independently of fusion.py's
implementation, per project rule (CLAUDE.md: tests before implementation,
paste real output, never "this should work").

Two formulas are under test (SPEC M5), selected by the keyword-only `mode`:

    cursor_only (the default):  att = 0.7*cursor_norm + 0.3*interaction_norm
    webcam:      att = 0.5*fix_dwell_norm + 0.3*cursor_norm + 0.2*interaction_norm

Each component is normalised to sum to 1 over the session's slot vocabulary.
The interaction weight for a slot is the MAX of {hover: 0.5, pickup: 1.0,
add_to_cart: 1.0} observed for that slot -- not the sum, not the count.
The weights are NOT renormalised when a component is empty.

The synthetic counterpart, `fuse_synthetic`, is exercised in the last section
of this file; its two formulas are derived from the same weight table.
"""

import json
import math

import numpy as np
import pytest

from analytics.fusion import (
    _MODE_WEIGHTS,
    _normalise,
    _normalise_rows,
    fuse_session,
    fuse_synthetic,
    purchase_slot_matrix,
    synthetic_weights,
)
from api.app.db import ROOT
from api.app.resolve import resolve


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
    part of the cursor-only formula and must be silently ignored, leaving an
    all-zero result when they are the only events present. (The fixation event
    below DOES count in mode="webcam" -- see the S16 block at the end of this
    file.)
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


# ---------------------------------------------------------------------------
# S16 -- the webcam branch, and proof the cursor-only branch is untouched.
# ---------------------------------------------------------------------------


def _fixation(slot_id: str | None, dur_ms: int, t_ms: int = 0) -> dict:
    """Build a `fixation` event with the full payload shape from
    schemas/event.schema.json: {x, y, dur_ms, slot_id|null, shelf_id|null}.

    A fixation with slot_id None landed on a shelf rather than on a product
    slot, so it belongs to no slot at all.
    """
    return {
        "t_ms": t_ms,
        "type": "fixation",
        "station_id": "B1",
        "payload": {
            "x": 0.5,
            "y": 0.5,
            "dur_ms": dur_ms,
            "slot_id": slot_id,
            "shelf_id": "SH1" if slot_id is None else None,
        },
    }


def test_two_argument_call_is_unchanged_by_the_new_mode_parameter():
    """`api/app/routers/experiments.py` calls fuse_session(events, slot_ids)
    positionally, so the two-argument form must keep its exact behaviour:
    cursor-only, and identical to passing mode="cursor_only" explicitly.

    Same inputs and same hand-computed answer as
    test_worked_example_from_spec: {S1: 0.725, S2: 0.275, S3: 0.0}.
    """
    slot_ids = ["S1", "S2", "S3"]
    events = [
        _event("cursor_dwell", "S1", dur_ms=300),
        _event("cursor_dwell", "S2", dur_ms=100),
        _event("pickup", "S1"),
        _event("hover", "S2"),
    ]

    two_arg = fuse_session(events, slot_ids)

    assert two_arg == pytest.approx({"S1": 0.725, "S2": 0.275, "S3": 0.0})
    assert two_arg == fuse_session(events, slot_ids, mode="cursor_only")


def test_two_argument_call_still_ignores_fixations():
    """Adding fixation events to a session fused with the default mode must
    not move a single number: cursor-only weights fixations at zero.

    Cursor dwell [200, 200] -> [0.5, 0.5]; no interactions; so
    fused = 0.7*[0.5, 0.5] = [0.35, 0.35] with or without the fixations.
    (In webcam mode the same fixations WOULD move it -- asserted below.)
    """
    slot_ids = ["S1", "S2"]
    cursor_only_events = [
        _event("cursor_dwell", "S1", dur_ms=200),
        _event("cursor_dwell", "S2", dur_ms=200),
    ]
    with_fixations = cursor_only_events + [_fixation("S1", 900), _fixation("S2", 100)]

    assert fuse_session(with_fixations, slot_ids) == pytest.approx({"S1": 0.35, "S2": 0.35})
    assert fuse_session(with_fixations, slot_ids) == fuse_session(cursor_only_events, slot_ids)


def test_webcam_worked_example():
    """Hand-computed webcam worked example over 3 slots.

    fixation dwell = [600, 200, 200] ms -> sum 1000 -> fix_norm    = [0.6, 0.2, 0.2]
    cursor dwell   = [300, 100,   0] ms -> sum  400 -> cursor_norm = [0.75, 0.25, 0.0]
    interaction max= [1.0, 0.5, 0.0]    -> sum  1.5 -> int_norm    = [2/3, 1/3, 0.0]

    att = 0.5*fix_norm + 0.3*cursor_norm + 0.2*int_norm
      S1: 0.5*0.6 + 0.3*0.75 + 0.2*(2/3) = 0.300 + 0.225 + 2/15
          = 60/120 + 27/120 + 16/120 = 79/120 = 0.658333...
      S2: 0.5*0.2 + 0.3*0.25 + 0.2*(1/3) = 0.100 + 0.075 + 1/15
          = 12/120 +  9/120 +  8/120 = 29/120 = 0.241666...
      S3: 0.5*0.2 + 0.3*0.00 + 0.2*0.0   = 0.1 = 12/120

    All three components are present, and 0.5+0.3+0.2 = 1, so the fused
    vector sums to exactly 1: (79 + 29 + 12)/120 = 120/120.

    The same events under cursor_only would give {S1: 0.725, S2: 0.275,
    S3: 0.0} (test_worked_example_from_spec), so this also proves the mode
    switch actually changes the maths.
    """
    slot_ids = ["S1", "S2", "S3"]
    events = [
        _fixation("S1", 600),
        _fixation("S2", 200),
        _fixation("S3", 200),
        _event("cursor_dwell", "S1", dur_ms=300),
        _event("cursor_dwell", "S2", dur_ms=100),
        _event("pickup", "S1"),
        _event("hover", "S2"),
    ]

    result = fuse_session(events, slot_ids, mode="webcam")

    assert result == pytest.approx({"S1": 79 / 120, "S2": 29 / 120, "S3": 0.1})
    assert sum(result.values()) == pytest.approx(1.0)


def test_webcam_fixation_dwell_is_summed_per_slot():
    """Several fixations on the same slot add up, exactly like cursor dwell.

    S1 gets 400 + 200 = 600 ms across two events; S2 gets 200 ms.
    fix totals = [600, 200] -> sum 800 -> fix_norm = [0.75, 0.25]
    No cursor and no interaction events, so fused = 0.5*fix_norm
        = [0.375, 0.125]  (summing to 0.5, not 1 -- the other components are
        genuinely absent and their weights are not redistributed).
    """
    slot_ids = ["S1", "S2"]
    events = [_fixation("S1", 400), _fixation("S1", 200), _fixation("S2", 200)]

    result = fuse_session(events, slot_ids, mode="webcam")

    assert result == pytest.approx({"S1": 0.375, "S2": 0.125})


def test_webcam_fixation_with_null_slot_id_contributes_nothing():
    """A fixation that landed on a shelf (slot_id: null) belongs to no slot.
    It must be dropped BEFORE normalisation, not counted in the denominator.

    fixations: S1 = 300 ms, (null) = 700 ms, S2 = 100 ms.
    Correct:  totals over slots = [300, 100] -> sum 400 -> [0.75, 0.25]
              fused = 0.5*[0.75, 0.25] = [0.375, 0.125]
    Wrong (null folded into the denominator):
              [300/1100, 100/1100] -> fused = [0.13636..., 0.04545...]

    Asserting the exact numbers distinguishes the two.
    """
    slot_ids = ["S1", "S2"]
    events = [_fixation("S1", 300), _fixation(None, 700), _fixation("S2", 100)]

    result = fuse_session(events, slot_ids, mode="webcam")

    assert result == pytest.approx({"S1": 0.375, "S2": 0.125})


def test_webcam_session_with_no_fixations_does_not_renormalise_the_weights():
    """A webcam session that produced no fixation events keeps the 0.3/0.2
    weights as they are -- they are NOT rescaled to fill the missing 0.5.
    The fused vector therefore sums to 0.5, and that is deliberate: it
    records that this session carried less signal.

    cursor dwell    = [300, 100] -> sum 400 -> cursor_norm = [0.75, 0.25]
    interaction max = [1.0, 0.5] -> sum 1.5 -> int_norm    = [2/3, 1/3]

    att = 0.3*cursor_norm + 0.2*int_norm   (fix_norm is all zero)
      S1: 0.3*0.75 + 0.2*(2/3) = 0.225 + 2/15 = 27/120 + 16/120 = 43/120 = 0.358333...
      S2: 0.3*0.25 + 0.2*(1/3) = 0.075 + 1/15 =  9/120 +  8/120 = 17/120 = 0.141666...
    sum = 60/120 = 0.5, which is 0.3 + 0.2 -- NOT 1.0.

    (If the weights were renormalised to 0.6/0.4 the answers would be
    0.6*0.75 + 0.4*(2/3) = 0.71666... and 0.6*0.25 + 0.4*(1/3) = 0.28333...,
    summing to 1.0. The assertions below rule that out.)
    """
    slot_ids = ["S1", "S2"]
    events = [
        _event("cursor_dwell", "S1", dur_ms=300),
        _event("cursor_dwell", "S2", dur_ms=100),
        _event("pickup", "S1"),
        _event("hover", "S2"),
    ]

    result = fuse_session(events, slot_ids, mode="webcam")

    assert result == pytest.approx({"S1": 43 / 120, "S2": 17 / 120})
    assert sum(result.values()) == pytest.approx(0.5)
    assert not any(math.isnan(v) for v in result.values())


def test_webcam_with_only_fixations_has_no_nan():
    """Neither cursor nor interaction events: both those components normalise
    to all-zero (guarded), never NaN, and the result is 0.5*fix_norm alone.

    fix totals = [100, 300] -> sum 400 -> [0.25, 0.75] -> fused [0.125, 0.375]
    """
    slot_ids = ["S1", "S2"]
    events = [_fixation("S1", 100), _fixation("S2", 300)]

    result = fuse_session(events, slot_ids, mode="webcam")

    assert result == pytest.approx({"S1": 0.125, "S2": 0.375})
    assert not any(math.isnan(v) for v in result.values())


def test_webcam_empty_event_list_is_all_zero_not_nan():
    slot_ids = ["S1", "S2", "S3"]

    result = fuse_session([], slot_ids, mode="webcam")

    assert result == {"S1": 0.0, "S2": 0.0, "S3": 0.0}
    assert not any(math.isnan(v) for v in result.values())


def test_webcam_drops_fixations_on_unknown_slots():
    """A fixation naming a slot outside slot_ids is dropped, exactly like an
    unknown-slot cursor_dwell, and must not enter the denominator.

    fixations: S1 = 100 ms, S999 (unknown) = 900 ms.
    Correct: totals = [100, 0] -> sum 100 -> [1.0, 0.0] -> fused [0.5, 0.0]
    (If S999's 900 ms reached the denominator, S1 would be 0.5*0.1 = 0.05.)
    """
    slot_ids = ["S1", "S2"]
    events = [_fixation("S1", 100), _fixation("S999", 900)]

    result = fuse_session(events, slot_ids, mode="webcam")

    assert result == pytest.approx({"S1": 0.5, "S2": 0.0})


@pytest.mark.parametrize("bad_mode", ["nonsense", "webcam_only", "cursor", "WEBCAM", "", None])
def test_unknown_mode_raises_value_error(bad_mode):
    """`mode` accepts only the two values in schemas/session.schema.json's
    enum. Anything else is a caller bug and must fail loudly rather than
    silently fusing with the wrong weights.
    """
    with pytest.raises(ValueError):
        fuse_session([], ["S1"], mode=bad_mode)


# ===========================================================================
# fuse_synthetic -- the synthetic counterpart of fuse_session
# ===========================================================================
#
# The real side of the ShopperTwin comparison fuses looking AND interaction;
# the synthetic side it is scored against used to be the population SimResult
# raw `fixation_prob`, which models looking only. Correlating "looking plus
# touching and buying" against "looking only" is not like-for-like, and
# calibration silently absorbed the difference into the persona shares
# (measured in analytics/tests/test_calibration.py).
#
# `fuse_synthetic` closes that gap: it gives the synthetic side a matching
# interaction channel built from the SimResult `purchase_share`, mapped to
# slots through the RESOLVED PLANOGRAM IT IS PASSED -- a sku moves between
# slots from variant to variant, so the map is never assumed.
#
# The synthetic side has one looking channel where the real side has two, so
# the two real looking weights collapse onto fixation_prob and the interaction
# weight is carried across unchanged:
#
#     cursor_only: 0.7 * fixation_prob_norm + 0.3 * synth_interaction_norm
#     webcam:      0.8 * fixation_prob_norm + 0.2 * synth_interaction_norm
#                  (0.5 + 0.3 looking, 0.2 interaction)
#
# Those numbers are DERIVED from _MODE_WEIGHTS, never a second copy of the
# table -- asserted by test_synthetic_weights_are_derived_from_the_real_ones.


def _planogram(slot_to_sku: dict) -> dict:
    """A minimal planogram shaped like schemas/planogram.schema.json, carrying
    only what `fuse_synthetic` reads: bays -> shelves -> slots -> sku_id."""
    return {
        "planogram_id": "fake",
        "bays": [
            {
                "bay_id": "B1",
                "shelves": [
                    {
                        "shelf_id": "B1S1",
                        "slots": [
                            {"slot_id": slot_id, "sku_id": sku_id, "facings": 1}
                            for slot_id, sku_id in slot_to_sku.items()
                        ],
                    }
                ],
                "ad_slots": [],
            }
        ],
        "skus": [],
    }


def _resolved_variant(variant_id: str) -> dict:
    base = json.loads(
        (ROOT / "data" / "planograms" / "demo_aisle.json").read_text(encoding="utf-8")
    )
    variant = json.loads(
        (ROOT / "data" / "variants" / f"{variant_id}.json").read_text(encoding="utf-8")
    )
    return resolve(base, variant)


def _occupied_slot_ids(planogram: dict) -> list:
    return [
        slot["slot_id"]
        for bay in planogram["bays"]
        for shelf in bay["shelves"]
        for slot in shelf["slots"]
        if slot["sku_id"] is not None
    ]


def test_fuse_synthetic_worked_example_cursor_only():
    """Hand-computed, worked out before the assertion.

    slot_ids = [S1, S2, S3]; S1 holds SKU_A, S2 holds SKU_B, S3 holds SKU_C.

    fixation_prob = {S1: 0.30, S2: 0.10, S3: 0.10, AD1: 0.50}
      AD1 is an ad slot, not in slot_ids, so it is dropped and does NOT enter
      the denominator: totals = [0.30, 0.10, 0.10] -> sum 0.50
      -> fix_norm = [0.6, 0.2, 0.2]

    purchase_share = {SKU_A: 0.25, SKU_B: 0.75}
      -> per slot [0.25, 0.75, 0.0] -> sum 1.0 -> int_norm = [0.25, 0.75, 0.0]

    att = 0.7*fix_norm + 0.3*int_norm
      S1: 0.7*0.6 + 0.3*0.25 = 0.42 + 0.075 = 0.495
      S2: 0.7*0.2 + 0.3*0.75 = 0.14 + 0.225 = 0.365
      S3: 0.7*0.2 + 0.3*0.00 = 0.14 + 0.0   = 0.140
    """
    slot_ids = ["S1", "S2", "S3"]
    planogram = _planogram({"S1": "SKU_A", "S2": "SKU_B", "S3": "SKU_C"})
    sim_result = {
        "fixation_prob": {"S1": 0.30, "S2": 0.10, "S3": 0.10, "AD1": 0.50},
        "purchase_share": {"SKU_A": 0.25, "SKU_B": 0.75},
    }

    result = fuse_synthetic(sim_result, planogram, slot_ids)

    assert result == pytest.approx({"S1": 0.495, "S2": 0.365, "S3": 0.140})
    # Both components normalise to 1 and the weights sum to 1, so this does too.
    assert sum(result.values()) == pytest.approx(1.0)


def test_fuse_synthetic_worked_example_webcam():
    """Same inputs, webcam weights: the two real looking weights (0.5 + 0.3)
    collapse onto fixation_prob and the 0.2 interaction weight is unchanged.

    att = 0.8*fix_norm + 0.2*int_norm, with fix_norm = [0.6, 0.2, 0.2] and
    int_norm = [0.25, 0.75, 0.0] as computed above.
      S1: 0.8*0.6 + 0.2*0.25 = 0.48 + 0.05 = 0.53
      S2: 0.8*0.2 + 0.2*0.75 = 0.16 + 0.15 = 0.31
      S3: 0.8*0.2 + 0.2*0.00 = 0.16 + 0.0  = 0.16
    """
    slot_ids = ["S1", "S2", "S3"]
    planogram = _planogram({"S1": "SKU_A", "S2": "SKU_B", "S3": "SKU_C"})
    sim_result = {
        "fixation_prob": {"S1": 0.30, "S2": 0.10, "S3": 0.10, "AD1": 0.50},
        "purchase_share": {"SKU_A": 0.25, "SKU_B": 0.75},
    }

    result = fuse_synthetic(sim_result, planogram, slot_ids, mode="webcam")

    assert result == pytest.approx({"S1": 0.53, "S2": 0.31, "S3": 0.16})
    assert sum(result.values()) == pytest.approx(1.0)


def test_fuse_synthetic_with_no_purchases_does_not_sum_to_one():
    """The same honest behaviour `fuse_session` has for a missing component:
    the weights are NOT renormalised to compensate.

    fixation_prob = {S1: 0.30, S2: 0.10, S3: 0.10} -> fix_norm = [0.6, 0.2, 0.2]
    purchase_share = {} -> int_norm = [0, 0, 0] (guarded division, not NaN)

    att = 0.7*fix_norm = [0.42, 0.14, 0.14], which sums to 0.7, not 1.0.
    """
    slot_ids = ["S1", "S2", "S3"]
    planogram = _planogram({"S1": "SKU_A", "S2": "SKU_B", "S3": "SKU_C"})
    sim_result = {
        "fixation_prob": {"S1": 0.30, "S2": 0.10, "S3": 0.10},
        "purchase_share": {},
    }

    result = fuse_synthetic(sim_result, planogram, slot_ids)

    assert result == pytest.approx({"S1": 0.42, "S2": 0.14, "S3": 0.14})
    assert sum(result.values()) == pytest.approx(0.7)
    assert not any(math.isnan(value) for value in result.values())


def test_fuse_synthetic_with_no_fixations_is_the_interaction_component_alone():
    """The mirror case: nothing looked at, so 0.3*int_norm alone, summing to 0.3."""
    slot_ids = ["S1", "S2", "S3"]
    planogram = _planogram({"S1": "SKU_A", "S2": "SKU_B", "S3": "SKU_C"})
    sim_result = {"fixation_prob": {}, "purchase_share": {"SKU_A": 0.25, "SKU_B": 0.75}}

    result = fuse_synthetic(sim_result, planogram, slot_ids)

    assert result == pytest.approx({"S1": 0.075, "S2": 0.225, "S3": 0.0})
    assert not any(math.isnan(value) for value in result.values())


def test_fuse_synthetic_of_an_empty_sim_result_is_all_zero_not_nan():
    slot_ids = ["S1", "S2"]
    planogram = _planogram({"S1": "SKU_A", "S2": "SKU_B"})

    result = fuse_synthetic({"fixation_prob": {}, "purchase_share": {}}, planogram, slot_ids)

    assert result == {"S1": 0.0, "S2": 0.0}
    assert not any(math.isnan(value) for value in result.values())


def test_fuse_synthetic_slots_absent_from_purchase_share_are_zero_not_missing():
    """Every slot in the vocabulary is a key in the output, exactly as in
    `fuse_session`, so the vector stays aligned with whatever it is compared
    against. A slot whose sku nobody bought and nobody looked at is 0.0."""
    slot_ids = ["S1", "S2", "S3", "S4"]
    planogram = _planogram({"S1": "SKU_A", "S2": "SKU_B", "S3": "SKU_C", "S4": "SKU_D"})
    sim_result = {"fixation_prob": {"S1": 1.0}, "purchase_share": {"SKU_A": 1.0}}

    result = fuse_synthetic(sim_result, planogram, slot_ids)

    assert set(result) == set(slot_ids)
    assert result["S2"] == 0.0
    assert result["S3"] == 0.0
    assert result["S4"] == 0.0
    assert result["S1"] == pytest.approx(1.0)


def test_fuse_synthetic_credits_a_moved_sku_to_its_new_slot():
    """A sku moves between slots from variant to variant, so the sku -> slot
    map must come from the planogram passed in, never from a fixed table.

    Variant B moves SKU_008 from the bottom shelf to eye level
    (B1S5P1 -> B1S3P2). With a purchase_share of exactly {SKU_008: 1.0} and no
    fixations, all 0.3 of the interaction weight must land on B1S5P1 under
    variant A and on B1S3P2 under variant B.

    Both are fused over the UNION of the two variants' occupied slots so the
    two vectors are keyed identically and the move is unambiguous: B1S3P2 is
    an empty slot under A and B1S5P1 is an empty slot under B (CLAUDE.md:
    empty shelf positions are real slot objects), and an empty slot holds no
    sku, so it takes no purchase share.
    """
    variant_a = _resolved_variant("A")
    variant_b = _resolved_variant("B")
    slot_ids = sorted(set(_occupied_slot_ids(variant_a)) | set(_occupied_slot_ids(variant_b)))
    sim_result = {"fixation_prob": {}, "purchase_share": {"SKU_008": 1.0}}

    under_a = fuse_synthetic(sim_result, variant_a, slot_ids)
    under_b = fuse_synthetic(sim_result, variant_b, slot_ids)

    assert under_a["B1S5P1"] == pytest.approx(0.3)
    assert under_a["B1S3P2"] == 0.0
    assert under_b["B1S3P2"] == pytest.approx(0.3)
    assert under_b["B1S5P1"] == 0.0


def test_synthetic_weights_are_derived_from_the_real_ones():
    """One weight table, not two. The synthetic split is the real table two
    looking weights summed, and its interaction weight unchanged."""
    for mode, (fixation, cursor, interaction) in _MODE_WEIGHTS.items():
        assert synthetic_weights(mode) == (fixation + cursor, interaction)

    assert synthetic_weights("cursor_only") == pytest.approx((0.7, 0.3))
    assert synthetic_weights("webcam") == pytest.approx((0.8, 0.2))


def test_retuning_the_real_weights_moves_the_synthetic_ones(monkeypatch):
    """The coupling is the whole point: if someone retunes the real fusion
    weights, the synthetic side must follow automatically rather than keeping
    a stale second copy of the numbers.

    Retuned cursor_only = (fixation 0.0, cursor 0.5, interaction 0.5)
      -> synthetic 0.5 looking / 0.5 interaction
      fix_norm [0.6, 0.2, 0.2], int_norm [0.25, 0.75, 0.0]
      S1: 0.5*0.6 + 0.5*0.25 = 0.300 + 0.125 = 0.425
      S2: 0.5*0.2 + 0.5*0.75 = 0.100 + 0.375 = 0.475
      S3: 0.5*0.2 + 0.5*0.00 = 0.100 + 0.0   = 0.100
    """
    monkeypatch.setattr(
        "analytics.fusion._MODE_WEIGHTS",
        {"cursor_only": (0.0, 0.5, 0.5), "webcam": (0.3, 0.3, 0.4)},
    )
    slot_ids = ["S1", "S2", "S3"]
    planogram = _planogram({"S1": "SKU_A", "S2": "SKU_B", "S3": "SKU_C"})
    sim_result = {
        "fixation_prob": {"S1": 0.30, "S2": 0.10, "S3": 0.10},
        "purchase_share": {"SKU_A": 0.25, "SKU_B": 0.75},
    }

    assert synthetic_weights("cursor_only") == pytest.approx((0.5, 0.5))
    assert synthetic_weights("webcam") == pytest.approx((0.6, 0.4))
    assert fuse_synthetic(sim_result, planogram, slot_ids) == pytest.approx(
        {"S1": 0.425, "S2": 0.475, "S3": 0.100}
    )


@pytest.mark.parametrize("bad_mode", ["nonsense", "webcam_only", "cursor", "WEBCAM", "", None])
def test_fuse_synthetic_rejects_an_unknown_mode(bad_mode):
    with pytest.raises(ValueError):
        fuse_synthetic(
            {"fixation_prob": {}, "purchase_share": {}},
            _planogram({"S1": "SKU_A"}),
            ["S1"],
            mode=bad_mode,
        )


def test_fuse_synthetic_ignores_skus_that_sit_outside_the_vocabulary():
    """A sku in a slot that is not part of the comparison vocabulary (or in no
    slot at all) contributes nothing and must not enter the denominator.

    purchase_share = {SKU_A: 0.5, SKU_C: 0.5} but only S1 (SKU_A) is in
    slot_ids, so per-slot totals = [0.5] -> normalised [1.0] -> 0.3.
    (If SKU_C 0.5 reached the denominator, S1 would be 0.15.)
    """
    slot_ids = ["S1"]
    planogram = _planogram({"S1": "SKU_A", "S2": "SKU_C"})
    sim_result = {"fixation_prob": {}, "purchase_share": {"SKU_A": 0.5, "SKU_C": 0.5}}

    assert fuse_synthetic(sim_result, planogram, slot_ids) == pytest.approx({"S1": 0.3})


def test_purchase_slot_matrix_splits_a_sku_that_holds_two_slots():
    """The planogram model gives a sku one slot (api/app/resolve.py move_sku
    relies on it), but the credit rule is total-preserving either way: a sku
    listed in two slots has its purchase share divided between them rather
    than counted twice."""
    planogram = _planogram({"S1": "SKU_A", "S2": "SKU_A", "S3": "SKU_B"})

    matrix = purchase_slot_matrix(planogram, ["S1", "S2", "S3"], ["SKU_A", "SKU_B"])

    assert matrix.tolist() == [[0.5, 0.5, 0.0], [0.0, 0.0, 1.0]]
    assert matrix.sum(axis=1).tolist() == [1.0, 1.0]


def test_normalise_rows_matches_the_scalar_normaliser():
    """`_normalise_rows` is the array form of `_normalise` -- same rule,
    including the guarded zero row -- so the synthetic side and the real side
    normalise identically."""
    slot_ids = ["S1", "S2", "S3"]
    cases = [
        {"S1": 300.0, "S2": 100.0, "S3": 0.0},
        {"S1": 0.0, "S2": 0.0, "S3": 0.0},
        {"S1": 1.0, "S2": 1.0, "S3": 1.0},
    ]

    rows = _normalise_rows(
        np.array([[case[slot_id] for slot_id in slot_ids] for case in cases], dtype=float)
    )

    for index, case in enumerate(cases):
        expected = _normalise(case, slot_ids)
        assert rows[index].tolist() == pytest.approx([expected[s] for s in slot_ids])
    assert not np.isnan(rows).any()
