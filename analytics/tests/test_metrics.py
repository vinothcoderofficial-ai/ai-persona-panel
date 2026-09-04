"""Tests for analytics/metrics.py -- real-vs-synthetic comparison metrics.

Every expected number below is hand-computed in the comment above its
assertion, independent of metrics.py's implementation.
"""

import pytest

from analytics.metrics import attention_spearman, purchase_share_mae

SLOT_IDS = ["S1", "S2", "S3", "S4"]


def test_perfect_rank_agreement_gives_rho_one():
    """Both vectors increase in the same order across all 4 slots (ranks
    1,2,3,4 in both, just at different scale) -> Spearman's rho = 1.0.
    """
    real = {"S1": 1.0, "S2": 2.0, "S3": 3.0, "S4": 4.0}
    synth = {"S1": 10.0, "S2": 20.0, "S3": 30.0, "S4": 40.0}

    assert attention_spearman(real, synth, SLOT_IDS) == pytest.approx(1.0)


def test_perfectly_reversed_ranks_give_rho_minus_one():
    """synth ranks are the exact reverse of real's (4,3,2,1 vs 1,2,3,4) -> rho = -1.0."""
    real = {"S1": 1.0, "S2": 2.0, "S3": 3.0, "S4": 4.0}
    synth = {"S1": 40.0, "S2": 30.0, "S3": 20.0, "S4": 10.0}

    assert attention_spearman(real, synth, SLOT_IDS) == pytest.approx(-1.0)


def test_hand_computed_intermediate_rank_correlation():
    """real values [10,20,30,40] for S1..S4 -> real ranks = [1,2,3,4].
    synth values [20,10,40,30] for S1..S4  -> synth ranks = [2,1,4,3].

    d_i = real_rank_i - synth_rank_i:
      S1: 1-2 = -1 -> d^2 = 1
      S2: 2-1 =  1 -> d^2 = 1
      S3: 3-4 = -1 -> d^2 = 1
      S4: 4-3 =  1 -> d^2 = 1
      sum(d^2) = 4, n = 4, no ties so the simplified formula is exact.

    rho = 1 - 6*sum(d^2) / (n*(n^2-1)) = 1 - 6*4/(4*15) = 1 - 24/60 = 1 - 0.4 = 0.6
    """
    real = {"S1": 10.0, "S2": 20.0, "S3": 30.0, "S4": 40.0}
    synth = {"S1": 20.0, "S2": 10.0, "S3": 40.0, "S4": 30.0}

    assert attention_spearman(real, synth, SLOT_IDS) == pytest.approx(0.6)


def test_constant_vector_returns_zero_not_nan():
    """Spearman's rho is mathematically undefined when a vector has zero
    variance (division by zero inside the correlation formula); scipy
    returns NaN in that case. This must come back as 0.0 instead -- whether
    it's the real vector that's constant, the synth vector, or (the
    realistic case: two untouched fusion outputs) both sides being the same
    all-zero vector.
    """
    varied = {"S1": 1.0, "S2": 2.0, "S3": 3.0, "S4": 4.0}
    constant = {"S1": 5.0, "S2": 5.0, "S3": 5.0, "S4": 5.0}
    all_zero = {"S1": 0.0, "S2": 0.0, "S3": 0.0, "S4": 0.0}

    assert attention_spearman(constant, varied, SLOT_IDS) == 0.0
    assert attention_spearman(varied, constant, SLOT_IDS) == 0.0
    assert attention_spearman(all_zero, all_zero, SLOT_IDS) == 0.0


def test_purchase_share_mae_hand_computed_example():
    """real = {A:0.5, B:0.3, C:0.2}, synth = {A:0.4, B:0.4, C:0.2}
    abs diffs = [|0.5-0.4|, |0.3-0.4|, |0.2-0.2|] = [0.1, 0.1, 0.0]
    MAE = (0.1 + 0.1 + 0.0) / 3 = 0.2 / 3 = 0.0666...
    """
    real = {"A": 0.5, "B": 0.3, "C": 0.2}
    synth = {"A": 0.4, "B": 0.4, "C": 0.2}

    assert purchase_share_mae(real, synth) == pytest.approx(0.2 / 3)


def test_purchase_share_mae_treats_key_missing_from_either_side_as_zero():
    """B is absent from synth (treated as 0.0 share there); C is absent from
    real (treated as 0.0 share there). sku_ids defaults to the union {A,B,C}.

      A: |0.6 - 0.6| = 0.0
      B: |0.4 - 0.0| = 0.4   (missing from synth)
      C: |0.0 - 0.1| = 0.1   (missing from real)
      MAE = (0.0 + 0.4 + 0.1) / 3 = 0.5 / 3 = 0.1666...
    """
    real = {"A": 0.6, "B": 0.4}
    synth = {"A": 0.6, "C": 0.1}

    assert purchase_share_mae(real, synth) == pytest.approx(0.5 / 3)


def test_purchase_share_mae_respects_explicit_sku_ids():
    """Passing sku_ids restricts the comparison to that list even though
    synth has an extra key (C) that the default union behaviour would
    otherwise pull in.

      A: |0.5 - 0.5| = 0.0
      B: |0.5 - 0.3| = 0.2
      MAE over [A, B] only = (0.0 + 0.2) / 2 = 0.1   (C=0.2 excluded)
    """
    real = {"A": 0.5, "B": 0.5}
    synth = {"A": 0.5, "B": 0.3, "C": 0.2}

    assert purchase_share_mae(real, synth, sku_ids=["A", "B"]) == pytest.approx(0.1)


def test_purchase_share_mae_identical_inputs_is_zero():
    shares = {"A": 0.33, "B": 0.33, "C": 0.34}

    assert purchase_share_mae(shares, shares) == 0.0
