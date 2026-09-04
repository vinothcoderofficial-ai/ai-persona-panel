"""Tests for the across-session half of analytics/fusion.py -- `trimmed_mean`
and `bootstrap_ci` (SPEC M5: "Across sessions: 10 % trimmed mean; bootstrap
1,000 -> 95 % CI").

Every expected number below is hand-computed in the comment above its
assertion, worked out independently of fusion.py's implementation.

The trim count is rounded PER TAIL with int(n * trim), so a panel of fewer
than 10 sessions trims nothing at all and the result is a plain mean. That
rounding rule is asserted directly below rather than assumed.
"""

import math

import pytest

from analytics.fusion import bootstrap_ci, trimmed_mean

SLOT_IDS = ["S1", "S2"]


def _sessions(s1_values, s2_value=0.0):
    """One fused-attention mapping per session, S1 varying, S2 held constant."""
    return [{"S1": v, "S2": s2_value} for v in s1_values]


# ---------------------------------------------------------------------------
# trimmed_mean
# ---------------------------------------------------------------------------


def test_five_sessions_trim_nothing_and_equal_the_plain_mean():
    """n = 5, trim = 0.10 -> int(5 * 0.10) = int(0.5) = 0 per tail, so nothing
    is dropped and the answer is the plain arithmetic mean.

    S1 = [0.1, 0.2, 0.3, 0.4, 0.5]; sum = 1.5; mean = 1.5 / 5 = 0.3
    S2 = 0.0 in every session -> mean 0.0
    """
    per_session = _sessions([0.1, 0.2, 0.3, 0.4, 0.5])

    result = trimmed_mean(per_session, SLOT_IDS)

    assert result == pytest.approx({"S1": 0.3, "S2": 0.0})


def test_nine_sessions_keep_their_outlier():
    """n = 9 -> int(9 * 0.10) = int(0.9) = 0 per tail. Below ten sessions there
    is no trimming at all, so a wild outlier still drags the mean.

    S1 = eight zeros and one 90.0; sum = 90.0; mean = 90.0 / 9 = 10.0
    (If one value per tail were trimmed, the 90.0 would vanish and S1 would
    come out at 0.0 instead.)
    """
    per_session = _sessions([0.0] * 8 + [90.0])

    result = trimmed_mean(per_session, SLOT_IDS)

    assert result["S1"] == pytest.approx(10.0)


def test_ten_sessions_trim_one_per_tail():
    """n = 10 -> int(10 * 0.10) = 1 per tail: the single lowest and single
    highest session values are dropped.

    S1 sorted = [0.0, 1, 2, 3, 4, 5, 6, 7, 8, 1000.0]
    drop 0.0 and 1000.0 -> middle 8 = [1..8]
    sum(1..8) = 36; mean = 36 / 8 = 4.5
    (The untrimmed mean would be (0 + 36 + 1000) / 10 = 103.6.)
    """
    per_session = _sessions([0.0] + [float(i) for i in range(1, 9)] + [1000.0])

    result = trimmed_mean(per_session, SLOT_IDS)

    assert result["S1"] == pytest.approx(4.5)
    assert result["S1"] != pytest.approx(103.6)


def test_twenty_sessions_trim_two_per_tail_leaving_the_middle_sixteen():
    """n = 20 -> int(20 * 0.10) = 2 per tail: the two lowest and two highest
    session values are dropped, leaving the middle 16.

    S1 = [-50, -50] + [1, 2, ..., 16] + [999, 999]   (20 values)
    sorted: the two -50s are the low tail, the two 999s the high tail.
    kept = [1..16]; sum(1..16) = 16 * 17 / 2 = 136; mean = 136 / 16 = 8.5

    The untrimmed mean would be (-100 + 136 + 1998) / 20 = 2034 / 20 = 101.7,
    so this also proves the trimming actually happened.

    S2 is 2.0 in all 20 sessions; trimming a constant changes nothing, and it
    checks that each slot is trimmed on its own values rather than on some
    session-level ordering.
    """
    s1_values = [-50.0, -50.0] + [float(i) for i in range(1, 17)] + [999.0, 999.0]
    assert len(s1_values) == 20
    per_session = _sessions(s1_values, s2_value=2.0)

    result = trimmed_mean(per_session, SLOT_IDS)

    assert result["S1"] == pytest.approx(8.5)
    assert result["S2"] == pytest.approx(2.0)
    assert result["S1"] != pytest.approx(101.7)


