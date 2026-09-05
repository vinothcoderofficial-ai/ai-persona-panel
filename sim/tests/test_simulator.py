"""M4 acceptance tests for sim/simulator.py."""
from __future__ import annotations

import time

import numpy as np
import pytest

from sim.simulator import build_store, combine, run
from .conftest import ad_slot_ids, empty_slot_ids, slot_categories


def test_exploration_zero_fixates_goal_slots_only(planogram, policies):
    """(d) With the goal gate shut, only slots in a goal category can be fixated."""
    policy = dict(policies["mission"], exploration=0.0)
    goals = set(policy["goal_categories"])
    assert goals, "the mission policy needs goal categories for this test to mean anything"

    store = build_store(planogram)
    result = run(store, policy, n_runs=2000, seed=7, variant_id="A")

    categories = slot_categories(planogram)
    fixated = [t for t, p in result["fixation_prob"].items() if p > 0.0]
    assert fixated, "the simulator recorded no fixations at all"
    for target_id in fixated:
        assert target_id in categories, f"{target_id} is not an occupied slot"
        assert categories[target_id] in goals, f"{target_id} is {categories[target_id]}"

    for ad_id in ad_slot_ids(planogram):
        assert result["fixation_prob"].get(ad_id, 0.0) == 0.0
        assert result["ad_slot_attention"][ad_id] == 0.0


def test_exploration_one_matches_p_saliency(planogram, policies):
    """(e) With relevance switched off, the within-bay fixation distribution is p_saliency."""
    policy = dict(policies["browser"], exploration=1.0)
    store = build_store(planogram)
    result = run(store, policy, n_runs=10_000, seed=11, variant_id="A")

    saliency = store.saliency  # the very saliency the run was driven by
    worst = 0.0
    worst_target = ""
    for bay_id, bay in saliency.items():
        observed = np.array([result["fixation_prob"][t] for t in bay.target_ids])
        assert observed.sum() > 0.0, bay_id
        observed = observed / observed.sum()  # condition on standing at this station
        deviation = np.abs(observed - bay.p_saliency)
        if deviation.max() > worst:
            worst = float(deviation.max())
            worst_target = bay.target_ids[int(deviation.argmax())]
    print(f"\nworst per-target deviation from p_saliency: {worst:.5f} at {worst_target}")
    assert worst <= 0.02


def test_ten_thousand_shoppers_four_personas_under_800ms(planogram, policies):
    """(f) The what-if budget. Store arrays are warm, exactly as the what-if endpoint keeps them."""
    store = build_store(planogram)
    for policy in policies.values():  # warm every numpy code path
        run(store, policy, n_runs=200, seed=1, variant_id="A")

    t0 = time.perf_counter()
    for policy in policies.values():
        run(store, policy, n_runs=10_000, seed=1, variant_id="A")
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    t1 = time.perf_counter()
    cold_store = build_store(planogram)
    for policy in policies.values():
        run(cold_store, policy, n_runs=10_000, seed=1, variant_id="A")
    with_build_ms = (time.perf_counter() - t1) * 1000.0

    print(f"\n10,000 shoppers x {len(policies)} personas: {elapsed_ms:.1f} ms "
          f"(including build_store: {with_build_ms:.1f} ms)")
    assert elapsed_ms < 800.0


def test_same_seed_gives_identical_simresult(planogram, policies):
    """(g) Byte-for-byte reproducibility, including sim_run_id."""
    store = build_store(planogram)
    first = run(store, policies["switcher"], n_runs=1500, seed=99, variant_id="A")
    second = run(store, policies["switcher"], n_runs=1500, seed=99, variant_id="A")
    assert first == second

    other_seed = run(store, policies["switcher"], n_runs=1500, seed=100, variant_id="A")
    assert other_seed != first
    assert other_seed["sim_run_id"] != first["sim_run_id"]

    # A rebuilt store must not change the answer either.
    rebuilt = run(build_store(planogram), policies["switcher"], n_runs=1500, seed=99, variant_id="A")
    assert rebuilt == first


