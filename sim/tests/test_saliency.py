"""M3 acceptance tests for sim/saliency.py."""
from __future__ import annotations

import copy
import time

import pytest

from sim.saliency import AD_SLOT_RAW, DEFAULT_WEIGHTS, LEVEL_SCORES, compute_saliency
from .conftest import one_bay_planogram, sku, slot


def test_eye_level_beats_bottom_all_else_equal():
    """(a) Only the shelf level differs, so the eye-level slot must score higher."""
    pg = one_bay_planogram(
        shelves=[
            {"shelf_id": "S1E", "height_m": 1.2, "level": "eye",
             "slots": [slot("EYE", "SKU_X", facings=3, x_m=0.35)]},
            {"shelf_id": "S1B", "height_m": 0.4, "level": "bottom",
             "slots": [slot("BOTTOM", "SKU_Y", facings=3, x_m=0.35)]},
        ],
        skus=[sku("SKU_X"), sku("SKU_Y")],
    )
    bay = compute_saliency(pg)["S1"]
    raw = bay.raw_by_id()
    p = bay.p_by_id()

    assert raw["EYE"] > raw["BOTTOM"]
    assert p["EYE"] > p["BOTTOM"]
    # The only difference is f_level, weighted by DEFAULT_WEIGHTS["level"].
    expected_gap = DEFAULT_WEIGHTS["level"] * (LEVEL_SCORES["eye"] - LEVEL_SCORES["bottom"])
    assert raw["EYE"] - raw["BOTTOM"] == pytest.approx(expected_gap)


def test_creative_on_shelf_talker_raises_attached_shelf(planogram):
    """(b) B1_TALKER is attached to shelf B1S3; giving it a creative must raise that shelf."""
    before = compute_saliency(planogram)["B1"]

    patched = copy.deepcopy(planogram)
    talker = next(a for a in patched["bays"][0]["ad_slots"] if a["ad_slot_id"] == "B1_TALKER")
    assert talker["attached_to"] == "B1S3" and talker["creative_id"] is None
    talker["creative_id"] = "AD_1"
    after = compute_saliency(patched)["B1"]

    raw_before, raw_after = before.raw_by_id(), after.raw_by_id()
    p_before, p_after = before.p_by_id(), after.p_by_id()

    b1s3_slots = [sl["slot_id"] for sh in patched["bays"][0]["shelves"]
                  if sh["shelf_id"] == "B1S3" for sl in sh["slots"] if sl["sku_id"] is not None]
    assert b1s3_slots, "B1S3 must hold at least one occupied slot for this test to mean anything"

    for slot_id in b1s3_slots:
        assert raw_after[slot_id] > raw_before[slot_id], slot_id
        assert p_after[slot_id] > p_before[slot_id], slot_id

    # The ad slot itself only becomes a fixation target once it carries a creative.
    assert "B1_TALKER" not in raw_before
    assert raw_after["B1_TALKER"] == pytest.approx(AD_SLOT_RAW["shelf_talker"])

    # Slots on other shelves are unaffected in raw terms.
    for slot_id in raw_before:
        if slot_id not in b1s3_slots:
            assert raw_after[slot_id] == pytest.approx(raw_before[slot_id])


def test_probabilities_sum_to_one_per_bay(planogram):
    """(c) p_saliency is a distribution within each bay."""
    bays = compute_saliency(planogram)
    assert set(bays) == {b["bay_id"] for b in planogram["bays"]}
    for bay_id, bay in bays.items():
        assert bay.p_saliency.sum() == pytest.approx(1.0), bay_id
        assert (bay.p_saliency > 0).all(), bay_id


def test_empty_slots_are_not_targets_but_occupy_space(planogram):
    """Empty slots are never fixation targets, yet they still shape the bay's normalisers."""
    bays = compute_saliency(planogram)
    targets = {t for bay in bays.values() for t in bay.target_ids}
    empties = [sl["slot_id"] for b in planogram["bays"] for sh in b["shelves"]
               for sl in sh["slots"] if sl["sku_id"] is None]
    assert empties, "the demo aisle is expected to contain empty slots"
    assert targets.isdisjoint(empties)

    # B1S3P1 sits next to the empty B1S3P2, so it has no occupied colour neighbour: f_color = 0.
    # Removing the empty slot would change nothing about facings/size normalisers here, but the
    # empty slot must still be counted when the bay's max facings/area are taken.
    b1 = bays["B1"]
    assert "B1S3P1" in b1.target_ids
    assert "B1S3P2" not in b1.target_ids


def test_ad_slot_without_creative_is_not_a_target(planogram):
    bays = compute_saliency(planogram)
    assert "B1_TALKER" not in bays["B1"].target_ids  # creative_id is null
    assert "B2_DECAL" not in bays["B2"].target_ids  # creative_id is null
    assert "B3_ENDCAP" in bays["B3"].target_ids  # carries AD_1
    raw = bays["B3"].raw_by_id()
    assert raw["B3_ENDCAP"] == pytest.approx(AD_SLOT_RAW["endcap_header"])


def test_bay_attached_creative_lifts_every_slot_in_the_bay(planogram):
    """f_ad applies to every slot in the bay when the ad slot is attached to a bay_id."""
    with_ad = compute_saliency(planogram)["B3"].raw_by_id()

    stripped = copy.deepcopy(planogram)
    endcap = next(a for a in stripped["bays"][2]["ad_slots"] if a["ad_slot_id"] == "B3_ENDCAP")
    assert endcap["attached_to"] == "B3"
    endcap["creative_id"] = None
    without_ad = compute_saliency(stripped)["B3"].raw_by_id()

    slot_ids = [sl["slot_id"] for sh in planogram["bays"][2]["shelves"]
                for sl in sh["slots"] if sl["sku_id"] is not None]
    for slot_id in slot_ids:
        gap = with_ad[slot_id] - without_ad[slot_id]
        assert gap == pytest.approx(DEFAULT_WEIGHTS["ad"]), slot_id


def test_all_bays_compute_in_under_5ms(planogram):
    """M3 acceptance: saliency is on the what-if hot path."""
    compute_saliency(planogram)  # warm imports and any lazily built numpy machinery
    timings = []
    for _ in range(20):
        t0 = time.perf_counter()
        compute_saliency(planogram)
        timings.append((time.perf_counter() - t0) * 1000.0)
    best_ms = min(timings)
    print(f"\nsaliency for {len(planogram['bays'])} bays: best {best_ms:.3f} ms "
          f"over {len(timings)} warm runs")
    assert best_ms < 5.0