def test_trimming_is_per_slot_not_per_session():
    """S1's outlier session and S2's outlier session are different sessions.
    Trimming per slot removes each slot's own extremes; trimming whole
    sessions (by, say, S1's ordering) would leave S2's outlier in place.

    20 sessions. S1 = 1.0 everywhere except sessions 0 and 1 (= -99.0) and
    sessions 18 and 19 (= 99.0)  -> trimmed to sixteen 1.0s -> 1.0
    S2 = 5.0 everywhere except sessions 5 and 6 (= -99.0) and sessions 10 and
    11 (= 99.0)                  -> trimmed to sixteen 5.0s -> 5.0
    """
    per_session = []
    for i in range(20):
        s1 = -99.0 if i in (0, 1) else (99.0 if i in (18, 19) else 1.0)
        s2 = -99.0 if i in (5, 6) else (99.0 if i in (10, 11) else 5.0)
        per_session.append({"S1": s1, "S2": s2})

    result = trimmed_mean(per_session, SLOT_IDS)

    assert result == pytest.approx({"S1": 1.0, "S2": 5.0})


def test_slot_missing_from_a_session_counts_as_zero():
    """A session mapping that omits a slot contributes 0.0 for it, so the slot
    stays comparable across the panel.

    S2 present in one of three sessions at 3.0 -> values [3.0, 0.0, 0.0]
    n = 3 -> no trimming -> mean = 3.0 / 3 = 1.0
    S1 = 1.0 in all three -> mean 1.0
    """
    per_session = [{"S1": 1.0, "S2": 3.0}, {"S1": 1.0}, {"S1": 1.0}]

    result = trimmed_mean(per_session, SLOT_IDS)

    assert result == pytest.approx({"S1": 1.0, "S2": 1.0})


def test_every_slot_id_appears_even_with_no_sessions():
    """An empty panel must not divide by zero: every slot comes back 0.0."""
    result = trimmed_mean([], SLOT_IDS)

    assert result == {"S1": 0.0, "S2": 0.0}
    assert not any(math.isnan(v) for v in result.values())


def test_a_single_session_is_returned_unchanged():
    """n = 1 -> int(0.1) = 0 trimmed; the mean of one value is that value."""
    result = trimmed_mean([{"S1": 0.42, "S2": 0.58}], SLOT_IDS)

    assert result == pytest.approx({"S1": 0.42, "S2": 0.58})


def test_trim_zero_is_the_plain_mean_even_with_many_sessions():
    """trim = 0.0 -> int(20 * 0.0) = 0 per tail, i.e. no trimming at all, so
    the 999s and -50s stay in: (-100 + 136 + 1998) / 20 = 101.7
    """
    s1_values = [-50.0, -50.0] + [float(i) for i in range(1, 17)] + [999.0, 999.0]
    per_session = _sessions(s1_values)

    result = trimmed_mean(per_session, SLOT_IDS, trim=0.0)

    assert result["S1"] == pytest.approx(101.7)


@pytest.mark.parametrize("bad_trim", [-0.01, 0.5, 0.9, 1.0])
def test_trim_outside_zero_to_a_half_raises_value_error(bad_trim):
    """trim >= 0.5 would trim away every value from both tails and leave an
    empty slice to average; a negative trim is meaningless. Both are caller
    bugs and must fail loudly rather than return a silent zero.
    """
    with pytest.raises(ValueError):
        trimmed_mean(_sessions([1.0, 2.0, 3.0]), SLOT_IDS, trim=bad_trim)


# ---------------------------------------------------------------------------
# bootstrap_ci
# ---------------------------------------------------------------------------


def test_same_seed_gives_identical_bounds():
    """scripts/eval.py has to regenerate RESULTS.md byte-identically, so the
    same seed must give exactly the same interval -- not merely a close one.
    """
    per_session = _sessions([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8])

    first = bootstrap_ci(per_session, SLOT_IDS, seed=7)
    second = bootstrap_ci(per_session, SLOT_IDS, seed=7)

    assert first == second


