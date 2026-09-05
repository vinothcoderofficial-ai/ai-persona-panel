"""S24 -- the placement optimizer: score every placement, rank them, and say
where the placement we are already running comes.

Two tests carry the PLAN's acceptance criteria and are marked as such below:

  * `test_top_pick_beats_the_current_placement` -- the recommendation is worth
    making. It compares the top pick and the *current* placement on the SAME
    objective, and if the current placement already wins it asserts rank 1 and
    says so, rather than passing quietly on a vacuous `>=`.
  * `test_runtime_for_twelve_slots_by_two_creatives_is_under_ninety_seconds` --
    PLAN's budget, measured and printed.

The rest of the file is mostly about the two things that are easy to get
subtly wrong and impossible to notice afterwards:

  * **The current placement is read off the planogram**, by content, not
    assumed to be "the candidate with no patches" and not hardcoded to
    B3_ENDCAP. `test_current_placement_is_identified_by_content_not_by_an_empty_patch_list`
    and `test_current_placement_follows_the_planogram_it_was_given` are built
    so an implementation that does either of those cannot pass.
  * **The uncertainty field is not a confidence interval.** S18 established
    that a committed SimResult cannot support a bootstrap of the synthetic
    panel. `test_seed_spread_is_not_a_confidence_interval` asserts that the
    field's name, its docstring and the printed summary all say what it
    actually is -- Monte Carlo run-to-run spread across seeds.
  * **Which lever actually settles a ranking.** The seed spread is a MIN-MAX
    RANGE, so it grows with the number of seeds it is taken over; only a
    larger `n_synth` narrows it. The pair
    `test_adding_seeds_widens_the_range_and_can_unresolve_a_top_pick` and
    `test_adding_shoppers_narrows_the_range_where_adding_seeds_does_not`
    pins that down, because reading "unresolved at five seeds" as "resolvable
    by running more seeds" is the natural and wrong inference.
  * **A top pick is a function of `n_synth`, not only of the seed.**
    `check_top_pick_stability` re-ranks at a ladder of run sizes and reports
    whether the same candidate wins at each. A ranking whose winner changes
    when the panel grows was never measuring the winner.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import re
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from analytics.optimizer import (
    DEFAULT_SEED,
    DEFAULT_STABILITY_LADDER,
    KIND_AD_PLACEMENT,
    KIND_SKU_LEVEL,
    LEVELS,
    UNPLACED,
    CandidateSet,
    SeedSpread,
    Scored,
    Stability,
    ad_placement_candidates,
    ad_purchase_lift_objective,
    check_top_pick_stability,
    rank_candidates,
    sku_level_candidates,
    sku_purchase_share_objective,
    stability_summary,
    summary,
)

ROOT = Path(__file__).resolve().parents[2]

# The seed planogram's ad furniture, as CLAUDE.md and data/planograms describe
# it: three ad slots, two creatives, and only B3_ENDCAP carrying anything.
AD_SLOTS = ("B1_TALKER", "B2_DECAL", "B3_ENDCAP")
CREATIVES = ("AD_1", "AD_2")
BASELINE_AD_SLOT = "B3_ENDCAP"
BASELINE_CREATIVE = "AD_1"

# The focal SKU of the whole experiment (data/variants/B.json moves it to eye
# level). It sits at B1S5P1, bottom shelf of bay B1.
FOCAL_SKU = "SKU_008"
FOCAL_SLOT = "B1S5P1"
FOCAL_BAY = "B1"
FOCAL_LEVEL = "bottom"
# B1's two empty shelf positions, kept free by the seed generator precisely so
# a level change has somewhere to land.
B1_EYE_EMPTY = "B1S3P2"
B1_BOTTOM_EMPTY = "B1S5P2"


def base_planogram() -> dict:
    path = ROOT / "data" / "planograms" / "demo_aisle.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _slots(planogram: dict) -> dict:
    return {
        slot["slot_id"]: slot
        for bay in planogram["bays"]
        for shelf in bay["shelves"]
        for slot in shelf["slots"]
    }


def _ad_slots(planogram: dict) -> dict:
    return {ad["ad_slot_id"]: ad for bay in planogram["bays"] for ad in bay["ad_slots"]}


def _fake_bundle(purchase_share: dict, exposed: dict, unexposed: dict,
                 sim_run_id: str = "fake") -> SimpleNamespace:
    """The only two things `rank_candidates` reads off a SimBundle."""
    return SimpleNamespace(
        per_persona={},
        population={
            "sim_run_id": sim_run_id,
            "purchase_share": purchase_share,
            "ad_exposed_purchase_share": exposed,
            "ad_unexposed_purchase_share": unexposed,
        },
    )


def _scripted_simulate(scores: dict):
    """A `simulate` stand-in whose population purchase share is dictated by a
    table keyed on (candidate patches digest, seed).

    Used by the tests that are about *ranking* rather than about the simulator:
    ties, determinism and seed spread are properties of optimizer.py, and
    driving them through 10,000 real shoppers would only make them slow and
    harder to control.
    """

    def simulate(resolved, variant_id, *, n_synth, seed):
        value = scores(variant_id, seed) if callable(scores) else scores[(variant_id, seed)]
        return _fake_bundle(
            purchase_share={FOCAL_SKU: value},
            exposed={},
            unexposed={},
            sim_run_id=f"{variant_id}:{seed}",
        )

    return simulate


# ---------------------------------------------------------------------------
# The ad slot x creative space
# ---------------------------------------------------------------------------


def test_ad_space_lists_every_slot_for_every_creative_plus_the_unplaced_option():
    space = ad_placement_candidates(base_planogram())

    assert isinstance(space, CandidateSet)
    ids = [c.candidate_id for c in space.candidates]
    expected = [f"ad:{creative}@{slot}" for creative in CREATIVES for slot in AD_SLOTS]
    expected += [f"ad:{creative}@{UNPLACED}" for creative in CREATIVES]
    assert sorted(ids) == sorted(expected)
    assert len(space) == len(AD_SLOTS) * len(CREATIVES) + len(CREATIVES)
    assert {c.kind for c in space.candidates} == {KIND_AD_PLACEMENT}


def test_ad_candidate_moves_the_creative_rather_than_leaving_it_in_two_places():
    # AD_1 currently hangs on B3_ENDCAP. "AD_1 on B1_TALKER" has to clear the
    # endcap as well, or the resolved planogram shows the creative twice --
    # which is the whole reason data/variants/C.json is two patches, not one.
    space = ad_placement_candidates(base_planogram(), creative_ids=("AD_1",))
    candidate = next(c for c in space.candidates if c.candidate_id == "ad:AD_1@B1_TALKER")

    assert list(candidate.patches) == [
        {"op": "set_ad_creative", "ad_slot_id": "B3_ENDCAP", "creative_id": None},
        {"op": "set_ad_creative", "ad_slot_id": "B1_TALKER", "creative_id": "AD_1"},
    ]


def test_unplacing_a_creative_clears_every_slot_it_hangs_on():
    space = ad_placement_candidates(base_planogram(), creative_ids=("AD_1",))
    candidate = next(c for c in space.candidates if c.candidate_id == f"ad:AD_1@{UNPLACED}")

    assert list(candidate.patches) == [
        {"op": "set_ad_creative", "ad_slot_id": "B3_ENDCAP", "creative_id": None},
    ]
    assert candidate.detail["ad_slot_id"] is None


def test_placing_a_creative_on_an_occupied_ad_slot_records_the_incumbent_it_displaces():
    # An ad slot carries one creative. Hanging AD_2 on the endcap takes AD_1
    # down, and the recommendation has to say so rather than quietly deleting
    # the other campaign.
    space = ad_placement_candidates(base_planogram(), creative_ids=("AD_2",))
    candidate = next(c for c in space.candidates if c.candidate_id == "ad:AD_2@B3_ENDCAP")

    assert candidate.detail["displaced_creative_id"] == "AD_1"
    assert "displaces AD_1" in candidate.label


def test_ad_space_honours_the_caller_s_slot_order_and_rejects_unknown_ids():
    space = ad_placement_candidates(
        base_planogram(), creative_ids=("AD_1",),
        ad_slot_ids=("B3_ENDCAP", "B1_TALKER"), include_unplaced=False,
    )
    assert [c.detail["ad_slot_id"] for c in space.candidates] == ["B3_ENDCAP", "B1_TALKER"]

    with pytest.raises(ValueError, match="B9_NOWHERE"):
        ad_placement_candidates(base_planogram(), ad_slot_ids=("B9_NOWHERE",))
    with pytest.raises(ValueError, match="AD_9"):
        ad_placement_candidates(base_planogram(), creative_ids=("AD_9",))


# ---------------------------------------------------------------------------
# The focal SKU x shelf level space
# ---------------------------------------------------------------------------


def test_sku_level_space_sends_the_sku_to_a_real_destination_slot_on_each_level():
    space = sku_level_candidates(base_planogram(), FOCAL_SKU)

    assert [c.candidate_id for c in space.candidates] == [f"sku:{FOCAL_SKU}@{lvl}" for lvl in LEVELS]
    assert {c.kind for c in space.candidates} == {KIND_SKU_LEVEL}

    slots = _slots(base_planogram())
    destinations = {c.detail["level"]: c.detail["to_slot_id"] for c in space.candidates}
    # Every destination is a slot that exists, and it is in the SKU's own bay.
    for level, to_slot_id in destinations.items():
        assert to_slot_id in slots, level
        assert to_slot_id.startswith(FOCAL_BAY), level

    # Eye level lands on the empty position the seed data keeps free for it.
    assert destinations["eye"] == B1_EYE_EMPTY
    # The SKU's own level is the "leave it alone" candidate: a move onto its
    # own slot, which resolve() treats as a no-op.
    assert destinations[FOCAL_LEVEL] == FOCAL_SLOT
    assert space.skipped == ()


def test_sku_level_candidates_are_move_sku_patches_naming_the_destination():
    space = sku_level_candidates(base_planogram(), FOCAL_SKU)
    candidate = next(c for c in space.candidates if c.detail["level"] == "eye")

    assert list(candidate.patches) == [
        {"op": "move_sku", "sku_id": FOCAL_SKU, "to_slot_id": B1_EYE_EMPTY},
    ]


def test_a_level_with_no_free_slot_becomes_a_documented_swap_not_a_crash():
    # B1's top shelf holds SKU_001 and SKU_002 with nothing free, so "put
    # SKU_008 on the top shelf" can only happen by giving a position up. That
    # is the rule web/src/whatif/patches.ts already follows, and the candidate
    # has to name the SKU it demotes.
    slots = _slots(base_planogram())
    assert slots["B1S1P1"]["sku_id"] == "SKU_001"
    assert slots["B1S1P2"]["sku_id"] == "SKU_002"

    space = sku_level_candidates(base_planogram(), FOCAL_SKU)
    candidate = next(c for c in space.candidates if c.detail["level"] == "top")

    assert candidate.detail["to_slot_id"] == "B1S1P1"
    assert candidate.detail["displaced_sku_id"] == "SKU_001"
    assert "swap" in candidate.label.lower()


def test_a_level_the_bay_does_not_have_is_skipped_with_a_reason():
    planogram = base_planogram()
    bay = next(b for b in planogram["bays"] if b["bay_id"] == FOCAL_BAY)
    bay["shelves"] = [shelf for shelf in bay["shelves"] if shelf["level"] != "top"]

    space = sku_level_candidates(planogram, FOCAL_SKU)

    assert [c.detail["level"] for c in space.candidates] == [
        level for level in LEVELS if level != "top"
    ]
    assert len(space.skipped) == 1
    skipped = space.skipped[0]
    assert skipped.candidate_id == f"sku:{FOCAL_SKU}@top"
    assert "no shelf at level" in skipped.reason
    assert skipped.detail["level"] == "top"


def test_a_shelf_with_no_slots_at_all_is_skipped_with_a_reason():
    planogram = base_planogram()
    bay = next(b for b in planogram["bays"] if b["bay_id"] == FOCAL_BAY)
    next(s for s in bay["shelves"] if s["level"] == "top")["slots"] = []

    space = sku_level_candidates(planogram, FOCAL_SKU)

    assert len(space.skipped) == 1
    assert "no slots" in space.skipped[0].reason


def test_sku_level_space_rejects_a_sku_that_is_on_no_shelf():
    # An unplaced SKU has no bay, so there is no "same bay, that level" to
    # search. Answering with an empty space would read as "no move helps".
    with pytest.raises(ValueError, match="SKU_999"):
        sku_level_candidates(base_planogram(), "SKU_999")


# ---------------------------------------------------------------------------
# Composition: the two spaces stay separable
# ---------------------------------------------------------------------------


def test_the_two_spaces_compose_and_report_the_candidate_count():
    ads = ad_placement_candidates(base_planogram(), creative_ids=("AD_1",))
    skus = sku_level_candidates(base_planogram(), FOCAL_SKU)
    both = ads + skus

    assert len(both) == len(ads) + len(skus)
    assert both.skipped == ads.skipped + skus.skipped
    assert [c.candidate_id for c in both.candidates] == (
        [c.candidate_id for c in ads.candidates] + [c.candidate_id for c in skus.candidates]
    )


def test_an_ad_only_space_never_moves_a_sku_and_a_sku_only_space_never_touches_an_ad():
    ad_ops = {p["op"] for c in ad_placement_candidates(base_planogram()).candidates
              for p in c.patches}
    sku_ops = {p["op"] for c in sku_level_candidates(base_planogram(), FOCAL_SKU).candidates
               for p in c.patches}

    assert ad_ops == {"set_ad_creative"}
    assert sku_ops == {"move_sku"}


def test_duplicate_candidate_ids_are_rejected_rather_than_double_counted():
    space = ad_placement_candidates(base_planogram(), creative_ids=("AD_1",))
    doubled = space + space

    with pytest.raises(ValueError, match="duplicate candidate"):
        rank_candidates(base_planogram(), doubled, sku_purchase_share_objective(FOCAL_SKU),
                        simulate=_scripted_simulate(lambda variant_id, seed: 0.0),
                        spread_seeds=())


# ---------------------------------------------------------------------------
# The current placement is read off the planogram
# ---------------------------------------------------------------------------


def test_current_placement_is_identified_from_the_planogram_as_passed_in():
    space = ad_placement_candidates(base_planogram(), creative_ids=("AD_1",))
    ranking = rank_candidates(
        base_planogram(), space, sku_purchase_share_objective(FOCAL_SKU),
        simulate=_scripted_simulate(lambda variant_id, seed: 0.0), spread_seeds=(),
    )

    assert ranking.current is not None
    assert ranking.current.candidate.detail["ad_slot_id"] == BASELINE_AD_SLOT
    assert ranking.current.candidate.detail["creative_id"] == BASELINE_CREATIVE
    assert 1 <= ranking.current_rank <= ranking.n_candidates


def test_current_placement_is_identified_by_content_not_by_an_empty_patch_list():
    # "AD_1 on B3_ENDCAP" emits a set_ad_creative patch that happens to write
    # back the value already there. Its patch list is NOT empty, and it is
    # still the placement we are running.
    space = ad_placement_candidates(base_planogram(), creative_ids=("AD_1",))
    ranking = rank_candidates(
        base_planogram(), space, sku_purchase_share_objective(FOCAL_SKU),
        simulate=_scripted_simulate(lambda variant_id, seed: 0.0), spread_seeds=(),
    )

    current = ranking.current
    assert current.candidate.patches != ()
    assert current.is_current is True
    assert sum(1 for e in ranking.entries if e.is_current) == 1


def test_current_placement_follows_the_planogram_it_was_given():
    # Hand the optimizer variant C's world -- AD_1 already on the bay 1 shelf
    # talker -- and the "current" row has to move with it.
    planogram = base_planogram()
    ads = _ad_slots(planogram)
    ads["B3_ENDCAP"]["creative_id"] = None
    ads["B1_TALKER"]["creative_id"] = "AD_1"

    ranking = rank_candidates(
        planogram, ad_placement_candidates(planogram, creative_ids=("AD_1",)),
        sku_purchase_share_objective(FOCAL_SKU),
        simulate=_scripted_simulate(lambda variant_id, seed: 0.0), spread_seeds=(),
    )

    assert ranking.current.candidate.detail["ad_slot_id"] == "B1_TALKER"


def test_a_sku_space_marks_the_sku_s_current_level_as_the_current_placement():
    ranking = rank_candidates(
        base_planogram(), sku_level_candidates(base_planogram(), FOCAL_SKU),
        sku_purchase_share_objective(FOCAL_SKU),
        simulate=_scripted_simulate(lambda variant_id, seed: 0.0), spread_seeds=(),
    )

    assert ranking.current.candidate.detail["level"] == FOCAL_LEVEL


# ---------------------------------------------------------------------------
# Determinism and tie-breaking
# ---------------------------------------------------------------------------


def test_ranking_is_deterministic_across_repeated_runs_at_the_same_seed():
    space = ad_placement_candidates(base_planogram())
    # A score that depends on the configuration but not on call order.
    simulate = _scripted_simulate(lambda variant_id, seed: (hash(variant_id) % 97) / 100.0)

    first = rank_candidates(base_planogram(), space, sku_purchase_share_objective(FOCAL_SKU),
                            simulate=simulate, spread_seeds=())
    second = rank_candidates(base_planogram(), space, sku_purchase_share_objective(FOCAL_SKU),
                             simulate=simulate, spread_seeds=())

    assert [(e.rank, e.candidate.candidate_id, e.objective) for e in first.entries] == \
           [(e.rank, e.candidate.candidate_id, e.objective) for e in second.entries]
    assert first.current_rank == second.current_rank


def test_ties_break_by_candidate_id_not_by_generation_order():
    # Generation order is deliberately not the sorted order, and every
    # candidate scores identically, so only the documented tie-break can
    # produce the result.
    space = ad_placement_candidates(
        base_planogram(), creative_ids=("AD_2", "AD_1"),
        ad_slot_ids=("B3_ENDCAP", "B2_DECAL", "B1_TALKER"), include_unplaced=False,
    )
    generated = [c.candidate_id for c in space.candidates]
    assert generated != sorted(generated)

    ranking = rank_candidates(base_planogram(), space, sku_purchase_share_objective(FOCAL_SKU),
                              simulate=_scripted_simulate(lambda variant_id, seed: 0.25),
                              spread_seeds=())

    assert [e.objective for e in ranking.entries] == [0.25] * len(space)
    assert [e.candidate.candidate_id for e in ranking.entries] == sorted(generated)
    assert [e.rank for e in ranking.entries] == list(range(1, len(space) + 1))


def test_an_undefined_objective_ranks_last_and_is_never_scored_as_zero():
    space = ad_placement_candidates(base_planogram(), creative_ids=("AD_1",))
    undefined_id = "ad:AD_1@B1_TALKER"

    def simulate(resolved, variant_id, *, n_synth, seed):
        # An arm with no purchases at all: brand_share -> None -> lift -> None.
        empty = {sku["sku_id"]: 0.0 for sku in resolved["skus"]}
        exposed = dict(empty)
        if _ad_slots(resolved)["B1_TALKER"]["creative_id"] != "AD_1":
            exposed["SKU_001"] = 1.0  # Crunch
        return _fake_bundle(purchase_share=empty, exposed=exposed,
                            unexposed={**empty, "SKU_001": 0.5, "SKU_002": 0.5},
                            sim_run_id=variant_id)

    ranking = rank_candidates(base_planogram(), space, ad_purchase_lift_objective("AD_1"),
                              simulate=simulate, spread_seeds=(43,), spread_top_n=4)

    undefined = next(e for e in ranking.entries if e.candidate.candidate_id == undefined_id)
    assert undefined.objective is None
    assert undefined.rank == ranking.n_candidates
    assert all(e.objective is not None for e in ranking.entries[:-1])
    # An undefined objective has no range to report either -- a spread built
    # from the seeds that happened to be defined would be a different quantity.
    assert undefined.seed_spread is None
    assert ranking.entries[0].seed_spread is not None


# ---------------------------------------------------------------------------
# The uncertainty field: what it is, and what it is not
# ---------------------------------------------------------------------------


def test_seed_spread_is_not_a_confidence_interval():
    scored_fields = {f.name for f in dataclasses.fields(Scored)}
    spread_fields = {f.name for f in dataclasses.fields(SeedSpread)}

    assert "seed_spread" in scored_fields
    forbidden = re.compile(r"ci95|conf|interval", re.IGNORECASE)
    assert not [name for name in scored_fields | spread_fields if forbidden.search(name)]

    # The docstring has to say so out loud, because the field name alone is
    # not what a reader of RESULTS.md will go on.
    doc = SeedSpread.__doc__.lower()
    assert "not a confidence interval" in doc
    assert "monte carlo" in doc


def test_seed_spread_reports_the_range_over_the_seeds_it_actually_ran():
    space = ad_placement_candidates(base_planogram(), creative_ids=("AD_1",),
                                    ad_slot_ids=("B1_TALKER",), include_unplaced=False)
    table = {42: 0.10, 43: 0.30, 44: 0.20}
    ranking = rank_candidates(base_planogram(), space, sku_purchase_share_objective(FOCAL_SKU),
                              simulate=_scripted_simulate(lambda variant_id, seed: table[seed]),
                              seed=42, spread_seeds=(43, 44), spread_top_n=1)

    spread = ranking.entries[0].seed_spread
    assert spread.seeds == (42, 43, 44)
    assert spread.values == (0.10, 0.30, 0.20)
    assert (spread.low, spread.high) == (0.10, 0.30)
    # The headline number stays the primary seed's, not the mean of the spread.
    assert ranking.entries[0].objective == 0.10


def test_overlapping_seed_spreads_are_surfaced_as_unresolved_rankings():
    space = ad_placement_candidates(base_planogram(), creative_ids=("AD_1",))
    # Two candidates whose seed ranges overlap, and one clear winner.
    by_slot = {
        "B1_TALKER": {42: 0.50, 43: 0.44},   # 0.44 - 0.50
        "B2_DECAL": {42: 0.48, 43: 0.52},    # 0.48 - 0.52  -> overlaps B1_TALKER
        "B3_ENDCAP": {42: 0.90, 43: 0.88},   # 0.88 - 0.90  -> overlaps neither
    }

    ranking = rank_candidates(
        base_planogram(), space, sku_purchase_share_objective(FOCAL_SKU),
        simulate=_scripted_simulate(
            lambda variant_id, seed: _lookup(base_planogram(), space, by_slot, variant_id, seed)),
        seed=42, spread_seeds=(43,), spread_top_n=4,
    )

    by_id = {e.candidate.candidate_id: e for e in ranking.entries}
    assert by_id["ad:AD_1@B1_TALKER"].unresolved_against == ("ad:AD_1@B2_DECAL",)
    assert by_id["ad:AD_1@B2_DECAL"].unresolved_against == ("ad:AD_1@B1_TALKER",)
    assert by_id["ad:AD_1@B3_ENDCAP"].unresolved_against == ()
    assert ranking.top_pick_is_resolved is True


def _lookup(planogram, space, by_slot, variant_id, seed):
    """Map a variant_id back to the ad slot its candidate targets."""
    from analytics.optimizer import variant_id_for

    base_hash_ids = {
        variant_id_for(planogram, c.patches): c.detail["ad_slot_id"] for c in space.candidates
    }
    slot = base_hash_ids[variant_id]
    return by_slot.get(slot, {42: 0.01, 43: 0.01})[seed]


def test_the_top_pick_is_reported_unresolved_when_it_overlaps_the_runner_up():
    space = ad_placement_candidates(base_planogram(), creative_ids=("AD_1",))
    by_slot = {
        "B1_TALKER": {42: 0.50, 43: 0.40},
        "B2_DECAL": {42: 0.49, 43: 0.55},
        "B3_ENDCAP": {42: 0.10, 43: 0.10},
    }
    ranking = rank_candidates(
        base_planogram(), space, sku_purchase_share_objective(FOCAL_SKU),
        simulate=_scripted_simulate(
            lambda variant_id, seed: _lookup(base_planogram(), space, by_slot, variant_id, seed)),
        seed=42, spread_seeds=(43,), spread_top_n=4,
    )

    assert ranking.top_pick_is_resolved is False
    assert "not resolved" in summary(ranking).lower()


def test_the_summary_never_calls_the_spread_a_confidence_interval():
    space = ad_placement_candidates(base_planogram(), creative_ids=("AD_1",))
    ranking = rank_candidates(
        base_planogram(), space, sku_purchase_share_objective(FOCAL_SKU),
        simulate=_scripted_simulate(lambda variant_id, seed: 0.1 + seed / 1000.0),
        seed=42, spread_seeds=(43, 44), spread_top_n=3,
    )

    text = summary(ranking)
    lower = text.lower()
    # The phrase may appear, but only ever to disclaim it. PLAN section 6's
    # example sentence prints "(CI 8-14)"; this prints a seed spread and says
    # in the same breath that it is not the other thing.
    assert lower.count("confidence interval") == lower.count("not a confidence interval")
    assert "not a confidence interval" in lower
    assert "seed spread" in lower
    assert "ci95" not in lower
    assert re.search(r"\bCI\b", text) is None
    assert f"ranks {ranking.current_rank}" in text
    assert f"of {ranking.n_candidates}" in text


# ---------------------------------------------------------------------------
# THE ACCEPTANCE TEST (PLAN section 6, S24)
# ---------------------------------------------------------------------------


def test_top_pick_beats_the_current_placement():
    """The optimizer's top pick beats the current placement on the same metric.

    Both numbers are the advertised brand's Ad-to-Purchase Lift, produced by
    `analytics.lift.synth_lift` from the same simulator call that a prediction
    lock uses, so "the same metric" is a fact about the code and not a claim.
    """
    base = base_planogram()
    ranking = rank_candidates(
        base, ad_placement_candidates(base, creative_ids=("AD_1",)),
        ad_purchase_lift_objective("AD_1"), spread_seeds=(), seed=DEFAULT_SEED,
    )

    assert ranking.current is not None, "the current placement must be in the ranking"
    assert ranking.current.candidate.detail["ad_slot_id"] == BASELINE_AD_SLOT
    assert 1 <= ranking.current_rank <= ranking.n_candidates

    best = ranking.best
    assert best.objective is not None
    assert best.objective >= ranking.current.objective

    if best.candidate.candidate_id == ranking.current.candidate.candidate_id:
        # The honest outcome when nothing beats what we already run: say it.
        assert ranking.current_rank == 1
        print("\ncurrent placement is already optimal (rank 1 of "
              f"{ranking.n_candidates})")
    else:
        assert best.objective > ranking.current.objective
        assert ranking.current_rank > 1

    print(f"\n{summary(ranking)}")
    for entry in ranking.entries:
        marker = "  <- current" if entry.is_current else ""
        value = "undefined" if entry.objective is None else f"{entry.objective:+.4f}"
        print(f"  {entry.rank}. {entry.candidate.label:<58} {value}{marker}")


# ---------------------------------------------------------------------------
# THE RUNTIME TEST (PLAN section 6, S24: < 90 s for 12 slots x 2 creatives)
# ---------------------------------------------------------------------------


def _twelve_ad_slot_planogram() -> dict:
    """The seed aisle with four ad slots per bay instead of one.

    `planogram_id` stays `demo_aisle` so the committed persona policies still
    load -- a policy names goal categories and brand affinities, never a slot,
    so extra ad furniture does not invalidate it. Every configuration below is
    therefore a planogram no other test has simulated, which is what makes the
    measured time an honest cold-cache number.
    """
    planogram = copy.deepcopy(base_planogram())
    extra = {"shelf_talker": "S2", "floor_decal": None, "screen": None}
    for bay in planogram["bays"]:
        bay_id = bay["bay_id"]
        for i, (ad_type, shelf_suffix) in enumerate(extra.items()):
            bay["ad_slots"].append({
                "ad_slot_id": f"{bay_id}_EXTRA{i + 1}",
                "type": ad_type,
                "attached_to": bay_id if shelf_suffix is None else f"{bay_id}{shelf_suffix}",
                "x_m": 0.1 * (i + 1),
                "width_m": 0.3,
                "creative_id": None,
            })
    return planogram


def test_runtime_for_twelve_slots_by_two_creatives_is_under_ninety_seconds():
    planogram = _twelve_ad_slot_planogram()
    ad_slot_ids = tuple(_ad_slots(planogram))
    assert len(ad_slot_ids) == 12

    space = ad_placement_candidates(planogram, creative_ids=CREATIVES,
                                    ad_slot_ids=ad_slot_ids, include_unplaced=False)
    assert len(space) == 24

    started = time.perf_counter()
    ranking = rank_candidates(planogram, space, ad_purchase_lift_objective("AD_1"))
    elapsed = time.perf_counter() - started

    print(f"\n24 configurations at n_synth={ranking.n_synth}: {elapsed:.1f} s")
    assert ranking.n_candidates == 24
    assert elapsed < 90.0


# ---------------------------------------------------------------------------
# End to end on the committed seed planogram
# ---------------------------------------------------------------------------


def test_the_full_committed_space_ranks_ad_moves_and_shelf_moves_together():
    base = base_planogram()
    space = ad_placement_candidates(base) + sku_level_candidates(base, FOCAL_SKU)
    ranking = rank_candidates(base, space, ad_purchase_lift_objective("AD_1"))

    assert ranking.n_candidates == len(space)
    assert ranking.objective_name == "ad-to-purchase lift for creative AD_1"
    assert ranking.current is not None
    assert {e.candidate.kind for e in ranking.entries} == {KIND_AD_PLACEMENT, KIND_SKU_LEVEL}
    # Every entry carries the deterministic run id of the simulation it scored,
    # so a reader can reproduce any row of the table.
    assert all(e.sim_run_id for e in ranking.entries)
    assert len({e.sim_run_id for e in ranking.entries}) > 1

    print(f"\n{summary(ranking)}")
    for entry in ranking.entries:
        marker = "  <- current" if entry.is_current else ""
        value = "undefined" if entry.objective is None else f"{entry.objective:+.4f}"
        spread = "" if entry.seed_spread is None else (
            f"  [seeds {entry.seed_spread.low:+.4f}..{entry.seed_spread.high:+.4f}]")
        print(f"  {entry.rank:>2}. {entry.candidate.label:<62} {value}{spread}{marker}")


def test_the_same_configuration_scores_the_same_as_a_direct_simulator_call():
    # The objective is not a second implementation of the lift: it is
    # analytics.lift.synth_lift over the same api.app.simcache call a
    # prediction lock uses. This pins that down rather than trusting it.
    from analytics.lift import creative_brand, sku_brands, synth_lift
    from api.app import simcache
    from api.app.resolve import resolve
    from analytics.optimizer import variant_id_for

    base = base_planogram()
    space = ad_placement_candidates(base, creative_ids=("AD_1",))
    ranking = rank_candidates(base, space, ad_purchase_lift_objective("AD_1"), spread_seeds=())

    entry = ranking.entries[0]
    variant_id = variant_id_for(base, entry.candidate.patches)
    resolved = resolve(base, {"variant_id": variant_id, "base_planogram_id": base["planogram_id"],
                              "name": "check", "patches": list(entry.candidate.patches)})
    bundle = simcache.population(resolved, variant_id, n_synth=ranking.n_synth, seed=ranking.seed)
    direct = synth_lift(bundle.population, brand_of_sku=sku_brands(resolved),
                        brand=creative_brand(resolved, "AD_1"))

    assert entry.objective == direct
    assert entry.sim_run_id == bundle.population["sim_run_id"]


# ---------------------------------------------------------------------------
# Which lever settles a ranking: shoppers, not seeds
# ---------------------------------------------------------------------------


def _two_ad_candidates():
    """Exactly two candidates, so a spread comparison has one pair in it."""
    return ad_placement_candidates(
        base_planogram(), creative_ids=("AD_1",),
        ad_slot_ids=("B1_TALKER", "B2_DECAL"), include_unplaced=False,
    )


def _slot_of_variant(space):
    """`variant_id -> ad_slot_id` for a scripted simulate to score against."""
    from analytics.optimizer import variant_id_for

    return {variant_id_for(base_planogram(), c.patches): c.detail["ad_slot_id"]
            for c in space.candidates}


def _by_slot(space, table):
    """Score a candidate from `table[ad_slot_id][seed]`, via its variant id."""
    slots = _slot_of_variant(space)
    return lambda variant_id, seed: table[slots[variant_id]][seed]


def test_seed_spread_reports_how_many_seeds_and_how_wide_the_range_is():
    # The width and the seed count are the two numbers that make one spread
    # comparable to another. A range over three seeds and a range over twenty
    # are not the same measurement, and `low`/`high` alone cannot say which
    # was taken.
    space = ad_placement_candidates(base_planogram(), creative_ids=("AD_1",),
                                    ad_slot_ids=("B1_TALKER",), include_unplaced=False)
    table = {42: 0.10, 43: 0.30, 44: 0.20}
    ranking = rank_candidates(base_planogram(), space, sku_purchase_share_objective(FOCAL_SKU),
                              simulate=_scripted_simulate(lambda variant_id, seed: table[seed]),
                              seed=42, spread_seeds=(43, 44), spread_top_n=1)

    spread = ranking.entries[0].seed_spread
    assert spread.n_seeds == 3
    assert spread.width == pytest.approx(0.20)


def test_the_seed_spread_docstring_says_more_seeds_does_not_narrow_it():
    # The natural reading of "unresolved at five seeds" is "run more seeds and
    # it will resolve". It is exactly backwards -- min-max is a range
    # statistic and grows with the number of draws -- and the docstring is
    # where a reader of RESULTS.md will go looking.
    doc = SeedSpread.__doc__.lower()
    assert "min" in doc and "max" in doc
    assert "widen" in doc or "wider" in doc or "grows" in doc
    assert "n_synth" in doc


def test_adding_seeds_widens_the_range_and_can_unresolve_a_top_pick():
    # Same simulations, same candidates, same n_synth -- only the number of
    # seeds the range is taken over changes. Two seeds separate the pair;
    # five, drawn from the same table, do not. "Resolved" is therefore partly
    # a statement about how many seeds were run, and the module has to carry
    # the count (`SeedSpread.n_seeds`) for it to mean anything.
    space = _two_ad_candidates()
    table = {
        "B1_TALKER": {42: 0.50, 43: 0.49, 44: 0.40, 45: 0.55, 46: 0.52},
        "B2_DECAL": {42: 0.45, 43: 0.44, 44: 0.47, 45: 0.42, 46: 0.46},
    }
    simulate = _scripted_simulate(_by_slot(space, table))

    def rank_with(seeds):
        return rank_candidates(base_planogram(), space,
                               sku_purchase_share_objective(FOCAL_SKU), simulate=simulate,
                               seed=42, spread_seeds=seeds, spread_top_n=2)

    two = rank_with((43,))
    five = rank_with((43, 44, 45, 46))

    assert two.best.seed_spread.n_seeds == 2
    assert five.best.seed_spread.n_seeds == 5
    # More seeds, a wider range -- never a narrower one.
    assert five.best.seed_spread.width > two.best.seed_spread.width
    # And the extra draws take the answer away rather than settling it.
    assert two.top_pick_is_resolved is True
    assert five.top_pick_is_resolved is False


def test_adding_shoppers_narrows_the_range_where_adding_seeds_does_not():
    # The other lever. `n_synth` is a Monte Carlo sample size, so the range
    # over a FIXED set of seeds shrinks as 1/sqrt(n_synth) -- sixteen times
    # the shoppers, a quarter of the range. That is what actually buys
    # resolution, which is why `check_top_pick_stability` climbs run sizes
    # and not seed counts.
    space = _two_ad_candidates()
    noise = {42: 0.00, 43: 0.06, 44: -0.06, 45: 0.03, 46: -0.03}
    truth = {"B1_TALKER": 0.50, "B2_DECAL": 0.44}
    slots = _slot_of_variant(space)

    def simulate(resolved, variant_id, *, n_synth, seed):
        scale = (10_000 / n_synth) ** 0.5
        value = truth[slots[variant_id]] + noise[seed] * scale
        return _fake_bundle(purchase_share={FOCAL_SKU: value}, exposed={}, unexposed={},
                            sim_run_id=f"{variant_id}:{n_synth}:{seed}")

    def rank_at(n_synth):
        return rank_candidates(base_planogram(), space,
                               sku_purchase_share_objective(FOCAL_SKU), simulate=simulate,
                               n_synth=n_synth, seed=42, spread_seeds=(43, 44, 45, 46),
                               spread_top_n=2)

    widths = {n: rank_at(n).best.seed_spread.width for n in (10_000, 40_000, 160_000)}

    assert widths[10_000] > widths[40_000] > widths[160_000]
    assert widths[10_000] / widths[160_000] == pytest.approx(4.0)
    # And that is what turns an unresolved pair into a resolved one.
    assert rank_at(10_000).top_pick_is_resolved is False
    assert rank_at(160_000).top_pick_is_resolved is True


def test_the_summary_sends_a_reader_to_run_size_rather_than_to_more_seeds():
    space = _two_ad_candidates()
    table = {
        "B1_TALKER": {42: 0.50, 43: 0.40},
        "B2_DECAL": {42: 0.49, 43: 0.55},
    }
    ranking = rank_candidates(base_planogram(), space,
                              sku_purchase_share_objective(FOCAL_SKU),
                              simulate=_scripted_simulate(_by_slot(space, table)),
                              seed=42, spread_seeds=(43,), spread_top_n=2)

    assert ranking.top_pick_is_resolved is False
    text = summary(ranking)
    lower = text.lower()
    assert "not resolved" in lower
    # The wrong lever is named and disclaimed in the same breath, and the
    # right one is named together with the call that measures it.
    assert "more seeds will not settle it" in lower
    assert "n_synth" in lower
    assert "check_top_pick_stability" in text
    # Still no confidence interval, on any branch.
    assert re.search(r"\bCI\b", text) is None


# ---------------------------------------------------------------------------
# Is the top pick a property of the planogram, or of n_synth?
# ---------------------------------------------------------------------------


def _stability_simulate(value_of):
    def simulate(resolved, variant_id, *, n_synth, seed):
        return _fake_bundle(purchase_share={FOCAL_SKU: value_of(variant_id, n_synth)},
                            exposed={}, unexposed={},
                            sim_run_id=f"{variant_id}:{n_synth}:{seed}")
    return simulate


def _flat_values(space, values):
    slots = _slot_of_variant(space)
    return lambda variant_id, n_synth: values[slots[variant_id]]


def test_the_stability_ladder_climbs_run_sizes_and_keeps_one_ranking_per_rung():
    space = _two_ad_candidates()
    check = check_top_pick_stability(
        base_planogram(), space, sku_purchase_share_objective(FOCAL_SKU),
        n_synth_ladder=(10_000, 50_000),
        simulate=_stability_simulate(
            _flat_values(space, {"B1_TALKER": 0.50, "B2_DECAL": 0.40})),
    )

    assert isinstance(check, Stability)
    assert check.n_synth_ladder == (10_000, 50_000)
    assert tuple(r.n_synth for r in check.rankings) == (10_000, 50_000)
    assert all(r.n_candidates == len(space) for r in check.rankings)
    assert check.objective_name == f"population purchase share of {FOCAL_SKU}"


def test_a_top_pick_that_survives_every_rung_is_reported_stable():
    space = _two_ad_candidates()
    check = check_top_pick_stability(
        base_planogram(), space, sku_purchase_share_objective(FOCAL_SKU),
        n_synth_ladder=(10_000, 50_000, 250_000),
        simulate=_stability_simulate(
            _flat_values(space, {"B1_TALKER": 0.50, "B2_DECAL": 0.40})),
    )

    assert check.top_pick_ids == ("ad:AD_1@B1_TALKER",) * 3
    assert check.top_pick_is_stable is True
    assert check.settled_top_pick == "ad:AD_1@B1_TALKER"
    assert check.reordered_at == ()


def test_a_top_pick_that_only_wins_at_the_small_run_size_is_reported_unstable():
    # The failure this check exists for: a candidate that leads at
    # n_synth=10,000 and loses once the panel grows was never the best
    # placement, it was the luckiest one. A seed spread at 10,000 cannot see
    # it, because every seed it re-rolls is drawn at 10,000.
    space = _two_ad_candidates()
    slots = _slot_of_variant(space)

    def value_of(variant_id, n_synth):
        if slots[variant_id] == "B1_TALKER":
            return 0.50 if n_synth < 50_000 else 0.30
        return 0.40

    check = check_top_pick_stability(
        base_planogram(), space, sku_purchase_share_objective(FOCAL_SKU),
        n_synth_ladder=(10_000, 50_000, 250_000),
        simulate=_stability_simulate(value_of),
    )

    assert check.top_pick_ids == ("ad:AD_1@B1_TALKER", "ad:AD_1@B2_DECAL", "ad:AD_1@B2_DECAL")
    assert check.top_pick_is_stable is False
    assert check.settled_top_pick is None
    assert check.reordered_at == (50_000, 250_000)


def test_a_one_rung_ladder_reports_stability_as_unknown_rather_than_true():
    # One rung is one measurement; "the top pick survived every rung it was
    # tried at" is vacuously true and must not be reported as a finding. The
    # module's word for "not established" is None, as it is for
    # `Ranking.top_pick_is_resolved`.
    space = _two_ad_candidates()
    check = check_top_pick_stability(
        base_planogram(), space, sku_purchase_share_objective(FOCAL_SKU),
        n_synth_ladder=(10_000,),
        simulate=_stability_simulate(
            _flat_values(space, {"B1_TALKER": 0.50, "B2_DECAL": 0.40})),
    )

    assert check.top_pick_is_stable is None
    assert check.settled_top_pick is None


def test_the_stability_ladder_must_be_non_empty_and_strictly_increasing():
    space = _two_ad_candidates()
    simulate = _stability_simulate(lambda variant_id, n_synth: 0.5)

    for bad in ((), (10_000, 10_000), (50_000, 10_000)):
        with pytest.raises(ValueError):
            check_top_pick_stability(
                base_planogram(), space, sku_purchase_share_objective(FOCAL_SKU),
                n_synth_ladder=bad, simulate=simulate,
            )


def test_stability_summary_names_the_winner_at_each_rung_and_refuses_a_settled_claim():
    space = _two_ad_candidates()
    slots = _slot_of_variant(space)

    def value_of(variant_id, n_synth):
        if slots[variant_id] == "B1_TALKER":
            return 0.50 if n_synth < 50_000 else 0.30
        return 0.40

    check = check_top_pick_stability(
        base_planogram(), space, sku_purchase_share_objective(FOCAL_SKU),
        n_synth_ladder=(10_000, 50_000), simulate=_stability_simulate(value_of),
    )

    text = stability_summary(check)
    assert "ad:AD_1@B1_TALKER" in text
    assert "ad:AD_1@B2_DECAL" in text
    assert "10,000" in text
    lower = text.lower()
    assert "not stable" in lower
    assert re.search(r"\bCI\b", text) is None


def test_the_default_stability_ladder_starts_at_the_default_run_size_and_grows():
    # A ladder whose first rung is not the size the ranking is normally
    # produced at would answer a different question from "does the number we
    # actually report survive a bigger panel".
    from analytics.optimizer import DEFAULT_N_SYNTH

    assert DEFAULT_STABILITY_LADDER[0] == DEFAULT_N_SYNTH
    assert list(DEFAULT_STABILITY_LADDER) == sorted(set(DEFAULT_STABILITY_LADDER))
    assert len(DEFAULT_STABILITY_LADDER) >= 2


# ---------------------------------------------------------------------------
# THE MEASUREMENT: does the committed aisle's top pick survive a bigger panel?
# ---------------------------------------------------------------------------


def test_the_committed_aisle_s_top_pick_is_checked_against_a_bigger_panel():
    """Run the real ladder on the committed planogram and say what happened.

    Both outcomes are reported rather than asserted into one shape: a top pick
    that survives is a result, and one that does not is a bigger result. What
    is asserted either way is that the check answers at all, and that
    `settled_top_pick` agrees with the answer instead of naming a winner the
    ladder did not produce.
    """
    base = base_planogram()
    space = ad_placement_candidates(base) + sku_level_candidates(base, FOCAL_SKU)

    started = time.perf_counter()
    check = check_top_pick_stability(
        base, space, ad_purchase_lift_objective("AD_1"), n_synth_ladder=(10_000, 50_000),
    )
    elapsed = time.perf_counter() - started

    assert len(check.rankings) == 2
    assert check.top_pick_is_stable in (True, False)

    print(f"\n{stability_summary(check)}")
    for ranking in check.rankings:
        best = ranking.best
        value = "undefined" if best.objective is None else ranking.format_value(best.objective)
        current = ranking.current
        where = "not in the space" if current is None else (
            f"ranks {current.rank} of {ranking.n_candidates} at "
            f"{ranking.format_value(current.objective)}")
        print(f"  n_synth={ranking.n_synth:>7,}: {best.candidate.candidate_id:<24} "
              f"{value:>9}   current placement {where}")
    print(f"  ladder wall clock: {elapsed:.1f} s")

    if check.top_pick_is_stable:
        assert check.settled_top_pick == check.top_pick_ids[0]
    else:
        assert check.settled_top_pick is None
        assert check.reordered_at != ()


# ---------------------------------------------------------------------------
# "Moving beats where it is now" is a different claim from "rank 1 beats rank 2"
# ---------------------------------------------------------------------------


def test_beats_current_names_only_candidates_that_clear_the_current_placement():
    # The claim the demo actually makes is about ONE pair -- this placement
    # against the one we are running -- and not about rank 1 against rank 2.
    # A candidate qualifies only when its whole seed range sits above the
    # current placement's whole seed range.
    space = ad_placement_candidates(base_planogram(), creative_ids=("AD_1",),
                                    include_unplaced=False)
    by_slot = {
        "B1_TALKER": {42: 0.90, 43: 0.88},   # 0.88..0.90, clears the current placement
        "B2_DECAL": {42: 0.50, 43: 0.44},    # 0.44..0.50, overlaps it
        "B3_ENDCAP": {42: 0.48, 43: 0.46},   # 0.46..0.48 -- this is the current placement
    }
    ranking = rank_candidates(
        base_planogram(), space, sku_purchase_share_objective(FOCAL_SKU),
        simulate=_scripted_simulate(_by_slot(space, by_slot)),
        seed=42, spread_seeds=(43,), spread_top_n=3,
    )

    assert ranking.current.candidate.candidate_id == "ad:AD_1@B3_ENDCAP"
    assert ranking.beats_current == ("ad:AD_1@B1_TALKER",)


def test_beats_current_is_empty_when_nothing_clears_the_placement_we_run():
    # Empty is a finding: "moving beats where it is now" is not settled here.
    # It is a tuple, not None, because the question WAS answered.
    space = ad_placement_candidates(base_planogram(), creative_ids=("AD_1",),
                                    include_unplaced=False)
    by_slot = {
        "B1_TALKER": {42: 0.52, 43: 0.44},
        "B2_DECAL": {42: 0.50, 43: 0.45},
        "B3_ENDCAP": {42: 0.48, 43: 0.46},
    }
    ranking = rank_candidates(
        base_planogram(), space, sku_purchase_share_objective(FOCAL_SKU),
        simulate=_scripted_simulate(_by_slot(space, by_slot)),
        seed=42, spread_seeds=(43,), spread_top_n=3,
    )

    assert ranking.beats_current == ()


def test_beats_current_is_unknown_when_the_current_placement_has_no_spread():
    # `spread_top_n` can leave the current placement outside the spread set --
    # on the committed aisle at a large n_synth it ranks below the default
    # five. With no range to clear, the comparison was not made, and reporting
    # that as "nothing beats it" would be a claim nobody measured.
    space = ad_placement_candidates(base_planogram(), creative_ids=("AD_1",),
                                    include_unplaced=False)
    by_slot = {
        "B1_TALKER": {42: 0.90, 43: 0.88},
        "B2_DECAL": {42: 0.80, 43: 0.78},
        "B3_ENDCAP": {42: 0.48, 43: 0.46},
    }
    ranking = rank_candidates(
        base_planogram(), space, sku_purchase_share_objective(FOCAL_SKU),
        simulate=_scripted_simulate(_by_slot(space, by_slot)),
        seed=42, spread_seeds=(43,), spread_top_n=2,
    )

    assert ranking.current.seed_spread is None
    assert ranking.beats_current is None


def test_beats_current_is_unknown_when_the_space_excludes_the_current_placement():
    space = _two_ad_candidates()          # B1_TALKER and B2_DECAL only
    ranking = rank_candidates(
        base_planogram(), space, sku_purchase_share_objective(FOCAL_SKU),
        simulate=_scripted_simulate(lambda variant_id, seed: 0.5 + seed / 1000.0),
        seed=42, spread_seeds=(43,), spread_top_n=2,
    )

    assert ranking.current is None
    assert ranking.beats_current is None


def test_a_placement_can_beat_the_current_one_while_the_top_two_stay_unresolved():
    # The measured situation on the committed aisle at n_synth=250,000, and the
    # reason the two claims are reported separately: the leaders are within
    # noise of each other, and one of them is still clear of the placement we
    # run today. Answering only "is rank 1 resolved" throws the second claim
    # away.
    space = ad_placement_candidates(base_planogram(), creative_ids=("AD_1",),
                                    include_unplaced=False)
    by_slot = {
        "B1_TALKER": {42: 0.90, 43: 0.86},   # 0.86..0.90  leaders overlap each other
        "B2_DECAL": {42: 0.89, 43: 0.87},    # 0.87..0.89  but both clear the current
        "B3_ENDCAP": {42: 0.48, 43: 0.46},   # 0.46..0.48  current placement
    }
    ranking = rank_candidates(
        base_planogram(), space, sku_purchase_share_objective(FOCAL_SKU),
        simulate=_scripted_simulate(_by_slot(space, by_slot)),
        seed=42, spread_seeds=(43,), spread_top_n=3,
    )

    assert ranking.top_pick_is_resolved is False
    assert ranking.beats_current == ("ad:AD_1@B1_TALKER", "ad:AD_1@B2_DECAL")

    text = summary(ranking)
    # Both sentences are printed: the one that is settled and the one that is not.
    assert "not resolved" in text.lower()
    assert "clear the current placement" in text.lower()
    assert "ad:AD_1@B1_TALKER" in text and "ad:AD_1@B2_DECAL" in text


def test_the_summary_says_so_when_nothing_clears_the_current_placement():
    space = ad_placement_candidates(base_planogram(), creative_ids=("AD_1",),
                                    include_unplaced=False)
    by_slot = {
        "B1_TALKER": {42: 0.52, 43: 0.44},
        "B2_DECAL": {42: 0.50, 43: 0.45},
        "B3_ENDCAP": {42: 0.48, 43: 0.46},
    }
    ranking = rank_candidates(
        base_planogram(), space, sku_purchase_share_objective(FOCAL_SKU),
        simulate=_scripted_simulate(_by_slot(space, by_slot)),
        seed=42, spread_seeds=(43,), spread_top_n=3,
    )

    lower = summary(ranking).lower()
    assert "no placement clears the current placement" in lower
    assert re.search(r"\bCI\b", summary(ranking)) is None
