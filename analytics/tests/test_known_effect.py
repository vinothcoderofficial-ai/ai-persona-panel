"""S19 -- the known-effect check: does moving SKU_008 to eye level move
attention the same way for both panels?

The test that matters most here is
`test_focal_slot_is_looked_up_per_variant`: variant B moves the focal SKU out
of B1S5P1 and into B1S3P2, so a module that hard-codes one slot id measures an
*empty shelf position* on one side of the comparison and reports a confident
number for a quantity nobody asked about. That test is built so a fixed-slot
implementation cannot pass it under either choice of fixed slot.
"""

import json
from pathlib import Path

import pytest

from analytics.known_effect import (
    REAL,
    SYNTH,
    focal_slot,
    known_effect,
    panel_uplift,
    same_direction,
    to_metrics_block,
)
from api.app.resolve import resolve

ROOT = Path(__file__).resolve().parents[2]

# The focal SKU of the whole experiment: data/variants/B.json moves it from the
# bottom shelf to eye level, which is the effect the panels are asked to
# reproduce.
FOCAL_SKU = "SKU_008"
FOCAL_SLOT_A = "B1S5P1"
FOCAL_SLOT_B = "B1S3P2"


def _resolved(variant_id: str) -> dict:
    base = json.loads((ROOT / "data" / "planograms" / "demo_aisle.json").read_text(encoding="utf-8"))
    variant = json.loads((ROOT / "data" / "variants" / f"{variant_id}.json").read_text(encoding="utf-8"))
    return resolve(base, variant)


# ---------------------------------------------------------------------------
# The uplift arithmetic
# ---------------------------------------------------------------------------


def test_uplift_is_hand_computed_for_both_panels():
    # real: 0.10 -> 0.15 is +50 %. synth: 0.20 -> 0.25 is +25 %.
    att_a = {REAL: {"S_A": 0.10, "S_B": 0.0}, SYNTH: {"S_A": 0.20, "S_B": 0.0}}
    att_b = {REAL: {"S_A": 0.0, "S_B": 0.15}, SYNTH: {"S_A": 0.0, "S_B": 0.25}}

    result = known_effect(att_a, att_b, "S_A", "S_B")

    assert result["real_uplift"] == pytest.approx(0.5)
    assert result["synth_uplift"] == pytest.approx(0.25)
    assert result["same_direction"] is True


def test_same_direction_is_false_when_the_panels_disagree():
    # real rises, synthetic falls -- the personas got the direction wrong.
    att_a = {REAL: {"S_A": 0.10}, SYNTH: {"S_A": 0.20}}
    att_b = {REAL: {"S_B": 0.15}, SYNTH: {"S_B": 0.05}}

    result = known_effect(att_a, att_b, "S_A", "S_B")

    assert result["real_uplift"] == pytest.approx(0.5)
    assert result["synth_uplift"] == pytest.approx(-0.75)
    assert result["same_direction"] is False


def test_both_panels_falling_still_counts_as_the_same_direction():
    att_a = {REAL: {"S_A": 0.10}, SYNTH: {"S_A": 0.20}}
    att_b = {REAL: {"S_B": 0.05}, SYNTH: {"S_B": 0.10}}

    result = known_effect(att_a, att_b, "S_A", "S_B")

    assert result["real_uplift"] == pytest.approx(-0.5)
    assert result["synth_uplift"] == pytest.approx(-0.5)
    assert result["same_direction"] is True


def test_a_slot_absent_from_the_variant_b_vector_reads_as_zero_attention():
    # -100 %: the focal slot got nothing at all under B. That is a real
    # measurement, not a missing one.
    att_a = {REAL: {"S_A": 0.10}, SYNTH: {"S_A": 0.20}}
    att_b = {REAL: {}, SYNTH: {"S_B": 0.30}}

    result = known_effect(att_a, att_b, "S_A", "S_B")

    assert result["real_uplift"] == pytest.approx(-1.0)
    assert result["synth_uplift"] == pytest.approx(0.5)
    assert result["same_direction"] is False


# ---------------------------------------------------------------------------
# THE correctness detail: the focal slot is looked up per variant
# ---------------------------------------------------------------------------


def test_focal_slot_reads_the_resolved_planogram_not_a_constant():
    resolved_a = _resolved("A")
    resolved_b = _resolved("B")

    assert focal_slot(resolved_a, FOCAL_SKU) == FOCAL_SLOT_A
    assert focal_slot(resolved_b, FOCAL_SKU) == FOCAL_SLOT_B
    # ...and B1S3P2 really is an empty shelf position under A, which is what
    # makes a fixed slot id dangerous rather than merely untidy.
    slots_a = {
        slot["slot_id"]: slot
        for bay in resolved_a["bays"]
        for shelf in bay["shelves"]
        for slot in shelf["slots"]
    }
    assert slots_a[FOCAL_SLOT_B]["sku_id"] is None
    assert slots_a[FOCAL_SLOT_B]["facings"] == 0


def test_focal_slot_is_none_for_a_sku_that_is_not_on_the_shelf():
    assert focal_slot(_resolved("A"), "SKU_DOES_NOT_EXIST") is None


