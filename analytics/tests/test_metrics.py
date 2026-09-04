"""Tests for analytics/metrics.py -- real-vs-synthetic comparison metrics.

Every expected number below is hand-computed in the comment above its
assertion, independent of metrics.py's implementation.
"""

import math

import pytest

from analytics.metrics import (
    ad_slot_index_spearman,
    attention_spearman,
    decision_agreement,
    heatmap_kl,
    purchase_share_mae,
)

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


# ---------------------------------------------------------------------------
# S16 -- heatmap KL, Ad Slot Index Spearman, decision agreement.
# ---------------------------------------------------------------------------

AD_SLOT_IDS = ["AD1", "AD2", "AD3", "AD4"]


def test_kl_of_identical_distributions_is_exactly_zero():
    """KL(P || P) = sum p*log(p/p) = sum p*log(1) = 0. Because the two vectors
    go through exactly the same epsilon-then-renormalise arithmetic, each
    ratio is the float 1.0 and each log term is exactly 0.0 -- so this is an
    exact 0.0, not an approximate one, and is asserted as such.
    """
    shares = {"S1": 0.4, "S2": 0.3, "S3": 0.2, "S4": 0.1}

    assert heatmap_kl(shares, shares, SLOT_IDS) == 0.0


def test_kl_hand_computed_two_slot_case():
    """Worked by hand over 2 slots with eps = 1.0, chosen so the epsilon
    arithmetic stays exact and checkable:

    real  = [3, 1] -> +eps -> [4, 2] -> sum 6 -> P = [4/6, 2/6] = [2/3, 1/3]
    synth = [1, 1] -> +eps -> [2, 2] -> sum 4 -> Q = [2/4, 2/4] = [1/2, 1/2]

    KL(P || Q) = (2/3)*ln((2/3)/(1/2)) + (1/3)*ln((1/3)/(1/2))
               = (2/3)*ln(4/3)         + (1/3)*ln(2/3)
               = (2/3)*(0.287682072452) + (1/3)*(-0.405465108108)
               = 0.191788048301        - 0.135155036036
               = 0.056633012265        (nats)
    """
    real = {"S1": 3.0, "S2": 1.0}
    synth = {"S1": 1.0, "S2": 1.0}

    result = heatmap_kl(real, synth, ["S1", "S2"], eps=1.0)

    assert result == pytest.approx(0.056633012265, rel=1e-9)


def test_kl_is_measured_in_nats_not_bits():
    """The value above in bits would be 0.056633.../ln(2) = 0.0817099...,
    which this must NOT return. Pinning the base stops a later 'tidy-up'
    silently switching to log2 and shifting every published number.
    """
    real = {"S1": 3.0, "S2": 1.0}
    synth = {"S1": 1.0, "S2": 1.0}

    result = heatmap_kl(real, synth, ["S1", "S2"], eps=1.0)

    assert result == pytest.approx(0.056633012265, rel=1e-9)
    assert result != pytest.approx(0.0817099, rel=1e-4)


def test_kl_is_finite_when_a_slot_has_zero_real_attention():
    """A slot nobody looked at gives p = 0, and the naive term 0*log(0/q)
    evaluates to 0 * -inf = NaN in floating point. Adding eps BEFORE
    renormalising is what avoids that.

    real  = [1, 0] -> +1e-3 -> [1.001, 0.001] -> sum 1.002
    synth = [0.5, 0.5] -> +1e-3 -> [0.501, 0.501] -> sum 1.002
    P = [0.999001996..., 0.000998003992...], Q = [0.5, 0.5]
    KL = 0.685253713381 (nats)
    """
    real = {"S1": 1.0, "S2": 0.0}
    synth = {"S1": 0.5, "S2": 0.5}

    result = heatmap_kl(real, synth, ["S1", "S2"])

    assert math.isfinite(result)
    assert not math.isnan(result)
    assert result == pytest.approx(0.685253713381, rel=1e-9)


def test_kl_is_finite_when_a_slot_has_zero_synthetic_attention():
    """The genuinely divergent case: q = 0 where p > 0 sends true KL to
    infinity. Epsilon keeps it large but finite, so the metric stays usable.

    real  = [1, 0] -> +1e-3 -> [1.001, 0.001] -> sum 1.002
    synth = [0, 1] -> +1e-3 -> [0.001, 1.001] -> sum 1.002
    KL = 6.894964849616 (nats) -- large, as it should be for two
    distributions that disagree completely, but finite.
    """
    real = {"S1": 1.0, "S2": 0.0}
    synth = {"S1": 0.0, "S2": 1.0}

    result = heatmap_kl(real, synth, ["S1", "S2"])

    assert math.isfinite(result)
    assert result == pytest.approx(6.894964849616, rel=1e-9)