def test_empty_slots_never_appear_in_fixation_prob(planogram, policies):
    store = build_store(planogram)
    result = run(store, policies["browser"], n_runs=1000, seed=3, variant_id="A")
    for empty_id in empty_slot_ids(planogram):
        assert empty_id not in result["fixation_prob"]
        assert empty_id not in result["dwell_ms_mean"]


def test_simresult_validates_against_schema(planogram, policies, simresult_validator):
    store = build_store(planogram)
    result = run(store, policies["loyalist"], n_runs=1000, seed=5, variant_id="A")
    errors = sorted(simresult_validator.iter_errors(result), key=lambda e: list(e.path))
    assert not errors, "\n".join(f"{'/'.join(str(p) for p in e.path)}: {e.message}" for e in errors)

    assert sum(result["fixation_prob"].values()) == pytest.approx(1.0)
    assert sum(result["purchase_share"].values()) == pytest.approx(1.0)
    assert result["n_runs"] == 1000
    assert result["seed"] == 5
    assert result["variant_id"] == "A"
    assert result["persona_id"] == "loyalist"


def test_dwell_and_attention_are_consistent(planogram, policies):
    """dwell_ms_mean is 0.0 for unfixated targets and a plausible mean elsewhere."""
    store = build_store(planogram)
    policy = policies["browser"]
    result = run(store, policy, n_runs=2000, seed=17, variant_id="A")

    median_dwell = float(np.exp(policy["dwell_ms"]["mu"]))
    for target_id, prob in result["fixation_prob"].items():
        mean_dwell = result["dwell_ms_mean"][target_id]
        if prob == 0.0:
            assert mean_dwell == 0.0
        else:
            assert 0.2 * median_dwell < mean_dwell < 5.0 * median_dwell

    # B3_ENDCAP carries a creative, so a browsing shopper should sometimes look at it.
    assert result["ad_slot_attention"]["B3_ENDCAP"] > 0.0
    assert result["ad_slot_attention"]["B1_TALKER"] == 0.0  # no creative, so not a target
    assert all(0.0 <= v <= 1.0 for v in result["ad_slot_attention"].values())


def test_purchases_only_happen_in_goal_categories(planogram, policies):
    store = build_store(planogram)
    policy = policies["loyalist"]
    result = run(store, policy, n_runs=2000, seed=23, variant_id="A")

    category = {s["sku_id"]: s["category"] for s in planogram["skus"]}
    bought = [sku_id for sku_id, share in result["purchase_share"].items() if share > 0.0]
    assert bought, "the loyalist bought nothing at all"
    for sku_id in bought:
        assert category[sku_id] in set(policy["goal_categories"])

    # Every SKU in the store is reported, so downstream metrics get a dense vector.
    assert set(result["purchase_share"]) == {s["sku_id"] for s in planogram["skus"]}


def test_ad_exposed_and_unexposed_shares_split_the_population(planogram, policies):
    store = build_store(planogram)
    result = run(store, policies["switcher"], n_runs=4000, seed=29, variant_id="A")
    for key in ("ad_exposed_purchase_share", "ad_unexposed_purchase_share"):
        total = sum(result[key].values())
        assert total == pytest.approx(1.0) or total == 0.0, key
        assert set(result[key]) == set(result["purchase_share"]), key


def test_only_the_browser_shops_without_goals(planogram, policies):
    """SPEC M4 keeps a shopper walking while `goals non-empty or archetype == browser`."""
    store = build_store(planogram)
    goalless = dict(policies["browser"], goal_categories=[])

    browsing = run(store, goalless, n_runs=500, seed=13, variant_id="A")
    assert browsing["path"]["stations_mean"] > 0.0

    listed = run(store, goalless, n_runs=500, seed=13, variant_id="A", archetype="mission")
    assert listed["path"]["stations_mean"] == 0.0
    assert listed["path"]["duration_s_mean"] == 0.0
    assert all(p == 0.0 for p in listed["fixation_prob"].values())


