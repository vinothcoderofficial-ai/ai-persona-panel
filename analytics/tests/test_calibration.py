"""Tests for analytics/calibration.py -- the grid search that fits the four
persona shares (PLAN S17, and PLAN §13 which overrides SPEC's Nelder-Mead:
"grid search over 4 persona shares (step 0.05); the 2 global params stay
fixed").

The gating test builds a fake "real" panel out of the simulator with a known
mix and asks the calibrator to recover it within ±0.1 per share.

How the fake panel is built, and why
------------------------------------
Each fake session is one synthetic shopper drawn from one persona. It carries
two independent finite samples of that persona's simulated behaviour:

* **looking** -- `DWELLS_PER_SESSION` `cursor_dwell` events, each landing on a
  slot drawn from that persona's `fixation_prob`. The panel's attention is
  those events put through the production path: `fusion.fuse_session` per
  session, then `fusion.trimmed_mean` across the panel.
* **buying** -- a `BASKET_SIZE` basket drawn from that persona's
  `purchase_share`. The panel's purchase share is the basket counts over the
  panel, normalised -- the same counting rule
  `api/app/routers/experiments.py::_real_purchase_share` uses.

Both are *noisy realisations* of the mix, never the exact population vectors:
9,600 dwell draws and 240 basket draws spread over 24 slots and 24 SKUs. On
top of that the personas the panel is drawn from are simulated at their own
seed and run size (REAL_SIM_SEED / REAL_SIM_N), independent of the ones the
calibrator fits against (CAL_SIM_SEED / CAL_SIM_N), so there is Monte Carlo
noise between the two sides as well as sampling noise within the panel.
`test_the_fixture_is_noisy_in_both_terms` pins that down: at the *true* mix
the Spearman is well below 1 and the MAE well above 0, so neither term of the
objective is exactly satisfied by the right answer and neither can pin it
alone.

The baskets are deliberately NOT injected into the fused event stream as
`add_to_cart` events. `fuse_session` gives interactions 0.3 of the fused
weight, but the synthetic side of the comparison is `fixation_prob` (see
`experiments.py`), which models looking only. Feeding purchases into the real
attention vector therefore adds a whole component the synthetic vector cannot
express, and the calibrator absorbs the difference into the shares: measured,
that displaces the recovered mix by ~0.15 and does NOT shrink with panel size
(still 0.15 at 600 sessions x 400 dwells). That is a real finding about the
fusion/simulator interface, but it is a property of the two attention
definitions, not of the search this module gates -- so looking and buying are
kept as separate samples here.
"""

import json
import time
from typing import Any, Dict, List, Mapping, Sequence

import numpy as np
import pytest

from analytics.calibration import calibrate, evaluate, mixture, share_grid
from analytics.fusion import fuse_session, trimmed_mean
from analytics.metrics import attention_spearman, purchase_share_mae
from api.app import simcache
from api.app.db import ROOT
from api.app.resolve import resolve
from sim.simulator import combine

# The mix the fake panel is generated from, keyed by persona id in the sorted
# order the calibrator uses. PLAN S17: [0.5, 0.2, 0.2, 0.1].
TRUE_MIX = {"browser": 0.5, "loyalist": 0.2, "mission": 0.2, "switcher": 0.1}
UNIFORM_MIX = {persona_id: 0.25 for persona_id in TRUE_MIX}

# The fake panel. 80 sessions is the order of the project's real collection
# target (PLAN S21: ">= 60 accepted"); 120 cursor dwells is what a 60 s
# session yields at the 300 ms dwell threshold; a 3-item basket is a plausible
# snack-aisle shop.
N_SESSIONS = 80
DWELLS_PER_SESSION = 120
BASKET_SIZE = 3
PANEL_SEED = 7

# The simulation the fake panel is drawn from...
REAL_SIM_SEED = 1234
REAL_SIM_N = 5000
# ...and the independent one the calibrator fits against.
CAL_SIM_SEED = 42
CAL_SIM_N = 10_000

