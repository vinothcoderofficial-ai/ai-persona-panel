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

Each session's basket also enters that session's own event stream as
`add_to_cart` events, because that is where a real basket is:
`api/app/routers/experiments.py::_real_purchase_share` counts exactly the
`add_to_cart` events `fuse_session` is fusing, so a real panel's attention
always carries an interaction component alongside the looking one.

That was not always true of this fixture. It used to hold the baskets out of
the event stream, because the synthetic side of the comparison was the
population's raw `fixation_prob` -- looking only -- so feeding purchases into
the real vector added a component the synthetic vector could not express, and
the search absorbed the difference into the shares. S17 measured that bias at
~0.15 of displaced share, not shrinking with panel size. Rather than keep
dodging it with a panel that cannot exist (shoppers who bought 240 items while
touching nothing), the synthetic side is now fused the same way the real side
is (`fusion.fuse_synthetic`), and the fixture is the realistic one.
`test_fusing_the_synthetic_side_reduces_the_basket_injection_bias` runs both
comparisons over this panel and reports the two displacements.
"""

import json
import time
from typing import Any, Dict, List, Mapping, Sequence

import numpy as np
import pytest

from analytics.calibration import DEFAULT_STEP, calibrate, evaluate, mixture, share_grid
from analytics.fusion import fuse_session, fuse_synthetic, trimmed_mean
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

    Each session's basket goes into that session's own event stream as
    `add_to_cart` events, because that is where a real basket is:
    `api/app/routers/experiments.py::_real_purchase_share` derives the panel's
    purchase share from exactly the `add_to_cart` events `fuse_session` is
    fusing, so a real shopper's basket is *necessarily* in the fused stream and
    the panel's attention *necessarily* carries an interaction component.
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

            for index in rng.choice(len(sku_ids), size=basket, p=buy):
                sku_id = sku_ids[index]
                bought[sku_id] = bought.get(sku_id, 0) + 1
                assert sku_id in slot_of_sku  # every purchasable sku sits in a slot
                t_ms += 500
                events.append({
                    "t_ms": t_ms,
                    "type": "add_to_cart",
                    "station_id": bay_of_slot[slot_of_sku[sku_id]],
                    "payload": {"sku_id": sku_id, "slot_id": slot_of_sku[sku_id]},
                })

            fused_sessions.append(fuse_session(events, slot_ids))

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



def _fake_planogram(slot_to_sku: Mapping[str, Any]) -> Dict[str, Any]:
    """A minimal planogram for the hand-built persona results below: just the
    bays -> shelves -> slots -> sku_id path `fusion.purchase_slot_matrix`
    reads, so those tests need no seed data. A slot mapped to None is an empty
    shelf position (CLAUDE.md: those are real slot objects)."""
    return {
        "planogram_id": "fake",
        "bays": [{
            "bay_id": "B1",
            "shelves": [{
                "shelf_id": "B1S1",
                "slots": [{"slot_id": slot_id, "sku_id": sku_id, "facings": 1}
                          for slot_id, sku_id in slot_to_sku.items()],
            }],
            "ad_slots": [],
        }],
        "skus": [],
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


def test_calibration_recovers_the_generating_mix(basis_a, panel_a, vocabulary, variant_a):
    """PLAN S17: fake sessions generated from mix [0.5, 0.2, 0.2, 0.1] ->
    calibration must recover EACH share within ±0.1. This gates Track D."""
    slot_ids, sku_ids = vocabulary
    real_attention, real_purchase_share = panel_a

    started = time.perf_counter()
    result = calibrate(real_attention, real_purchase_share, basis_a,
                       planogram=variant_a, slot_ids=slot_ids, sku_ids=sku_ids)
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


def test_the_fixture_is_noisy_in_both_terms(basis_a, panel_a, vocabulary, variant_a):
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
                        planogram=variant_a, slot_ids=slot_ids, sku_ids=sku_ids)
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
                              planogram=variant_a, slot_ids=slot_ids,
                              sku_ids=sku_ids)["shares"]
        print(f"\nseed {seed}: {[round(recovered[p], 2) for p in sorted(recovered)]}")

        for persona_id, true_share in TRUE_MIX.items():
            assert abs(recovered[persona_id] - true_share) <= TOLERANCE, (
                f"seed {seed}, {persona_id}: {recovered[persona_id]} vs {true_share}")


# ---------------------------------------------------------------------------
# the objective is genuinely minimised, and both terms carry weight
# ---------------------------------------------------------------------------


def test_the_returned_objective_beats_the_true_and_uniform_mixes(
        basis_a, panel_a, vocabulary, variant_a):
    """An exhaustive search cannot be beaten by any grid point, and both
    reference mixes are grid points. If the true mix scored better than the
    winner the search would be broken."""
    slot_ids, sku_ids = vocabulary
    real_attention, real_purchase_share = panel_a
    kwargs = dict(planogram=variant_a, slot_ids=slot_ids, sku_ids=sku_ids)

    best = calibrate(real_attention, real_purchase_share, basis_a, **kwargs)
    at_truth = evaluate(TRUE_MIX, real_attention, real_purchase_share, basis_a, **kwargs)
    at_uniform = evaluate(UNIFORM_MIX, real_attention, real_purchase_share, basis_a, **kwargs)

    assert best["objective"] <= at_truth["objective"]
    assert best["objective"] <= at_uniform["objective"]


def test_the_attention_term_alone_moves_the_answer_toward_the_true_mix(
        basis_a, panel_a, vocabulary, variant_a):
    """VACUITY GUARD. With `mae_weight=0` the purchase term is switched off
    entirely, so nothing but the Spearman over slots can drive the search.
    The answer still lands much closer to the generating mix than uniform
    does -- so the recovery in the gating test cannot be the MAE term acting
    alone."""
    slot_ids, sku_ids = vocabulary
    real_attention, real_purchase_share = panel_a

    attention_only = calibrate(real_attention, real_purchase_share, basis_a,
                               planogram=variant_a, slot_ids=slot_ids, sku_ids=sku_ids,
                               mae_weight=0.0)["shares"]
    error = max(abs(attention_only[p] - TRUE_MIX[p]) for p in TRUE_MIX)
    uniform_error = max(abs(UNIFORM_MIX[p] - TRUE_MIX[p]) for p in TRUE_MIX)
    print(f"\nattention-only mix {[round(attention_only[p], 2) for p in sorted(attention_only)]} "
          f"max err {error:.3f} (uniform: {uniform_error:.3f})")

    assert error < uniform_error
    assert error <= 0.15


def test_the_recovered_mix_beats_uniform_on_the_attention_term(
        basis_a, panel_a, vocabulary, variant_a):
    """VACUITY GUARD, the other direction: the mix the full objective picks is
    better than uniform on the Spearman *specifically*, not only on the
    combined score. A winner chosen by the MAE term alone would have no
    reason to be."""
    slot_ids, sku_ids = vocabulary
    real_attention, real_purchase_share = panel_a
    kwargs = dict(planogram=variant_a, slot_ids=slot_ids, sku_ids=sku_ids)

    best = calibrate(real_attention, real_purchase_share, basis_a, **kwargs)
    at_uniform = evaluate(UNIFORM_MIX, real_attention, real_purchase_share, basis_a, **kwargs)

    assert best["attention_spearman"] > at_uniform["attention_spearman"]


def test_the_objective_is_one_minus_rho_plus_five_mae(basis_a, panel_a, vocabulary, variant_a):
    """The formula PLAN S17 specifies, checked against the components the same
    call reports."""
    slot_ids, sku_ids = vocabulary
    real_attention, real_purchase_share = panel_a

    result = calibrate(real_attention, real_purchase_share, basis_a,
                       planogram=variant_a, slot_ids=slot_ids, sku_ids=sku_ids)

    assert result["objective"] == pytest.approx(
        (1.0 - result["attention_spearman"]) + 5.0 * result["purchase_share_mae"])
    assert result["mae_weight"] == 5.0
    assert result["step"] == 0.05
    assert result["n_candidates"] == 1771


def test_calibrate_is_deterministic(basis_a, panel_a, vocabulary, variant_a):
    """No RNG anywhere in the search: the same panel and basis give a
    bit-identical result, which is what lets a calibration be frozen and
    re-verified from the committed data."""
    slot_ids, sku_ids = vocabulary
    real_attention, real_purchase_share = panel_a
    kwargs = dict(planogram=variant_a, slot_ids=slot_ids, sku_ids=sku_ids)

    assert (calibrate(real_attention, real_purchase_share, basis_a, **kwargs)
            == calibrate(real_attention, real_purchase_share, basis_a, **kwargs))


def test_ties_break_to_the_lexicographically_smallest_share_tuple():
    """Four personas with identical vectors make every candidate score
    identically. The winner must then be the first candidate in the grid's
    ascending lexicographic order -- all weight on the last persona id --
    rather than whichever the dict happened to yield first."""
    per_persona = _synthetic_results(4)

    result = calibrate({"S1": 0.5, "S2": 0.3, "S3": 0.2}, {"K1": 0.6, "K2": 0.4},
                       per_persona,
                       planogram=_fake_planogram({"S1": "K1", "S2": "K2", "S3": None}),
                       slot_ids=["S1", "S2", "S3"], sku_ids=["K1", "K2"])

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
                       planogram=variant_a, slot_ids=slot_ids, sku_ids=sku_ids)["shares"]
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
                       planogram=variant_b, slot_ids=slot_ids_b, sku_ids=sku_ids_b)

    assert frozen == before  # the caller's mapping is untouched
    assert holdout["shares"] == before  # and so are the shares it reports
    assert holdout["variant_id"] == "B"
    assert -1.0 <= holdout["attention_spearman"] <= 1.0
    assert holdout["purchase_share_mae"] >= 0.0
    assert holdout["objective"] == pytest.approx(
        (1.0 - holdout["attention_spearman"]) + 5.0 * holdout["purchase_share_mae"])


def test_calibrate_echoes_the_variant_it_was_fitted_on(basis_a, panel_a, vocabulary, variant_a):
    """Fit-vs-holdout has to be reportable separately (PLAN S17: "always report
    fit and holdout separately"), so the result says which variant produced
    it. The caller passes A."""
    slot_ids, sku_ids = vocabulary

    result = calibrate(panel_a[0], panel_a[1], basis_a, planogram=variant_a,
                       slot_ids=slot_ids, sku_ids=sku_ids)

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
                  planogram=_fake_planogram({"S1": "K1"}),
                  slot_ids=["S1"], sku_ids=["K1"])


def test_persona_results_from_different_seeds_are_refused():
    """Same reason: `combine()` refuses mixed seeds, so this does too."""
    per_persona = _synthetic_results(2)
    per_persona["p1"] = {**per_persona["p1"], "seed": 99}

    with pytest.raises(ValueError, match="seed"):
        calibrate({"S1": 1.0}, {"K1": 1.0}, per_persona,
                  planogram=_fake_planogram({"S1": "K1"}),
                  slot_ids=["S1"], sku_ids=["K1"])


def test_an_empty_persona_bundle_is_refused():
    with pytest.raises(ValueError, match="persona"):
        calibrate({"S1": 1.0}, {"K1": 1.0}, {},
                  planogram=_fake_planogram({"S1": "K1"}),
                  slot_ids=["S1"], sku_ids=["K1"])


def test_evaluate_refuses_shares_that_do_not_name_every_persona():
    per_persona = _synthetic_results(4)

    with pytest.raises(ValueError, match="persona"):
        evaluate({"p0": 1.0}, {"S1": 1.0}, {"K1": 1.0}, per_persona,
                 planogram=_fake_planogram({"S1": "K1"}),
                 slot_ids=["S1"], sku_ids=["K1"])


def test_evaluate_refuses_shares_that_do_not_sum_to_one():
    per_persona = _synthetic_results(2)

    with pytest.raises(ValueError, match="sum to 1"):
        evaluate({"p0": 0.5, "p1": 0.2}, {"S1": 1.0}, {"K1": 1.0}, per_persona,
                 planogram=_fake_planogram({"S1": "K1"}),
                 slot_ids=["S1"], sku_ids=["K1"])


def test_evaluate_refuses_a_negative_share():
    per_persona = _synthetic_results(2)

    with pytest.raises(ValueError, match="negative"):
        evaluate({"p0": 1.5, "p1": -0.5}, {"S1": 1.0}, {"K1": 1.0}, per_persona,
                 planogram=_fake_planogram({"S1": "K1"}),
                 slot_ids=["S1"], sku_ids=["K1"])


def test_calibrate_returns_plain_floats(basis_a, panel_a, vocabulary, variant_a):
    """RESULTS.md and schemas/metrics.schema.json's `calibrated_shares` want
    JSON numbers, not numpy scalars."""
    slot_ids, sku_ids = vocabulary

    result = calibrate(panel_a[0], panel_a[1], basis_a, planogram=variant_a,
                       slot_ids=slot_ids, sku_ids=sku_ids)

    assert all(isinstance(share, float) for share in result["shares"].values())
    assert isinstance(result["objective"], float)
    assert isinstance(result["attention_spearman"], float)
    assert isinstance(result["purchase_share_mae"], float)
    assert json.loads(json.dumps(result)) == result


# ---------------------------------------------------------------------------
# the metric mismatch this module used to absorb into the shares
# ---------------------------------------------------------------------------


def _legacy_calibrate(
    real_attention: Mapping[str, float],
    real_purchase_share: Mapping[str, float],
    per_persona: Mapping[str, Mapping[str, Any]],
    *,
    slot_ids: Sequence[str],
    sku_ids: Sequence[str],
    mae_weight: float = 5.0,
) -> Dict[str, float]:
    """The comparison this change replaces, kept test-side so its bias can be
    measured rather than asserted from memory.

    Identical to `calibrate` in every respect -- same grid, same objective,
    same tie rule -- except that the synthetic attention it scores is the
    population's RAW `fixation_prob` (SPEC M5 as written), not the fused
    synthetic vector. Nothing in analytics/ calls this; it exists only so
    `test_fusing_the_synthetic_side_reduces_the_basket_injection_bias` can
    report a before number next to an after number.
    """
    persona_ids = sorted(per_persona)
    best_objective = None
    best_shares: tuple = ()

    for shares in share_grid(0.05, len(persona_ids)):
        mixed = mixture(per_persona, dict(zip(persona_ids, shares)),
                        slot_ids=slot_ids, sku_ids=sku_ids)
        rho = attention_spearman(real_attention, mixed["fixation_prob"], slot_ids)
        mae = purchase_share_mae(real_purchase_share, mixed["purchase_share"], sku_ids)
        objective = (1.0 - rho) + mae_weight * mae
        if best_objective is None or objective < best_objective - 1e-12:
            best_objective, best_shares = objective, shares

    return dict(zip(persona_ids, best_shares))


def _max_share_error(recovered: Mapping[str, float]) -> float:
    """The bar PLAN S17 states: worst per-share distance from the true mix."""
    return max(abs(recovered[persona_id] - share) for persona_id, share in TRUE_MIX.items())


def _total_share_error(recovered: Mapping[str, float]) -> float:
    """How much share the recovered mix moved in total (L1), reported next to
    the max because "displaced by 0.15" can mean either."""
    return sum(abs(recovered[persona_id] - share) for persona_id, share in TRUE_MIX.items())


def test_fusing_the_synthetic_side_reduces_the_basket_injection_bias(
        basis_a, panel_a, vocabulary, variant_a):
    """THE POINT OF THE CHANGE, measured rather than argued.

    Same panel (the realistic one, baskets in the fused stream), same basis,
    same objective, same grid. The ONLY difference between the two numbers is
    which synthetic vector the Spearman is taken against: the population's raw
    `fixation_prob`, or `fuse_synthetic` of the same population result.

    Measured over the four panel seeds this file uses (max per-share error):

        seed        7      11      23      31     mean
        raw     0.100   0.150   0.100   0.200   0.1375
        fused   0.050   0.000   0.050   0.050   0.0375

    The raw comparison misses PLAN S17's +/-0.1 bar on half of them and
    reproduces the ~0.15 S17 reported; the fused comparison is inside it on
    every one.

    A residual mismatch remains, and it shows up when the panel gets much
    bigger than the one this project will collect. At 600 sessions x 400
    dwells the fused comparison lands at 0.150 on seeds 7, 11 and 23 while the
    raw one lands at 0.100, 0.050 and 0.100. The real interaction channel is a
    trimmed mean of per-session MAX weights -- saturating (a slot bought twice
    in one session still scores 1.0) and truncated (`trimmed_mean` drops the
    top 10 % of sessions per slot, which is most of the non-zero ones for a
    sparse channel) -- whereas the synthetic one is a smooth population
    purchase share. At panel sizes near PLAN S21's ">= 60 accepted" that
    second-order difference is well under the sampling noise; at 600 sessions
    it is what is left. Closing it would mean changing how the REAL side
    aggregates interactions, which is not this change.
    """
    slot_ids, sku_ids = vocabulary
    real_attention, real_purchase_share = panel_a

    before = _legacy_calibrate(real_attention, real_purchase_share, basis_a,
                               slot_ids=slot_ids, sku_ids=sku_ids)
    after = calibrate(real_attention, real_purchase_share, basis_a,
                      planogram=variant_a, slot_ids=slot_ids, sku_ids=sku_ids)["shares"]

    print(f"\ntrue mix                 {[TRUE_MIX[p] for p in sorted(TRUE_MIX)]}")
    print(f"before (raw fixation)    {[round(before[p], 2) for p in sorted(before)]} "
          f"max err {_max_share_error(before):.3f} "
          f"total displaced {_total_share_error(before):.3f}")
    print(f"after  (fused synthetic) {[round(after[p], 2) for p in sorted(after)]} "
          f"max err {_max_share_error(after):.3f} "
          f"total displaced {_total_share_error(after):.3f}")

    # Closer by at least a full grid step -- the smallest improvement a 0.05
    # grid can even express, so this is not a rounding artefact.
    assert _max_share_error(after) <= _max_share_error(before) - DEFAULT_STEP + 1e-9
    assert _total_share_error(after) < _total_share_error(before)
    # ...and inside PLAN S17's +/-0.1 recovery tolerance, which is the bar the
    # gating test holds the search to.
    assert _max_share_error(after) <= TOLERANCE


def test_the_grid_search_scores_the_fused_synthetic_vector(basis_a, panel_a, vocabulary,
                                                           variant_a):
    """`evaluate` must report the Spearman of the real panel against
    `fuse_synthetic` of the combined population result -- not against raw
    `fixation_prob`, and not against some third thing computed only here.

    Checked against the real `combine()` and the real `fuse_synthetic`, so a
    pass proves the wiring rather than the module agreeing with itself.
    """
    slot_ids, sku_ids = vocabulary
    real_attention, real_purchase_share = panel_a
    persona_ids = sorted(basis_a)
    results = [basis_a[persona_id] for persona_id in persona_ids]

    for shares in [(0.5, 0.2, 0.2, 0.1), (0.25, 0.25, 0.25, 0.25), (1.0, 0.0, 0.0, 0.0)]:
        scored = evaluate(dict(zip(persona_ids, shares)), real_attention, real_purchase_share,
                          basis_a, planogram=variant_a, slot_ids=slot_ids, sku_ids=sku_ids)
        expected_vector = fuse_synthetic(combine(results, list(shares)), variant_a, slot_ids)

        assert scored["attention_spearman"] == pytest.approx(
            attention_spearman(real_attention, expected_vector, slot_ids), abs=1e-12)
        assert scored["attention_spearman"] != pytest.approx(
            attention_spearman(real_attention,
                               combine(results, list(shares))["fixation_prob"], slot_ids))


def test_mixing_then_fusing_is_not_the_same_as_fusing_then_mixing(basis_a, vocabulary,
                                                                  variant_a):
    """Why the grid fuses the MIXED vectors instead of mixing pre-fused ones.

    `combine()` is linear in the shares, so the mixed `fixation_prob` and
    `purchase_share` are one matrix product away -- but `fuse_synthetic`
    normalises, and normalisation is not linear. Each persona spends a
    different fraction of its fixations on ad slots, so restricting
    `fixation_prob` to the product slots leaves each persona summing to a
    different total (0.9903 to 0.9987 on variant A) and

        sum_p share_p * (v_p / S_p)   !=   (sum_p share_p * v_p) / (sum_p share_p * S_p)

    The right-hand side is what `fuse_synthetic(combine(...))` computes, and it
    is what `calibrate` must reproduce for every candidate, so the grid mixes
    first and fuses after. That order is exactly as fast -- the fusion is two
    array operations over the whole candidate matrix, not 1,771 separate calls
    -- so there is no correctness/speed trade-off to make here.

    This test pins the inequivalence down so nobody "optimises" the order back.
    """
    slot_ids, _sku_ids = vocabulary
    persona_ids = sorted(basis_a)
    results = [basis_a[persona_id] for persona_id in persona_ids]
    shares = (0.5, 0.2, 0.2, 0.1)

    mix_then_fuse = fuse_synthetic(combine(results, list(shares)), variant_a, slot_ids)
    per_persona_fused = [fuse_synthetic(result, variant_a, slot_ids) for result in results]
    fuse_then_mix = {
        slot_id: sum(share * fused[slot_id] for share, fused in zip(shares, per_persona_fused))
        for slot_id in slot_ids
    }

    difference = max(abs(mix_then_fuse[s] - fuse_then_mix[s]) for s in slot_ids)
    print(f"\nmax |mix-then-fuse - fuse-then-mix| = {difference:.3e}")

    assert difference > 1e-9, "the two orders agree here -- the guard below is vacuous"
    # The implemented order is the one that reproduces fuse_synthetic(combine(...)).
    assert mix_then_fuse != pytest.approx(fuse_then_mix, abs=1e-9)