def test_time_budget_bounds_the_path(planogram, policies):
    store = build_store(planogram)
    n_bays = len(planogram["bays"])
    result = run(store, policies["browser"], n_runs=1000, seed=31, variant_id="A")
    assert 0.0 < result["path"]["stations_mean"] <= n_bays
    assert result["path"]["duration_s_mean"] > 0.0
    assert result["path"]["duration_s_mean"] < policies["browser"]["time_budget_s"]["mean"] * 3


# ---------------------------------------------------------------------------
# The purchase-event counts behind the two ad-exposure arms.
#
# `ad_exposed_purchase_share` / `ad_unexposed_purchase_share` are NORMALISED
# share vectors, so the number of purchase events behind each arm cannot be
# read back out of them. `purchase_share` recovers the exposed FRACTION and
# nothing else; `n_runs` is the shopper count and a shopper buys 0..n_bays
# items, so it is not a stand-in. These two integers are that missing pair,
# and analytics/lift.py needs them to resample the synthetic arm.
# ---------------------------------------------------------------------------


def test_purchase_event_counts_recover_the_pooled_share_from_the_two_arms(planogram, policies):
    """This is what the counts MEAN: weight the two arm vectors by them and
    you get `purchase_share` back exactly. Nothing weaker pins them down --
    any pair in the same ratio reproduces the arms' mixture, but only the true
    event counts also reproduce the totals a bootstrap needs.
    """
    store = build_store(planogram)
    result = run(store, policies["switcher"], n_runs=4000, seed=29, variant_id="A")

    n_exposed = result["n_purchases_exposed"]
    n_unexposed = result["n_purchases_unexposed"]
    assert isinstance(n_exposed, int) and isinstance(n_unexposed, int)
    assert n_exposed > 0 and n_unexposed > 0, "seed 29 must exercise both arms"

    total = n_exposed + n_unexposed
    for sku_id, pooled in result["purchase_share"].items():
        recovered = (
            n_exposed * result["ad_exposed_purchase_share"][sku_id]
            + n_unexposed * result["ad_unexposed_purchase_share"][sku_id]
        ) / total
        assert recovered == pytest.approx(pooled, abs=1e-12), sku_id


def test_purchase_event_counts_are_zero_when_nobody_bought_anything(planogram, policies):
    """Zero is a real answer, not a missing one: an arm with no purchases has
    an all-zero share vector, and its count must say 0 rather than be absent."""
    store = build_store(planogram)
    goalless = dict(policies["browser"], goal_categories=[])
    result = run(store, goalless, n_runs=500, seed=13, variant_id="A", archetype="mission")

    assert result["n_purchases_exposed"] == 0
    assert result["n_purchases_unexposed"] == 0
    assert all(v == 0.0 for v in result["ad_exposed_purchase_share"].values())
    assert all(v == 0.0 for v in result["ad_unexposed_purchase_share"].values())


def test_purchase_event_counts_validate_against_the_schema(planogram, policies,
                                                           simresult_validator):
    store = build_store(planogram)
    result = run(store, policies["switcher"], n_runs=500, seed=29, variant_id="A")
    errors = sorted(simresult_validator.iter_errors(result), key=lambda e: list(e.path))
    assert not errors, "\n".join(f"{'/'.join(str(p) for p in e.path)}: {e.message}"
                                 for e in errors)


# ---------------------------------------------------------------------------
# combine(): how the counts blend for the population row.
#
# Every other field blends as sum(share x value), which for a NORMALISED share
# vector is a mixture. A count is not a share, so neither a plain sum nor a
# share-weighted sum is the right blend -- see the docstring on
# `sim.simulator.combine`. What the population row needs is the count that
# carries the same information as the blended vector: the effective sample
# size of a weighted average of independent estimates.
# ---------------------------------------------------------------------------