TOLERANCE = 0.1  # PLAN S17: "recover each share within ±0.1"


# ---------------------------------------------------------------------------
# fixture construction (test-side only -- nothing here belongs in the module)
# ---------------------------------------------------------------------------


def _resolved(variant_id: str) -> Dict[str, Any]:
    base = json.loads((ROOT / "data" / "planograms" / "demo_aisle.json").read_text(encoding="utf-8"))
    variant = json.loads((ROOT / "data" / "variants" / f"{variant_id}.json").read_text(encoding="utf-8"))
    return resolve(base, variant)


def _occupied_slot_ids(planogram: Mapping[str, Any]) -> List[str]:
    """The shared slot vocabulary, built exactly as experiments.py builds it."""
    return [
        slot["slot_id"]
        for bay in planogram["bays"]
        for shelf in bay["shelves"]
        for slot in shelf["slots"]
        if slot["sku_id"] is not None
    ]


def _slot_of_sku(planogram: Mapping[str, Any]) -> Dict[str, str]:
    return {
        slot["sku_id"]: slot["slot_id"]
        for bay in planogram["bays"]
        for shelf in bay["shelves"]
        for slot in shelf["slots"]
        if slot["sku_id"] is not None
    }


def _bay_of_slot(planogram: Mapping[str, Any]) -> Dict[str, str]:
    return {
        slot["slot_id"]: bay["bay_id"]
        for bay in planogram["bays"]
        for shelf in bay["shelves"]
        for slot in shelf["slots"]
    }


def _probabilities(result: Mapping[str, Any], field: str, keys: Sequence[str]) -> np.ndarray:
    """One persona's `field` over `keys`, renormalised so it can be sampled
    from. `fixation_prob` covers ad slots too, so restricting it to the
    product slots leaves it summing to slightly under 1."""
    vector = np.array([float(result[field].get(key, 0.0)) for key in keys])
    return vector / vector.sum()


def _fake_panel(
    per_persona: Mapping[str, Mapping[str, Any]],
    planogram: Mapping[str, Any],
    slot_ids: Sequence[str],
    sku_ids: Sequence[str],
    mix: Mapping[str, float],
    *,
    n_sessions: int = N_SESSIONS,
    dwells: int = DWELLS_PER_SESSION,
    basket: int = BASKET_SIZE,
    seed: int,
) -> tuple[Dict[str, float], Dict[str, float]]:
    """A fake "real" panel drawn from `mix`, as (attention, purchase_share).

    The persona counts are exact (mix x n_sessions), so the panel's *composition*
    is the target mix and every deviation the calibrator has to survive comes
    from the finite dwell and basket samples, not from a mis-drawn panel.
    """
    rng = np.random.default_rng(seed)
    slot_of_sku = _slot_of_sku(planogram)
    bay_of_slot = _bay_of_slot(planogram)

    counts = {persona_id: int(round(share * n_sessions)) for persona_id, share in mix.items()}
    if sum(counts.values()) != n_sessions:
        raise ValueError(f"{mix} does not split {n_sessions} sessions into whole numbers")

    fused_sessions: List[Dict[str, float]] = []
    bought: Dict[str, int] = {}

    for persona_id in sorted(mix):
        result = per_persona[persona_id]
        look = _probabilities(result, "fixation_prob", slot_ids)
        buy = _probabilities(result, "purchase_share", sku_ids)

        for _ in range(counts[persona_id]):
            events: List[Dict[str, Any]] = []
            t_ms = 0
            for index in rng.choice(len(slot_ids), size=dwells, p=look):
                slot_id = slot_ids[index]
                t_ms += 500
                events.append({
                    "t_ms": t_ms,
                    "type": "cursor_dwell",
                    "station_id": bay_of_slot[slot_id],
                    "payload": {"slot_id": slot_id, "dur_ms": float(rng.lognormal(6.0, 0.4))},
                })
            fused_sessions.append(fuse_session(events, slot_ids))

            for index in rng.choice(len(sku_ids), size=basket, p=buy):
                sku_id = sku_ids[index]
                bought[sku_id] = bought.get(sku_id, 0) + 1
                assert sku_id in slot_of_sku  # every purchasable sku sits in a slot

    total = sum(bought.values())
    return trimmed_mean(fused_sessions, slot_ids), {k: v / total for k, v in bought.items()}


