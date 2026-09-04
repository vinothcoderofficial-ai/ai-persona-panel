"""M4 acceptance tests for sim/simulator.py."""
from __future__ import annotations

import time

import numpy as np
import pytest

from sim.simulator import build_store, run
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