def test_kl_is_non_negative_and_asymmetric():
    """KL >= 0 always (Gibbs' inequality), and KL(P||Q) != KL(Q||P) in
    general -- so argument order is load-bearing and the function must not be
    quietly symmetric.
    """
    real = {"S1": 0.7, "S2": 0.2, "S3": 0.1, "S4": 0.0}
    synth = {"S1": 0.25, "S2": 0.25, "S3": 0.25, "S4": 0.25}

    forward = heatmap_kl(real, synth, SLOT_IDS)
    reverse = heatmap_kl(synth, real, SLOT_IDS)

    assert forward > 0.0
    assert reverse > 0.0
    assert forward != pytest.approx(reverse)


def test_kl_ignores_slots_outside_slot_ids():
    """Only the slots in slot_ids are compared; a key present in the mappings
    but absent from slot_ids (a slot from another bay, say) must not enter
    either distribution. Restricting to [S1, S2] reproduces the hand-computed
    value from test_kl_hand_computed_two_slot_case exactly.
    """
    real = {"S1": 3.0, "S2": 1.0, "OTHER": 500.0}
    synth = {"S1": 1.0, "S2": 1.0, "OTHER": 0.0}

    result = heatmap_kl(real, synth, ["S1", "S2"], eps=1.0)

    assert result == pytest.approx(0.056633012265, rel=1e-9)


def test_kl_of_two_all_zero_vectors_is_zero_not_nan():
    """Two untouched fusion outputs: epsilon makes both uniform, and a
    distribution against itself is 0.0. It must never be NaN.
    """
    all_zero = {"S1": 0.0, "S2": 0.0, "S3": 0.0, "S4": 0.0}

    result = heatmap_kl(all_zero, all_zero, SLOT_IDS)

    assert result == 0.0


def test_kl_of_an_empty_slot_list_is_zero_not_nan():
    """Nothing to compare divides by a zero total; guarded to 0.0."""
    result = heatmap_kl({"S1": 1.0}, {"S1": 1.0}, [])

    assert result == 0.0
    assert not math.isnan(result)


def test_ad_slot_index_spearman_hand_computed_ranking():
    """Spearman over the ad slots only.

    real  AD1..AD4 = [0.4, 0.3, 0.2, 0.1] -> ranks [4, 3, 2, 1]
    synth AD1..AD4 = [0.1, 0.4, 0.2, 0.3] -> ranks [1, 4, 2, 3]

    d_i = real_rank_i - synth_rank_i:
      AD1: 4-1 =  3 -> d^2 = 9
      AD2: 3-4 = -1 -> d^2 = 1
      AD3: 2-2 =  0 -> d^2 = 0
      AD4: 1-3 = -2 -> d^2 = 4
      sum(d^2) = 14, n = 4, no ties so the simplified formula is exact.

    rho = 1 - 6*14 / (4*(16-1)) = 1 - 84/60 = 1 - 1.4 = -0.4
    """
    real = {"AD1": 0.4, "AD2": 0.3, "AD3": 0.2, "AD4": 0.1}
    synth = {"AD1": 0.1, "AD2": 0.4, "AD3": 0.2, "AD4": 0.3}

    assert ad_slot_index_spearman(real, synth, AD_SLOT_IDS) == pytest.approx(-0.4)


def test_ad_slot_index_spearman_only_looks_at_the_ad_slots_it_was_given():
    """Product slots present in the same mappings must not leak into the ad
    correlation. Adding four wildly ranked shelf slots to both mappings must
    leave the hand-computed -0.4 above untouched.
    """
    real = {"AD1": 0.4, "AD2": 0.3, "AD3": 0.2, "AD4": 0.1, "S1": 9.0, "S2": 0.0}
    synth = {"AD1": 0.1, "AD2": 0.4, "AD3": 0.2, "AD4": 0.3, "S1": 0.0, "S2": 9.0}

    assert ad_slot_index_spearman(real, synth, AD_SLOT_IDS) == pytest.approx(-0.4)


def test_ad_slot_index_spearman_returns_zero_not_nan_for_a_constant_vector():
    """No creative anywhere drew attention: the vector is constant and rank
    correlation is undefined, so this returns 0.0 rather than NaN, matching
    attention_spearman's guard.
    """
    varied = {"AD1": 0.4, "AD2": 0.3, "AD3": 0.2, "AD4": 0.1}
    all_zero = {"AD1": 0.0, "AD2": 0.0, "AD3": 0.0, "AD4": 0.0}

    assert ad_slot_index_spearman(all_zero, varied, AD_SLOT_IDS) == 0.0
    assert ad_slot_index_spearman(varied, all_zero, AD_SLOT_IDS) == 0.0