def _synthetic_results(n: int, *, variant_id: str = "A", seed: int = 0) -> Dict[str, Dict[str, Any]]:
    """`n` hand-built persona results, all carrying the SAME vectors.

    Only the four fields calibration.py reads are present, which is also the
    point: the module must not depend on the rest of a SimResult.
    """
    return {
        f"p{i}": {
            "variant_id": variant_id,
            "seed": seed,
            "fixation_prob": {"S1": 0.5, "S2": 0.3, "S3": 0.2},
            "purchase_share": {"K1": 0.6, "K2": 0.4},
        }
        for i in range(n)
    }


@pytest.fixture(scope="module")
def variant_a() -> Dict[str, Any]:
    return _resolved("A")


@pytest.fixture(scope="module")
def vocabulary(variant_a) -> tuple[List[str], List[str]]:
    return _occupied_slot_ids(variant_a), [sku["sku_id"] for sku in variant_a["skus"]]


@pytest.fixture(scope="module")
def basis_a(variant_a) -> Dict[str, Dict[str, Any]]:
    """What the calibrator fits against: one SimResult per persona on variant A."""
    return simcache.population(variant_a, "A", n_synth=CAL_SIM_N, seed=CAL_SIM_SEED).per_persona


@pytest.fixture(scope="module")
def truth_a(variant_a) -> Dict[str, Dict[str, Any]]:
    """The independent simulation the fake panel's shoppers are drawn from."""
    return simcache.population(variant_a, "A", n_synth=REAL_SIM_N, seed=REAL_SIM_SEED).per_persona


@pytest.fixture(scope="module")
def panel_a(truth_a, variant_a, vocabulary) -> tuple[Dict[str, float], Dict[str, float]]:
    slot_ids, sku_ids = vocabulary
    return _fake_panel(truth_a, variant_a, slot_ids, sku_ids, TRUE_MIX, seed=PANEL_SEED)


# ---------------------------------------------------------------------------
# share_grid
# ---------------------------------------------------------------------------


def test_share_grid_has_exactly_1771_candidates():
    """Every composition of 20 units into 4 ordered parts: C(20+3, 3) = 1771."""
    grid = share_grid(0.05, 4)

    assert len(grid) == 1771
    assert len(set(grid)) == 1771


def test_every_share_grid_candidate_is_a_valid_mix():
    """Built in integer units and divided only at the end, so float drift can
    never make a candidate miss 1.0 or go negative."""
    for candidate in share_grid(0.05, 4):
        assert len(candidate) == 4
        assert sum(candidate) == pytest.approx(1.0, abs=1e-9)
        assert all(share >= 0.0 for share in candidate)
        assert all(abs(share * 20 - round(share * 20)) < 1e-9 for share in candidate)


def test_share_grid_contains_the_true_mix_and_the_uniform_mix():
    """Both reference points the tests below compare against are grid points,
    so "the search found something better" is a fair comparison."""
    grid = set(share_grid(0.05, 4))

    assert tuple(TRUE_MIX[p] for p in sorted(TRUE_MIX)) in grid
    assert (0.25, 0.25, 0.25, 0.25) in grid


def test_share_grid_starts_at_the_lexicographically_smallest_candidate():
    """The visiting order is what breaks ties, so it is pinned here."""
    grid = share_grid(0.05, 4)

    assert grid[0] == (0.0, 0.0, 0.0, 1.0)
    assert grid[-1] == (1.0, 0.0, 0.0, 0.0)
    assert grid == sorted(grid)


def test_share_grid_of_a_coarser_step():
    """step 0.25 -> 4 units into 4 parts -> C(7, 3) = 35."""
    assert len(share_grid(0.25, 4)) == 35


