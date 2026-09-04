"""The four hand-written persona policies must validate, and the personas must diverge."""
from __future__ import annotations

import pytest

from sim.simulator import build_store, combine, run
from .conftest import PERSONA_IDS


def test_every_policy_validates_against_the_schema(policies, policy_validator):
    assert set(policies) == set(PERSONA_IDS)
    for persona_id, policy in policies.items():
        errors = sorted(policy_validator.iter_errors(policy), key=lambda e: list(e.path))
        assert not errors, f"{persona_id}: " + "; ".join(
            f"{'/'.join(str(p) for p in e.path)}: {e.message}" for e in errors
        )
        assert policy["persona_id"] == persona_id


def test_policies_only_reference_brands_and_categories_that_exist(policies, planogram):
    brands = {s["brand"] for s in planogram["skus"]}
    categories = {s["category"] for s in planogram["skus"]}
    for persona_id, policy in policies.items():
        for brand in policy["brand_affinity"]:
            if brand != "_default":
                assert brand in brands, f"{persona_id} invented brand {brand}"
        for category in policy["goal_categories"]:
            assert category in categories, f"{persona_id} invented category {category}"


def test_mission_visits_fewer_stations_than_browser(planogram, policies):
    """PLAN §11: inter-persona divergence is a reported metric, not an accident."""
    store = build_store(planogram)
    mission = run(store, policies["mission"], n_runs=5000, seed=41, variant_id="A")
    browser = run(store, policies["browser"], n_runs=5000, seed=41, variant_id="A")
    print(f"\nstations_mean  mission={mission['path']['stations_mean']:.3f}  "
          f"browser={browser['path']['stations_mean']:.3f}")
    print(f"duration_s_mean mission={mission['path']['duration_s_mean']:.2f}  "
          f"browser={browser['path']['duration_s_mean']:.2f}")
    assert mission["path"]["stations_mean"] < browser["path"]["stations_mean"]
    assert mission["path"]["duration_s_mean"] < browser["path"]["duration_s_mean"]


def test_loyalist_has_a_dominant_brand(policies):
    affinity = dict(policies["loyalist"]["brand_affinity"])
    default = affinity.pop("_default")
    top_brand = max(affinity, key=affinity.get)
    rest = [v for b, v in affinity.items() if b != top_brand]
    assert affinity[top_brand] >= 0.8
    assert max(rest) <= 0.3
    assert default <= 0.3


def test_personas_produce_different_purchase_mixes(planogram, policies):
    store = build_store(planogram)
    results = {p: run(store, policies[p], n_runs=4000, seed=43, variant_id="A")
               for p in PERSONA_IDS}
    mixes = {p: tuple(round(v, 4) for v in r["purchase_share"].values())
             for p, r in results.items()}
    assert len(set(mixes.values())) == len(PERSONA_IDS)

    # The loyalist should concentrate on its dominant brand far more than the switcher does.
    brand_of = {s["sku_id"]: s["brand"] for s in planogram["skus"]}
    affinity = {b: v for b, v in policies["loyalist"]["brand_affinity"].items() if b != "_default"}
    top_brand = max(affinity, key=affinity.get)
    loyal_share = sum(v for k, v in results["loyalist"]["purchase_share"].items()
                      if brand_of[k] == top_brand)
    switcher_share = sum(v for k, v in results["switcher"]["purchase_share"].items()
                         if brand_of[k] == top_brand)
    print(f"\n{top_brand} purchase share: loyalist={loyal_share:.3f} switcher={switcher_share:.3f}")
    assert loyal_share > switcher_share


def test_population_result_is_the_share_weighted_mix(planogram, policies, personas,
                                                     simresult_validator):
    store = build_store(planogram)
    results = [run(store, policies[p], n_runs=2000, seed=47, variant_id="A") for p in PERSONA_IDS]
    shares = [personas[p]["share_of_population"] for p in PERSONA_IDS]
    assert sum(shares) == pytest.approx(1.0)

    population = combine(results, shares)
    errors = sorted(simresult_validator.iter_errors(population), key=lambda e: list(e.path))
    assert not errors, "\n".join(f"{'/'.join(str(p) for p in e.path)}: {e.message}" for e in errors)

    assert population["persona_id"] == "population"
    assert population["n_runs"] == 2000 * len(PERSONA_IDS)
    assert sum(population["fixation_prob"].values()) == pytest.approx(1.0)

    any_target = next(iter(population["fixation_prob"]))
    expected = sum(s * r["fixation_prob"][any_target] for s, r in zip(shares, results))
    assert population["fixation_prob"][any_target] == pytest.approx(expected)

    expected_stations = sum(s * r["path"]["stations_mean"] for s, r in zip(shares, results))
    assert population["path"]["stations_mean"] == pytest.approx(expected_stations)