def test_decision_agreement_when_both_panels_pick_the_same_variant():
    """Real KPI: A = 0.52 (max), B = 0.31, C = 0.17 -> winner A
    Synth KPI: A = 0.61 (max), B = 0.22, C = 0.17 -> winner A
    Same argmax -> agree is True. The scale of the KPI is irrelevant; only
    which variant tops it matters.
    """
    real = {"var_A": 0.52, "var_B": 0.31, "var_C": 0.17}
    synth = {"var_A": 0.61, "var_B": 0.22, "var_C": 0.17}

    result = decision_agreement(real, synth, kpi="purchase_share_focal")

    assert result == {
        "kpi": "purchase_share_focal",
        "winner_real": "var_A",
        "winner_synth": "var_A",
        "agree": True,
    }


def test_decision_agreement_when_the_panels_disagree():
    """Real KPI: A = 0.52 (max), B = 0.31 -> winner A
    Synth KPI: A = 0.19, B = 0.55 (max)  -> winner B
    Different argmax -> agree is False. This is the case the whole benchmark
    exists to catch, so it must be reported, not smoothed over.
    """
    real = {"var_A": 0.52, "var_B": 0.31}
    synth = {"var_A": 0.19, "var_B": 0.55}

    result = decision_agreement(real, synth, kpi="purchase_share_focal")

    assert result["winner_real"] == "var_A"
    assert result["winner_synth"] == "var_B"
    assert result["agree"] is False


def test_decision_agreement_returns_exactly_the_schema_block_keys():
    """schemas/metrics.schema.json's decision_agreement block requires
    exactly {kpi, winner_real, winner_synth, agree} and sets
    additionalProperties: false, so anything extra would fail validation.
    `agree` must be a real bool, not a truthy 0/1 -- the schema types it as
    boolean and json.dump would otherwise emit an integer.
    """
    result = decision_agreement({"var_A": 1.0}, {"var_A": 1.0}, kpi="attention_top_slot")

    assert set(result) == {"kpi", "winner_real", "winner_synth", "agree"}
    assert isinstance(result["agree"], bool)
    assert isinstance(result["winner_real"], str)
    assert isinstance(result["winner_synth"], str)
    assert result["kpi"] == "attention_top_slot"


def test_decision_agreement_breaks_ties_by_sorted_variant_id():
    """var_A and var_B tie at 0.4 on the real panel. The winner is the
    lowest variant id in sorted order -- "var_A" -- regardless of the dict's
    insertion order, so a report generated twice never flips.

    The two mappings below hold the same tied values in opposite insertion
    orders; both must resolve to var_A, and repeated calls must agree.
    """
    a_first = {"var_A": 0.4, "var_B": 0.4, "var_C": 0.1}
    b_first = {"var_B": 0.4, "var_A": 0.4, "var_C": 0.1}
    synth = {"var_A": 0.9, "var_B": 0.1, "var_C": 0.0}

    from_a = decision_agreement(a_first, synth, kpi="purchase_share_focal")
    from_b = decision_agreement(b_first, synth, kpi="purchase_share_focal")

    assert from_a["winner_real"] == "var_A"
    assert from_b["winner_real"] == "var_A"
    assert from_a == from_b
    assert from_a == decision_agreement(a_first, synth, kpi="purchase_share_focal")


def test_decision_agreement_tie_on_the_synthetic_side_too():
    """The same deterministic rule applies to the synthetic panel: tied
    variants resolve to the lowest sorted id, here var_B out of {var_B,
    var_C} (var_A is not tied for the max, it is below them).
    """
    real = {"var_A": 0.9, "var_B": 0.1, "var_C": 0.1}
    synth = {"var_C": 0.5, "var_B": 0.5, "var_A": 0.2}

    result = decision_agreement(real, synth, kpi="purchase_share_focal")

    assert result["winner_real"] == "var_A"
    assert result["winner_synth"] == "var_B"
    assert result["agree"] is False


def test_decision_agreement_ignores_variants_only_one_panel_measured():
    """Each panel's winner comes from its own mapping. Real ran A and B;
    synth also scored an unmeasured C highest. The real winner stays B (its
    own max) and the synth winner is C -- no cross-panel key intersection is
    imposed, because silently dropping a variant would hide the disagreement.
    """
    real = {"var_A": 0.2, "var_B": 0.8}
    synth = {"var_A": 0.2, "var_B": 0.3, "var_C": 0.5}

    result = decision_agreement(real, synth, kpi="purchase_share_focal")

    assert result["winner_real"] == "var_B"
    assert result["winner_synth"] == "var_C"
    assert result["agree"] is False


@pytest.mark.parametrize(
    ("real", "synth"),
    [({}, {"var_A": 1.0}), ({"var_A": 1.0}, {}), ({}, {})],
)
def test_decision_agreement_with_an_empty_panel_raises_value_error(real, synth):
    """There is no argmax over nothing. The schema requires a string winner
    on both sides, so an empty panel is a caller bug and must fail loudly
    rather than invent a winner or emit None into the metrics file.
    """
    with pytest.raises(ValueError):
        decision_agreement(real, synth, kpi="purchase_share_focal")