def test_share_grid_rejects_a_step_that_does_not_divide_one():
    """0.03 gives 33.33 units; rounding it would silently change the grid."""
    with pytest.raises(ValueError, match="step"):
        share_grid(0.03, 4)


# ---------------------------------------------------------------------------
# the linearity the whole speed argument rests on
# ---------------------------------------------------------------------------


def test_mixture_reproduces_combine_exactly(basis_a, vocabulary):
    """`combine()` blends every field as sum(share x persona value), so a
    share-weighted linear mix of the per-persona vectors IS the population
    result -- which is what lets the grid search skip re-simulating 1,771
    times. Asserted against the real `combine()` rather than assumed."""
    slot_ids, sku_ids = vocabulary
    persona_ids = sorted(basis_a)
    results = [basis_a[persona_id] for persona_id in persona_ids]

    for shares in [(0.5, 0.2, 0.2, 0.1), (0.25, 0.25, 0.25, 0.25), (1.0, 0.0, 0.0, 0.0)]:
        expected = combine(results, list(shares))
        actual = mixture(basis_a, dict(zip(persona_ids, shares)),
                         slot_ids=slot_ids, sku_ids=sku_ids)

        for slot_id in slot_ids:
            assert actual["fixation_prob"][slot_id] == pytest.approx(
                expected["fixation_prob"][slot_id], abs=1e-12)
        for sku_id in sku_ids:
            assert actual["purchase_share"][sku_id] == pytest.approx(
                expected["purchase_share"][sku_id], abs=1e-12)


# ---------------------------------------------------------------------------
# THE GATING TEST
# ---------------------------------------------------------------------------


def test_calibration_recovers_the_generating_mix(basis_a, panel_a, vocabulary):
    """PLAN S17: fake sessions generated from mix [0.5, 0.2, 0.2, 0.1] ->
    calibration must recover EACH share within ±0.1. This gates Track D."""
    slot_ids, sku_ids = vocabulary
    real_attention, real_purchase_share = panel_a

    started = time.perf_counter()
    result = calibrate(real_attention, real_purchase_share, basis_a,
                       slot_ids=slot_ids, sku_ids=sku_ids)
    elapsed = time.perf_counter() - started

    recovered = result["shares"]
    print(f"\ntrue mix      {[TRUE_MIX[p] for p in sorted(TRUE_MIX)]}")
    print(f"recovered     {[round(recovered[p], 2) for p in sorted(recovered)]}")
    print(f"per-share err {[round(abs(recovered[p] - TRUE_MIX[p]), 3) for p in sorted(TRUE_MIX)]}")
    print(f"rho {result['attention_spearman']:+.4f}  mae {result['purchase_share_mae']:.5f} "
          f"obj {result['objective']:.4f}")
    print(f"grid search over {result['n_candidates']} candidates: {elapsed:.3f} s")

    assert set(recovered) == set(TRUE_MIX)
    for persona_id, true_share in TRUE_MIX.items():
        assert abs(recovered[persona_id] - true_share) <= TOLERANCE, (
            f"{persona_id}: recovered {recovered[persona_id]}, true {true_share}")

    # 1,771 candidates x one 4-persona 10,000-shopper run would be ~7 minutes.
    # Anything near that means the search re-simulated instead of taking the
    # linear combination `test_mixture_reproduces_combine_exactly` licenses.
    assert elapsed < 10.0, f"grid search took {elapsed:.1f} s -- is it re-simulating?"