def test_a_different_seed_gives_a_different_interval():
    """Sanity check that the seed is actually being consumed rather than
    ignored: on varied data, two different seeds must not coincide exactly.
    """
    per_session = _sessions([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8])

    assert bootstrap_ci(per_session, SLOT_IDS, seed=7) != bootstrap_ci(
        per_session, SLOT_IDS, seed=8
    )


def test_interval_brackets_the_trimmed_mean():
    """The point estimate the interval describes is the trimmed mean of the
    observed panel, so it must lie inside the 2.5/97.5 percentile bounds, and
    the bounds must be ordered low <= high.
    """
    per_session = _sessions([0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50])
    point = trimmed_mean(per_session, SLOT_IDS)

    ci = bootstrap_ci(per_session, SLOT_IDS, seed=42)

    for slot_id in SLOT_IDS:
        low, high = ci[slot_id]
        assert low <= high
        assert low <= point[slot_id] <= high


def test_identical_sessions_give_a_zero_width_interval():
    """Every resample of a panel whose sessions are all identical is that same
    panel, so the bootstrap distribution is a single point: low == high ==
    the value itself (0.25 for S1, 0.75 for S2), with no spread to report.
    """
    per_session = [{"S1": 0.25, "S2": 0.75} for _ in range(12)]

    ci = bootstrap_ci(per_session, SLOT_IDS, seed=1)

    assert ci["S1"] == (0.25, 0.25)
    assert ci["S2"] == (0.75, 0.75)


def test_varied_sessions_give_a_positive_width_interval():
    """The mirror of the test above: real spread across sessions must produce
    a strictly positive interval width, otherwise the CI is not measuring
    anything.
    """
    per_session = _sessions([0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9])

    low, high = bootstrap_ci(per_session, SLOT_IDS, seed=3)["S1"]

    assert high > low


def test_a_wider_panel_spread_gives_a_wider_interval():
    """Two panels with the same mean but different spread: the noisier panel
    must get the wider interval. Both are centred on 0.5.

    tight  = [0.4, 0.45, 0.5, 0.55, 0.6, 0.4, 0.45, 0.55, 0.6, 0.5]
    spread = [0.0, 0.1,  0.5, 0.9,  1.0, 0.0, 0.1,  0.9,  1.0, 0.5]
    """
    tight = _sessions([0.4, 0.45, 0.5, 0.55, 0.6, 0.4, 0.45, 0.55, 0.6, 0.5])
    spread = _sessions([0.0, 0.1, 0.5, 0.9, 1.0, 0.0, 0.1, 0.9, 1.0, 0.5])

    tight_low, tight_high = bootstrap_ci(tight, SLOT_IDS, seed=11)["S1"]
    spread_low, spread_high = bootstrap_ci(spread, SLOT_IDS, seed=11)["S1"]

    assert (spread_high - spread_low) > (tight_high - tight_low)


def test_empty_panel_gives_a_zero_interval_not_nan():
    """No sessions at all: nothing to resample, so report (0.0, 0.0) rather
    than dividing by zero or emitting NaN into RESULTS.md.
    """
    ci = bootstrap_ci([], SLOT_IDS, seed=0)

    assert ci == {"S1": (0.0, 0.0), "S2": (0.0, 0.0)}
    assert not any(math.isnan(v) for bounds in ci.values() for v in bounds)


def test_n_boot_is_honoured_and_changes_the_estimate_resolution():
    """A smaller n_boot must still run and stay reproducible; it is a real
    parameter, not decoration.
    """
    per_session = _sessions([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8])

    small = bootstrap_ci(per_session, SLOT_IDS, n_boot=50, seed=5)

    assert small == bootstrap_ci(per_session, SLOT_IDS, n_boot=50, seed=5)
    assert small["S1"][0] <= small["S1"][1]


@pytest.mark.parametrize("bad_ci", [0.0, 1.0, -0.5, 1.5])
def test_ci_outside_zero_to_one_raises_value_error(bad_ci):
    with pytest.raises(ValueError):
        bootstrap_ci(_sessions([0.1, 0.2, 0.3]), SLOT_IDS, seed=0, ci=bad_ci)


@pytest.mark.parametrize("bad_n_boot", [0, -1])
def test_non_positive_n_boot_raises_value_error(bad_n_boot):
    with pytest.raises(ValueError):
        bootstrap_ci(_sessions([0.1, 0.2, 0.3]), SLOT_IDS, n_boot=bad_n_boot, seed=0)