def persona_result(persona_id: str, *, n_exposed: int, n_unexposed: int,
                   n_runs: int = 1000) -> dict:
    """The smallest SimResult `combine()` will accept, with chosen arm counts."""
    return {
        "sim_run_id": f"{persona_id}0000",
        "variant_id": "A",
        "persona_id": persona_id,
        "n_runs": n_runs,
        "seed": 1,
        "fixation_prob": {"S1": 1.0},
        "dwell_ms_mean": {"S1": 400.0},
        "ad_slot_attention": {"AD": 0.5},
        "purchase_share": {"A1": 0.5, "B1": 0.5},
        "ad_exposed_purchase_share": ({"A1": 0.5, "B1": 0.5} if n_exposed
                                      else {"A1": 0.0, "B1": 0.0}),
        "ad_unexposed_purchase_share": ({"A1": 0.5, "B1": 0.5} if n_unexposed
                                        else {"A1": 0.0, "B1": 0.0}),
        "n_purchases_exposed": n_exposed,
        "n_purchases_unexposed": n_unexposed,
        "path": {"stations_mean": 2.0, "duration_s_mean": 40.0},
    }


def test_combined_count_is_the_pooled_count_when_shares_match_the_arm_sizes():
    """The one case where the mixture IS the pool: shares proportional to the
    arms' event counts. Then, and only then, the effective count equals the
    plain sum -- which is the sanity check that the formula is not inventing
    precision or throwing it away.
    """
    results = [persona_result("a", n_exposed=2000, n_unexposed=1),
               persona_result("b", n_exposed=6000, n_unexposed=3)]
    population = combine(results, [0.25, 0.75])

    assert population["n_purchases_exposed"] == 8000
    assert population["n_purchases_exposed"] == sum(r["n_purchases_exposed"] for r in results)


def test_combined_count_shrinks_when_a_thinly_simulated_persona_dominates():
    """90 % of the population row comes from a persona with 100 purchase
    events. The blended vector is therefore about as well resolved as those
    100 events, not as the 10,000 in the pool -- and nowhere near the 1,080 a
    share-weighted sum would claim either.
    """
    results = [persona_result("a", n_exposed=100, n_unexposed=1),
               persona_result("b", n_exposed=9900, n_unexposed=1)]
    population = combine(results, [0.9, 0.1])

    assert population["n_purchases_exposed"] == 123
    assert population["n_purchases_exposed"] < sum(r["n_purchases_exposed"] for r in results)
    assert population["n_purchases_exposed"] < round(0.9 * 100 + 0.1 * 9900)


def test_combined_count_ignores_a_persona_whose_arm_is_empty():
    """A persona with no purchases in an arm contributes an all-zero vector to
    the mixture, so `analytics/lift.py` renormalises it away. The count has to
    drop it on exactly the same terms, or it would report a sample size for
    shoppers who contributed nothing.
    """
    results = [persona_result("a", n_exposed=0, n_unexposed=10),
               persona_result("b", n_exposed=500, n_unexposed=10),
               persona_result("c", n_exposed=1500, n_unexposed=10)]
    population = combine(results, [0.5, 0.25, 0.25])

    assert population["n_purchases_exposed"] == 1500


def test_combined_count_is_zero_when_every_arm_is_empty():
    results = [persona_result("a", n_exposed=0, n_unexposed=7),
               persona_result("b", n_exposed=0, n_unexposed=7)]
    population = combine(results, [0.4, 0.6])

    assert population["n_purchases_exposed"] == 0
    assert population["n_purchases_unexposed"] > 0


def test_combine_refuses_results_that_carry_no_purchase_event_counts():
    """`combine()` already hard-requires the two arm vectors (blend() raises on
    a result that lacks them). The counts are on the same footing: a population
    row missing them would silently lose the synthetic interval."""
    good = persona_result("a", n_exposed=10, n_unexposed=10)
    stale = persona_result("b", n_exposed=10, n_unexposed=10)
    del stale["n_purchases_exposed"]

    with pytest.raises(ValueError, match="n_purchases_exposed"):
        combine([good, stale], [0.5, 0.5])


def test_combined_counts_validate_against_the_schema(simresult_validator):
    results = [persona_result("a", n_exposed=2000, n_unexposed=1200),
               persona_result("b", n_exposed=600, n_unexposed=4000)]
    population = combine(results, [0.3, 0.7])
    errors = sorted(simresult_validator.iter_errors(population), key=lambda e: list(e.path))
    assert not errors, "\n".join(f"{'/'.join(str(p) for p in e.path)}: {e.message}"
                                 for e in errors)