def test_the_fixture_is_noisy_in_both_terms(basis_a, panel_a, vocabulary):
    """The anti-vacuity precondition for the gating test.

    A previous attempt at this fixture handed the calibrator a purchase vector
    that was the exact population mixture, so the MAE term alone was minimised
    at the true answer and the test could not fail. Here BOTH terms are noisy
    at the true mix: the attention Spearman is short of 1 and the purchase MAE
    is well above 0, so neither term is exactly satisfied by the right answer.
    """
    slot_ids, sku_ids = vocabulary
    real_attention, real_purchase_share = panel_a

    at_truth = evaluate(TRUE_MIX, real_attention, real_purchase_share, basis_a,
                        slot_ids=slot_ids, sku_ids=sku_ids)
    print(f"\nat the true mix: rho {at_truth['attention_spearman']:+.4f} "
          f"mae {at_truth['purchase_share_mae']:.5f}")

    assert at_truth["attention_spearman"] < 0.99
    assert at_truth["purchase_share_mae"] > 0.005


def test_recovery_holds_across_panel_seeds(basis_a, truth_a, variant_a, vocabulary):
    """The gating test uses one panel seed. This redraws the panel three more
    times so a lucky draw cannot be what makes it pass."""
    slot_ids, sku_ids = vocabulary

    for seed in (11, 23, 31):
        real_attention, real_purchase_share = _fake_panel(
            truth_a, variant_a, slot_ids, sku_ids, TRUE_MIX, seed=seed)
        recovered = calibrate(real_attention, real_purchase_share, basis_a,
                              slot_ids=slot_ids, sku_ids=sku_ids)["shares"]
        print(f"\nseed {seed}: {[round(recovered[p], 2) for p in sorted(recovered)]}")

        for persona_id, true_share in TRUE_MIX.items():
            assert abs(recovered[persona_id] - true_share) <= TOLERANCE, (
                f"seed {seed}, {persona_id}: {recovered[persona_id]} vs {true_share}")


# ---------------------------------------------------------------------------
# the objective is genuinely minimised, and both terms carry weight
# ---------------------------------------------------------------------------


def test_the_returned_objective_beats_the_true_and_uniform_mixes(basis_a, panel_a, vocabulary):
    """An exhaustive search cannot be beaten by any grid point, and both
    reference mixes are grid points. If the true mix scored better than the
    winner the search would be broken."""
    slot_ids, sku_ids = vocabulary
    real_attention, real_purchase_share = panel_a
    kwargs = dict(slot_ids=slot_ids, sku_ids=sku_ids)

    best = calibrate(real_attention, real_purchase_share, basis_a, **kwargs)
    at_truth = evaluate(TRUE_MIX, real_attention, real_purchase_share, basis_a, **kwargs)
    at_uniform = evaluate(UNIFORM_MIX, real_attention, real_purchase_share, basis_a, **kwargs)

    assert best["objective"] <= at_truth["objective"]
    assert best["objective"] <= at_uniform["objective"]


def test_the_attention_term_alone_moves_the_answer_toward_the_true_mix(
        basis_a, panel_a, vocabulary):
    """VACUITY GUARD. With `mae_weight=0` the purchase term is switched off
    entirely, so nothing but the Spearman over slots can drive the search.
    The answer still lands much closer to the generating mix than uniform
    does -- so the recovery in the gating test cannot be the MAE term acting
    alone."""
    slot_ids, sku_ids = vocabulary
    real_attention, real_purchase_share = panel_a

    attention_only = calibrate(real_attention, real_purchase_share, basis_a,
                               slot_ids=slot_ids, sku_ids=sku_ids, mae_weight=0.0)["shares"]
    error = max(abs(attention_only[p] - TRUE_MIX[p]) for p in TRUE_MIX)
    uniform_error = max(abs(UNIFORM_MIX[p] - TRUE_MIX[p]) for p in TRUE_MIX)
    print(f"\nattention-only mix {[round(attention_only[p], 2) for p in sorted(attention_only)]} "
          f"max err {error:.3f} (uniform: {uniform_error:.3f})")

    assert error < uniform_error
    assert error <= 0.15