def test_focal_slot_is_looked_up_per_variant():
    """A fixed-slot implementation fails this, whichever slot it fixes on.

    Attention under A sits on B1S5P1 (0.02) and under B on B1S3P2 (0.08), so:

      * per-variant lookup  -> (0.08 - 0.02) / 0.02 = +3.0
      * fixed at B1S5P1     -> (0.00 - 0.02) / 0.02 = -1.0
      * fixed at B1S3P2     -> (0.08 - 0.00) / 0.00 = undefined

    None of the three agree, so the assertion below can only be satisfied by
    looking the focal SKU up in each variant's own resolved planogram.
    """
    resolved_a = _resolved("A")
    resolved_b = _resolved("B")

    att_a = {
        REAL: {FOCAL_SLOT_A: 0.03, "B1S3P1": 0.09},
        SYNTH: {FOCAL_SLOT_A: 0.02, "B1S3P1": 0.11},
    }
    att_b = {
        REAL: {FOCAL_SLOT_B: 0.06, "B1S3P1": 0.09},
        SYNTH: {FOCAL_SLOT_B: 0.08, "B1S3P1": 0.11},
    }

    result = known_effect(
        att_a,
        att_b,
        focal_slot(resolved_a, FOCAL_SKU),
        focal_slot(resolved_b, FOCAL_SKU),
    )

    assert result["synth_uplift"] == pytest.approx(3.0)
    assert result["real_uplift"] == pytest.approx(1.0)
    assert result["same_direction"] is True

    # Spelled out: the wrong answers a fixed slot id would have produced.
    assert panel_uplift(att_a[SYNTH], att_b[SYNTH], FOCAL_SLOT_A, FOCAL_SLOT_A) == pytest.approx(-1.0)
    assert panel_uplift(att_a[SYNTH], att_b[SYNTH], FOCAL_SLOT_B, FOCAL_SLOT_B) is None


# ---------------------------------------------------------------------------
# Guards: nothing here may return inf, nan, or a fabricated zero
# ---------------------------------------------------------------------------


def test_zero_baseline_attention_is_none_not_inf():
    att_a = {REAL: {"S_A": 0.0}, SYNTH: {"S_A": 0.20}}
    att_b = {REAL: {"S_B": 0.15}, SYNTH: {"S_B": 0.25}}

    result = known_effect(att_a, att_b, "S_A", "S_B")

    assert result["real_uplift"] is None
    assert result["synth_uplift"] == pytest.approx(0.25)
    assert result["same_direction"] is None


def test_zero_on_both_sides_is_still_none():
    assert panel_uplift({"S_A": 0.0}, {"S_B": 0.0}, "S_A", "S_B") is None


def test_a_missing_panel_gives_none_rather_than_zero():
    """The empty-real-panel case: no real vector at all is not a real uplift of 0."""
    att_a = {SYNTH: {"S_A": 0.20}}
    att_b = {SYNTH: {"S_B": 0.25}}

    result = known_effect(att_a, att_b, "S_A", "S_B")

    assert result["real_uplift"] is None
    assert result["synth_uplift"] == pytest.approx(0.25)
    assert result["same_direction"] is None


def test_a_missing_focal_slot_gives_none():
    """`focal_slot` returned None (the sku is on no shelf) -- there is nothing
    to measure, and the answer must not be 0.0."""
    att_a = {REAL: {"S_A": 0.10}, SYNTH: {"S_A": 0.20}}
    att_b = {REAL: {"S_B": 0.15}, SYNTH: {"S_B": 0.25}}

    assert known_effect(att_a, att_b, None, "S_B")["real_uplift"] is None
    assert known_effect(att_a, att_b, "S_A", None)["synth_uplift"] is None


def test_same_direction_is_none_safe():
    assert same_direction(None, 0.5) is None
    assert same_direction(0.5, None) is None
    assert same_direction(None, None) is None
    assert same_direction(0.5, 0.25) is True
    assert same_direction(-0.5, -0.25) is True
    assert same_direction(0.5, -0.25) is False
    assert same_direction(0.0, 0.0) is True
    assert same_direction(0.0, 0.25) is False


# ---------------------------------------------------------------------------
# The schema block
# ---------------------------------------------------------------------------


def test_metrics_block_omits_undefined_numbers_rather_than_zeroing_them():
    """schemas/metrics.schema.json types every field of `known_effect` as a
    plain number/boolean and requires none of them, so an undefined uplift is
    an ABSENT key -- never 0.0, and never null in a field typed `number`."""
    block = to_metrics_block({"real_uplift": None, "synth_uplift": 0.25, "same_direction": None})

    assert block == {"synth_uplift": 0.25}
    assert "real_uplift" not in block
    assert "same_direction" not in block


def test_metrics_block_keeps_everything_that_is_defined():
    block = to_metrics_block(
        {"real_uplift": 0.5, "synth_uplift": 0.25, "same_direction": True}
    )
    assert block == {"real_uplift": 0.5, "synth_uplift": 0.25, "same_direction": True}
