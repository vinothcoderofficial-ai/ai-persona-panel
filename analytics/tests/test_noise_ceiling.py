"""Tests for analytics/noise_ceiling.py -- the real panel's split-half
repeatability, and the `relative_agreement` ratio that benchmarks the
synthetic panel against it (PLAN S17: "200 half-splits -> mean rho +
2.5/97.5 percentiles; relative_agreement = min(1, rho / ceiling)").

The ceiling answers "how well does the real panel agree with ITSELF?".
Without it a synthetic rho of 0.6 is uninterpretable: it could be a poor
model, or it could be the best any model could do against data this noisy.

Every fixture here is a hand-built panel of fused-attention mappings -- the
shape `analytics/fusion.fuse_session` returns -- so these tests exercise the
splitting and percentile logic, not the simulator.
"""

import numpy as np
import pytest

from analytics.noise_ceiling import noise_ceiling, relative_agreement

SLOT_IDS = ["S1", "S2", "S3", "S4", "S5", "S6"]

# The real demo aisle has 24 occupied slots. A Spearman over six points is
# coarse enough that a single no-signal panel can land anywhere in
# [-0.7, +0.5] by chance, so the "pure noise centres on zero" claim is made
# over a realistic vocabulary rather than the small one the other tests use.
WIDE_SLOT_IDS = [f"S{i}" for i in range(1, 25)]

# One clean, strictly-ranked attention vector. Every "identical sessions"
# panel below repeats it: no ties, so its split-half Spearman is exactly 1.
RANKED = {"S1": 0.30, "S2": 0.25, "S3": 0.20, "S4": 0.15, "S5": 0.07, "S6": 0.03}


def _identical_panel(n: int) -> list[dict[str, float]]:
    """`n` copies of the same session. Any two halves are identical."""
    return [dict(RANKED) for _ in range(n)]


def _noise_panel(n: int, seed: int, slot_ids=WIDE_SLOT_IDS) -> list[dict[str, float]]:
    """`n` sessions of pure noise: every slot drawn iid, no shared signal.

    The slots are exchangeable, so the two half-means differ only by
    independent noise and their rank correlation has expectation 0.
    """
    rng = np.random.default_rng(seed)
    return [
        {slot_id: float(rng.uniform(0.0, 1.0)) for slot_id in slot_ids}
        for _ in range(n)
    ]


def _signal_plus_noise_panel(n: int, seed: int) -> list[dict[str, float]]:
    """`n` sessions that share the RANKED signal but each carry their own
    noise -- the realistic case, where the ceiling lands strictly between 0
    and 1."""
    rng = np.random.default_rng(seed)
    return [
        {
            slot_id: float(max(0.0, RANKED[slot_id] + rng.normal(0.0, 0.12)))
            for slot_id in SLOT_IDS
        }
        for _ in range(n)
    ]


# ---------------------------------------------------------------------------
# noise_ceiling
# ---------------------------------------------------------------------------


def test_identical_sessions_give_a_ceiling_of_exactly_one():
    """Every session carries the same strictly-ranked vector, so whichever
    sessions land in each half their trimmed means are identical and the
    Spearman between the halves is 1.0 on all 200 splits. Mean 1.0, and a
    degenerate interval [1.0, 1.0] -- a panel with no disagreement to
    measure."""
    result = noise_ceiling(_identical_panel(8), SLOT_IDS, seed=1)

    assert result["spearman_mean"] == pytest.approx(1.0)
    assert result["ci95"] == pytest.approx([1.0, 1.0])


def test_pure_noise_sessions_give_a_ceiling_near_zero():
    """No shared signal between sessions -> the two half-means rank the slots
    independently -> split-half rho has expectation 0, and 200 splits over a
    24-slot vocabulary put the mean close to it.

    The two halves are complementary rather than independent samples, which
    is worth stating because it looks like it should bias the answer: what a
    session contributes to one half it withholds from the other. It does not.
    Over slots, the shared component raises the covariance and the withheld
    noise cancels it exactly, so a panel with no signal centres on 0 -- as
    measured here, and as `test_a_signal_panel_lands_strictly_between_the_two_extremes`
    shows does not happen once there IS a shared ranking.
    """
    result = noise_ceiling(_noise_panel(24, seed=5), WIDE_SLOT_IDS, seed=1)

    assert abs(result["spearman_mean"]) < 0.20


def test_a_noise_panel_yields_no_relative_agreement():
    """The two functions together: a panel that does not repeat has a ceiling
    at or below zero, and `relative_agreement` then reports 0.0 for any
    synthetic rho rather than dividing by it. A ceiling can legitimately come
    out negative -- it means "no repeatable signal", not "broken code" -- and
    this is what stops that turning into a positive-looking accuracy claim."""
    ceiling = noise_ceiling(_noise_panel(24, seed=5), WIDE_SLOT_IDS, seed=1)

    if ceiling["spearman_mean"] <= 0.0:
        assert relative_agreement(0.6, ceiling["spearman_mean"]) == 0.0
    else:
        # A tiny positive ceiling makes even a modest rho hit the clamp.
        assert relative_agreement(0.6, ceiling["spearman_mean"]) == 1.0


def test_a_signal_panel_lands_strictly_between_the_two_extremes():
    """The realistic case: a shared ranking plus per-session noise repeats
    well but not perfectly."""
    result = noise_ceiling(_signal_plus_noise_panel(20, seed=3), SLOT_IDS, seed=1)

    assert 0.2 < result["spearman_mean"] < 1.0