def test_the_recovered_mix_beats_uniform_on_the_attention_term(basis_a, panel_a, vocabulary):
    """VACUITY GUARD, the other direction: the mix the full objective picks is
    better than uniform on the Spearman *specifically*, not only on the
    combined score. A winner chosen by the MAE term alone would have no
    reason to be."""
    slot_ids, sku_ids = vocabulary
    real_attention, real_purchase_share = panel_a
    kwargs = dict(slot_ids=slot_ids, sku_ids=sku_ids)

    best = calibrate(real_attention, real_purchase_share, basis_a, **kwargs)
    at_uniform = evaluate(UNIFORM_MIX, real_attention, real_purchase_share, basis_a, **kwargs)

    assert best["attention_spearman"] > at_uniform["attention_spearman"]


def test_the_objective_is_one_minus_rho_plus_five_mae(basis_a, panel_a, vocabulary):
    """The formula PLAN S17 specifies, checked against the components the same
    call reports."""
    slot_ids, sku_ids = vocabulary
    real_attention, real_purchase_share = panel_a

    result = calibrate(real_attention, real_purchase_share, basis_a,
                       slot_ids=slot_ids, sku_ids=sku_ids)

    assert result["objective"] == pytest.approx(
        (1.0 - result["attention_spearman"]) + 5.0 * result["purchase_share_mae"])
    assert result["mae_weight"] == 5.0
    assert result["step"] == 0.05
    assert result["n_candidates"] == 1771


def test_calibrate_is_deterministic(basis_a, panel_a, vocabulary):
    """No RNG anywhere in the search: the same panel and basis give a
    bit-identical result, which is what lets a calibration be frozen and
    re-verified from the committed data."""
    slot_ids, sku_ids = vocabulary
    real_attention, real_purchase_share = panel_a
    kwargs = dict(slot_ids=slot_ids, sku_ids=sku_ids)

    assert (calibrate(real_attention, real_purchase_share, basis_a, **kwargs)
            == calibrate(real_attention, real_purchase_share, basis_a, **kwargs))


def test_ties_break_to_the_lexicographically_smallest_share_tuple():
    """Four personas with identical vectors make every candidate score
    identically. The winner must then be the first candidate in the grid's
    ascending lexicographic order -- all weight on the last persona id --
    rather than whichever the dict happened to yield first."""
    per_persona = _synthetic_results(4)

    result = calibrate({"S1": 0.5, "S2": 0.3, "S3": 0.2}, {"K1": 0.6, "K2": 0.4},
                       per_persona, slot_ids=["S1", "S2", "S3"], sku_ids=["K1", "K2"])

    assert result["shares"] == {"p0": 0.0, "p1": 0.0, "p2": 0.0, "p3": 1.0}


# ---------------------------------------------------------------------------
# freeze and holdout
# ---------------------------------------------------------------------------


def test_evaluate_on_a_holdout_variant_leaves_the_frozen_shares_alone(
        basis_a, panel_a, vocabulary, variant_a):
    """Fit on A, freeze, then score B. `evaluate` must report B's metrics under
    the shares it was handed and must not re-fit them or mutate the caller's
    mapping -- refitting on the holdout is exactly what would destroy it."""
    slot_ids, sku_ids = vocabulary
    real_attention, real_purchase_share = panel_a

    frozen = calibrate(real_attention, real_purchase_share, basis_a,
                       slot_ids=slot_ids, sku_ids=sku_ids)["shares"]
    before = dict(frozen)

    variant_b = _resolved("B")
    basis_b = simcache.population(variant_b, "B", n_synth=CAL_SIM_N,
                                  seed=CAL_SIM_SEED).per_persona
    truth_b = simcache.population(variant_b, "B", n_synth=REAL_SIM_N,
                                  seed=REAL_SIM_SEED).per_persona
    slot_ids_b = _occupied_slot_ids(variant_b)
    sku_ids_b = [sku["sku_id"] for sku in variant_b["skus"]]
    panel_b = _fake_panel(truth_b, variant_b, slot_ids_b, sku_ids_b, TRUE_MIX, seed=PANEL_SEED)

    holdout = evaluate(frozen, panel_b[0], panel_b[1], basis_b,
                       slot_ids=slot_ids_b, sku_ids=sku_ids_b)

    assert frozen == before  # the caller's mapping is untouched
    assert holdout["shares"] == before  # and so are the shares it reports
    assert holdout["variant_id"] == "B"
    assert -1.0 <= holdout["attention_spearman"] <= 1.0
    assert holdout["purchase_share_mae"] >= 0.0
    assert holdout["objective"] == pytest.approx(
        (1.0 - holdout["attention_spearman"]) + 5.0 * holdout["purchase_share_mae"])