def test_the_same_seed_reproduces_the_answer_exactly():
    """scripts/eval.py regenerates RESULTS.md from the committed sessions;
    the same seed must give bit-identical numbers, not merely close ones."""
    panel = _signal_plus_noise_panel(20, seed=3)

    first = noise_ceiling(panel, SLOT_IDS, seed=7)
    second = noise_ceiling(panel, SLOT_IDS, seed=7)

    assert first == second


def test_a_different_seed_gives_a_different_answer():
    """The splits are random, so a different seed draws different halves.
    (On a noisy panel; an identical-session panel would tie at 1.0 for every
    seed, which is why this fixture carries noise.)"""
    panel = _signal_plus_noise_panel(20, seed=3)

    assert noise_ceiling(panel, SLOT_IDS, seed=7) != noise_ceiling(panel, SLOT_IDS, seed=8)


def test_ci95_brackets_the_mean_and_n_splits_is_echoed():
    """The reported block is exactly the `noise_ceiling` object of
    schemas/metrics.schema.json: spearman_mean, a two-element ci95, and the
    split count that produced them."""
    result = noise_ceiling(_signal_plus_noise_panel(20, seed=3), SLOT_IDS,
                           n_splits=200, seed=1)

    assert set(result) == {"spearman_mean", "ci95", "n_splits"}
    assert result["n_splits"] == 200
    assert isinstance(result["n_splits"], int)
    assert len(result["ci95"]) == 2
    assert result["ci95"][0] <= result["spearman_mean"] <= result["ci95"][1]


def test_n_splits_is_honoured():
    """A caller asking for fewer splits gets fewer, and the echo reports what
    actually ran rather than the default."""
    result = noise_ceiling(_signal_plus_noise_panel(20, seed=3), SLOT_IDS,
                           n_splits=25, seed=1)

    assert result["n_splits"] == 25


def test_fewer_than_four_sessions_raises():
    """Three sessions split into halves of one, so each "half mean" is a
    single session and the ceiling would measure two individuals rather than
    the panel's repeatability. That number would be misleading, so it is
    refused rather than returned."""
    with pytest.raises(ValueError, match="at least 4"):
        noise_ceiling(_signal_plus_noise_panel(3, seed=3), SLOT_IDS, seed=1)


def test_four_sessions_are_accepted():
    """Four is the documented minimum, not the first rejected value."""
    result = noise_ceiling(_signal_plus_noise_panel(4, seed=3), SLOT_IDS, seed=1)

    assert -1.0 <= result["spearman_mean"] <= 1.0


def test_n_splits_below_one_raises():
    """Zero splits would leave nothing to average or take percentiles of."""
    with pytest.raises(ValueError, match="n_splits"):
        noise_ceiling(_identical_panel(8), SLOT_IDS, n_splits=0, seed=1)


def test_no_result_is_ever_nan():
    """A panel where every session is all-zero has a constant vector on both
    sides of every split. `metrics.attention_spearman` already returns 0.0
    rather than NaN there; this asserts the ceiling inherits that instead of
    propagating a NaN into RESULTS.md."""
    flat = [{slot_id: 0.0 for slot_id in SLOT_IDS} for _ in range(8)]

    result = noise_ceiling(flat, SLOT_IDS, seed=1)

    assert result["spearman_mean"] == 0.0
    assert not any(np.isnan(value) for value in result["ci95"])


# ---------------------------------------------------------------------------
# relative_agreement
# ---------------------------------------------------------------------------


def test_relative_agreement_is_the_plain_ratio_below_the_ceiling():
    """rho 0.45 against a ceiling of 0.90 -> the synthetic panel reached half
    of what the real panel manages against itself."""
    assert relative_agreement(0.45, 0.90) == pytest.approx(0.5)


def test_relative_agreement_clamps_above_at_one():
    """A synthetic panel that out-agrees the real panel's own repeatability
    has not beaten the data -- it has hit the ceiling. Reporting 1.2 would
    read as "120 % accurate"."""
    assert relative_agreement(0.95, 0.80) == 1.0


def test_relative_agreement_clamps_a_negative_rho_to_zero():
    """A negative rho divided by a positive ceiling is a negative ratio; the
    lower clamp keeps it at 0.0 so an anti-correlated prediction never reads
    as partial agreement."""
    assert relative_agreement(-0.60, 0.80) == 0.0


def test_relative_agreement_of_a_negative_rho_and_a_negative_ceiling_is_zero():
    """Two negatives would otherwise divide into a positive ratio -- the exact
    trap the lower clamp exists for. A ceiling at or below zero means the
    panel does not agree with itself at all, so there is nothing to be a
    fraction of."""
    assert relative_agreement(-0.60, -0.80) == 0.0


def test_relative_agreement_returns_zero_for_a_zero_ceiling():
    """Guards the division: a panel with no self-agreement gives 0.0, never
    a ZeroDivisionError and never an infinity."""
    assert relative_agreement(0.75, 0.0) == 0.0


def test_relative_agreement_returns_a_float():
    """RESULTS.md and schemas/metrics.schema.json want a plain number."""
    value = relative_agreement(np.float64(0.45), np.float64(0.90))

    assert isinstance(value, float)