def test_calibrate_echoes_the_variant_it_was_fitted_on(basis_a, panel_a, vocabulary):
    """Fit-vs-holdout has to be reportable separately (PLAN S17: "always report
    fit and holdout separately"), so the result says which variant produced
    it. The caller passes A."""
    slot_ids, sku_ids = vocabulary

    result = calibrate(panel_a[0], panel_a[1], basis_a, slot_ids=slot_ids, sku_ids=sku_ids)

    assert result["variant_id"] == "A"


# ---------------------------------------------------------------------------
# guards
# ---------------------------------------------------------------------------


def test_persona_results_from_different_variants_are_refused():
    """`combine()` refuses to blend results from two variants; the linear
    shortcut must refuse the same input, or the mixture would be meaningless."""
    per_persona = _synthetic_results(2)
    per_persona["p1"] = {**per_persona["p1"], "variant_id": "B"}

    with pytest.raises(ValueError, match="variant"):
        calibrate({"S1": 1.0}, {"K1": 1.0}, per_persona,
                  slot_ids=["S1"], sku_ids=["K1"])


def test_persona_results_from_different_seeds_are_refused():
    """Same reason: `combine()` refuses mixed seeds, so this does too."""
    per_persona = _synthetic_results(2)
    per_persona["p1"] = {**per_persona["p1"], "seed": 99}

    with pytest.raises(ValueError, match="seed"):
        calibrate({"S1": 1.0}, {"K1": 1.0}, per_persona,
                  slot_ids=["S1"], sku_ids=["K1"])


def test_an_empty_persona_bundle_is_refused():
    with pytest.raises(ValueError, match="persona"):
        calibrate({"S1": 1.0}, {"K1": 1.0}, {}, slot_ids=["S1"], sku_ids=["K1"])


def test_evaluate_refuses_shares_that_do_not_name_every_persona():
    per_persona = _synthetic_results(4)

    with pytest.raises(ValueError, match="persona"):
        evaluate({"p0": 1.0}, {"S1": 1.0}, {"K1": 1.0}, per_persona,
                 slot_ids=["S1"], sku_ids=["K1"])


def test_evaluate_refuses_shares_that_do_not_sum_to_one():
    per_persona = _synthetic_results(2)

    with pytest.raises(ValueError, match="sum to 1"):
        evaluate({"p0": 0.5, "p1": 0.2}, {"S1": 1.0}, {"K1": 1.0}, per_persona,
                 slot_ids=["S1"], sku_ids=["K1"])


def test_evaluate_refuses_a_negative_share():
    per_persona = _synthetic_results(2)

    with pytest.raises(ValueError, match="negative"):
        evaluate({"p0": 1.5, "p1": -0.5}, {"S1": 1.0}, {"K1": 1.0}, per_persona,
                 slot_ids=["S1"], sku_ids=["K1"])


def test_calibrate_returns_plain_floats(basis_a, panel_a, vocabulary):
    """RESULTS.md and schemas/metrics.schema.json's `calibrated_shares` want
    JSON numbers, not numpy scalars."""
    slot_ids, sku_ids = vocabulary

    result = calibrate(panel_a[0], panel_a[1], basis_a, slot_ids=slot_ids, sku_ids=sku_ids)

    assert all(isinstance(share, float) for share in result["shares"].values())
    assert isinstance(result["objective"], float)
    assert isinstance(result["attention_spearman"], float)
    assert isinstance(result["purchase_share_mae"], float)
    assert json.loads(json.dumps(result)) == result
