"""S25 -- ad slot value: put a price on the shelf before it is sold.

PLAN section 6 writes the formula as `slot value/week = predicted incremental
units x margin x store-weeks`. Two things about that sentence drive most of
this file:

  * **It is dimensionally loose.** A per-week value multiplied by store-weeks
    is not a per-week value. `test_dimensions_*` pin the reading
    `analytics/slot_value.py` actually implements -- incremental units per
    store-week x margin per unit x store-weeks = money over the period -- so
    an implementation that drops or double-counts the store-weeks factor
    cannot pass.
  * **Two of its three inputs are not in this repo.**
    `test_the_repo_carries_no_margin_so_the_caller_must_supply_one` and
    `test_the_simulator_reports_shares_not_units` assert that premise against
    the committed data rather than trusting a comment, and the rest of the
    "assumed, not measured" block asserts that the module makes forgetting it
    structurally impossible: no defaults, no invented industry rate, no
    quietly-derived margin, and every assumed number echoed in the printed
    output next to the word "assumed".

The third theme is the honesty S25 inherits from S24. The underlying lift is
not resolved at 10,000 shoppers (docs/METHODOLOGY.md section 12.13: the top
pick spans +6.2%..+14.2% over seeds 42-46 and the current placement
+1.3%..+8.9%, and those overlap). A rand figure built on the seed-42 lift
carries exactly that uncertainty, so `test_summary_*` require the point
estimate to say so and forbid it from ever printing a confidence interval --
the same line S18 drew and section 12.7 records.
"""

from __future__ import annotations

import dataclasses
import inspect
import json
import re
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from analytics import slot_value as sv
from analytics.lift import creative_brand, sku_brands, synth_lift
from analytics.optimizer import (
    DEFAULT_N_SYNTH,
    DEFAULT_SEED,
    SeedSpread,
    ad_placement_candidates,
    ad_purchase_lift_objective,
    rank_candidates,
    sku_purchase_share_objective,
)

ROOT = Path(__file__).resolve().parents[2]

CREATIVE = "AD_1"
CREATIVE_BRAND = "Crunch"
BASELINE_AD_SLOT = "B3_ENDCAP"
TOP_PICK_AD_SLOT = "B1_TALKER"
FOCAL_SKU = "SKU_008"

# The hand-computed commercial case every dimensional test is built on. Chosen
# so every product below is exact in binary floating point:
#
#   120 units/store-week x 0.10 lift  = 12 units/store-week incremental
#   12 x 7.50 currency/unit           = 90 currency per store-week
#   90 x (4 stores x 13 weeks = 52)   = 4680 currency over the period
HAND_BASELINE_UNITS = 120.0
HAND_MARGIN = 7.50
HAND_STORES = 4
HAND_WEEKS = 13
HAND_STORE_WEEKS = 52.0
HAND_LIFT = 0.10
HAND_INCREMENTAL_UNITS = 12.0
HAND_PER_STORE_WEEK = 90.0
HAND_VALUE = 4680.0

# The `basis` string is required and printed, so the one used here says what it
# actually is. There is no client, no category report and no sell-through data
# behind this demo aisle; these four numbers are round illustrative figures
# chosen to make the arithmetic checkable by hand, and the worked example must
# not read as though they came from somewhere.
HAND_BASIS = ("ILLUSTRATIVE ONLY -- round figures chosen for a hand-checkable example; "
              "no client volume or margin data exists for this demo aisle")
HAND_CURRENCY = "INR"


def base_planogram() -> dict:
    return json.loads((ROOT / "data" / "planograms" / "demo_aisle.json").read_text(encoding="utf-8"))


def hand_assumptions(**overrides) -> "sv.Assumptions":
    kwargs = dict(
        baseline_brand_units_per_store_week=HAND_BASELINE_UNITS,
        margin_per_unit=HAND_MARGIN,
        n_stores=HAND_STORES,
        n_weeks=HAND_WEEKS,
        currency=HAND_CURRENCY,
        basis=HAND_BASIS,
    )
    kwargs.update(overrides)
    return sv.Assumptions(**kwargs)


def _fake_bundle(exposed: dict, unexposed: dict, purchase_share: dict | None = None,
                 sim_run_id: str = "fake") -> SimpleNamespace:
    """The only fields the pricing path reads off a SimBundle."""
    return SimpleNamespace(
        per_persona={},
        population={
            "sim_run_id": sim_run_id,
            "purchase_share": purchase_share or {},
            "ad_exposed_purchase_share": exposed,
            "ad_unexposed_purchase_share": unexposed,
        },
    )


def _arms_giving_lift(target: float) -> tuple[dict, dict]:
    """Two arm vectors whose Crunch share ratio is exactly `1 + target`.

    SKU_001 is Crunch and SKU_003 is Nimbus, so an unexposed share of 0.20 and
    an exposed share of 0.20 * (1 + target) put `synth_lift` on `target`.
    """
    unexposed_share = 0.20
    exposed_share = unexposed_share * (1.0 + target)
    return (
        {"SKU_001": exposed_share, "SKU_003": 1.0 - exposed_share},
        {"SKU_001": unexposed_share, "SKU_003": 1.0 - unexposed_share},
    )


def _simulate_giving_lift(target: float):
    exposed, unexposed = _arms_giving_lift(target)

    def simulate(resolved, variant_id, *, n_synth, seed):
        return _fake_bundle(exposed, unexposed, sim_run_id=f"{variant_id}:{seed}")

    return simulate


# ---------------------------------------------------------------------------
# The premise: neither margin nor units exist in this repo
# ---------------------------------------------------------------------------


def test_the_repo_carries_no_margin_so_the_caller_must_supply_one():
    """SKUs carry `price`. Nothing anywhere carries margin, cost or a rate.

    This is the fact the whole module is shaped around, so it is asserted
    against the committed planogram rather than left as a comment. If someone
    later adds a real margin field, this test fails and S25 should be revisited
    -- deliberately, not by accident.
    """
    planogram = base_planogram()
    forbidden = re.compile(r"margin|cost|cogs", re.IGNORECASE)

    for sku in planogram["skus"]:
        assert "price" in sku, sku["sku_id"]
        assert not [key for key in sku if forbidden.search(key)], sku

    schema = (ROOT / "schemas" / "planogram.schema.json").read_text(encoding="utf-8")
    assert not forbidden.search(schema)


def test_the_simulator_reports_shares_not_units():
    """The two arms `synth_lift` reads are normalised shares, and the schema
    caps them at 1. There is no unit count in a SimResult to price directly."""
    schema = json.loads((ROOT / "schemas" / "simresult.schema.json").read_text(encoding="utf-8"))
    properties = schema["properties"]

    assert properties["purchase_share"]["additionalProperties"]["maximum"] == 1
    assert "ad_exposed_purchase_share" in properties
    assert "ad_unexposed_purchase_share" in properties
    # n_runs counts shoppers, not purchases, so it is not a unit volume either.
    assert properties["n_runs"]["type"] == "integer"
    assert not [key for key in properties if re.search(r"units|traffic|volume", key)]


# ---------------------------------------------------------------------------
# Assumed, not measured: the module must make it impossible to forget
# ---------------------------------------------------------------------------


def test_no_commercial_input_has_a_default():
    """Every field of `Assumptions` is required. A default here would be an
    invented margin rate or an invented store count wearing a number's clothes.
    """
    for field in dataclasses.fields(sv.Assumptions):
        assert field.default is dataclasses.MISSING, field.name
        assert field.default_factory is dataclasses.MISSING, field.name

    with pytest.raises(TypeError):
        sv.Assumptions()


def test_the_module_invents_no_default_margin_rate_or_traffic_figure():
    """No module-level constant supplies a commercial number.

    `DEFAULT_N_SYNTH` and `DEFAULT_SEED` are imported from the optimizer and
    are simulation conventions, not commercial assumptions; nothing named for
    margin, units, stores or traffic may carry a default value here.
    """
    banned = re.compile(r"margin|traffic|units|store|week|price", re.IGNORECASE)
    offenders = [
        name for name, value in vars(sv).items()
        if name.isupper() and banned.search(name) and isinstance(value, (int, float))
    ]
    assert offenders == []


def test_pricing_entry_points_require_the_assumptions_explicitly():
    for function in (sv.value_of_lift, sv.incremental_units_per_store_week,
                     sv.price_placement, sv.price_ranking):
        parameter = inspect.signature(function).parameters["assumptions"]
        assert parameter.default is inspect.Parameter.empty, function.__name__


def test_margin_from_price_needs_the_rate_spelled_out():
    """Price is data; the rate that turns it into margin is not. The helper
    exists so a caller CAN go from price to margin -- but only by naming the
    rate, and the arithmetic is exactly price x rate with nothing added."""
    parameter = inspect.signature(sv.margin_per_unit_from_price).parameters["margin_rate"]
    assert parameter.default is inspect.Parameter.empty
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY

    planogram = base_planogram()
    price = next(s["price"] for s in planogram["skus"] if s["sku_id"] == "SKU_001")
    assert price == 25.0
    assert sv.margin_per_unit_from_price(planogram, "SKU_001", margin_rate=0.30) == 7.5


def test_margin_from_price_rejects_an_unknown_sku_and_an_impossible_rate():
    planogram = base_planogram()
    with pytest.raises(ValueError, match="SKU_999"):
        sv.margin_per_unit_from_price(planogram, "SKU_999", margin_rate=0.3)
    for rate in (0.0, -0.1, 1.5):
        with pytest.raises(ValueError, match="margin_rate"):
            sv.margin_per_unit_from_price(planogram, "SKU_001", margin_rate=rate)


def test_assumptions_reject_numbers_that_cannot_mean_anything():
    for bad in ({"baseline_brand_units_per_store_week": 0.0},
                {"baseline_brand_units_per_store_week": -5.0},
                {"margin_per_unit": 0.0},
                {"margin_per_unit": -1.0},
                {"n_stores": 0},
                {"n_weeks": 0}):
        with pytest.raises(ValueError):
            hand_assumptions(**bad)


def test_assumptions_demand_a_currency_and_a_stated_basis():
    """A bare number has no unit and no provenance. Both are required, because
    the planogram's own `price` field carries no currency either."""
    with pytest.raises(ValueError, match="currency"):
        hand_assumptions(currency="  ")
    with pytest.raises(ValueError, match="basis"):
        hand_assumptions(basis="")


# ---------------------------------------------------------------------------
# Dimensions: PLAN's formula, resolved
# ---------------------------------------------------------------------------


def test_store_weeks_is_stores_times_weeks():
    assert hand_assumptions().store_weeks == HAND_STORE_WEEKS
    assert hand_assumptions(n_stores=10, n_weeks=1).store_weeks == 10.0


def test_dimensions_value_is_the_hand_computed_product():
    """120 units/store-week x 0.10 x 7.50 currency/unit x 52 store-weeks."""
    assert sv.incremental_units_per_store_week(HAND_LIFT, hand_assumptions()) == \
        HAND_INCREMENTAL_UNITS
    assert sv.value_of_lift(HAND_LIFT, hand_assumptions()) == HAND_VALUE


def test_dimensions_doubling_store_weeks_doubles_the_value():
    once = sv.value_of_lift(HAND_LIFT, hand_assumptions())
    more_weeks = sv.value_of_lift(HAND_LIFT, hand_assumptions(n_weeks=HAND_WEEKS * 2))
    more_stores = sv.value_of_lift(HAND_LIFT, hand_assumptions(n_stores=HAND_STORES * 2))

    assert more_weeks == pytest.approx(2 * once)
    assert more_stores == pytest.approx(2 * once)
    # The two routes to the same store-week count are the same money.
    assert more_weeks == pytest.approx(more_stores)


def test_dimensions_doubling_margin_doubles_the_value():
    once = sv.value_of_lift(HAND_LIFT, hand_assumptions())
    twice = sv.value_of_lift(HAND_LIFT, hand_assumptions(margin_per_unit=HAND_MARGIN * 2))
    assert twice == pytest.approx(2 * once)


def test_dimensions_doubling_the_baseline_volume_doubles_the_value():
    once = sv.value_of_lift(HAND_LIFT, hand_assumptions())
    twice = sv.value_of_lift(
        HAND_LIFT, hand_assumptions(baseline_brand_units_per_store_week=HAND_BASELINE_UNITS * 2))
    assert twice == pytest.approx(2 * once)


def test_dimensions_a_per_week_figure_is_recoverable_by_dividing_by_store_weeks():
    """PLAN calls the output "slot value/week". It is a value over a period, and
    the per-store-week rate is that value divided by the store-weeks -- which is
    invariant to how many store-weeks were asked for."""
    priced = sv.price_from_lift(HAND_LIFT, hand_assumptions(), label="hand")

    assert priced.value == HAND_VALUE
    assert priced.value_per_store_week == HAND_PER_STORE_WEEK
    assert priced.value / priced.assumptions.store_weeks == priced.value_per_store_week

    longer = sv.price_from_lift(HAND_LIFT, hand_assumptions(n_weeks=HAND_WEEKS * 3), label="hand")
    assert longer.value == pytest.approx(3 * HAND_VALUE)
    assert longer.value_per_store_week == pytest.approx(HAND_PER_STORE_WEEK)


def test_the_incremental_units_are_the_baseline_scaled_by_the_lift():
    for lift in (0.0, 0.05, 0.5, -0.25):
        assert sv.incremental_units_per_store_week(lift, hand_assumptions()) == \
            pytest.approx(HAND_BASELINE_UNITS * lift)


def test_a_placement_that_hurts_the_brand_prices_negative_not_zero():
    """A negative lift is a real measurement -- the slot costs money."""
    priced = sv.price_from_lift(-0.20, hand_assumptions(), label="worse")
    assert priced.incremental_units_per_store_week == pytest.approx(-24.0)
    assert priced.value == pytest.approx(-9360.0)


# ---------------------------------------------------------------------------
# The chain from the simulator: lift -> incremental units -> money
# ---------------------------------------------------------------------------


def test_the_lift_is_the_projects_one_lift_formula_not_a_second_copy():
    """Price a placement through a scripted simulator, then compute the lift
    independently with `analytics.lift.synth_lift` off the same arms. If
    slot_value.py had its own lift arithmetic the two would drift."""
    base = base_planogram()
    exposed, unexposed = _arms_giving_lift(0.35)

    priced = sv.price_placement(
        base, patches=(), creative_id=CREATIVE, assumptions=hand_assumptions(),
        simulate=lambda resolved, variant_id, *, n_synth, seed: _fake_bundle(exposed, unexposed),
    )

    expected_lift = synth_lift(
        {"ad_exposed_purchase_share": exposed, "ad_unexposed_purchase_share": unexposed},
        brand_of_sku=sku_brands(base), brand=creative_brand(base, CREATIVE),
    )
    assert priced.lift == pytest.approx(expected_lift)
    assert priced.lift == pytest.approx(0.35)
    assert priced.brand == CREATIVE_BRAND
    assert priced.value == pytest.approx(sv.value_of_lift(expected_lift, hand_assumptions()))


def test_pricing_a_placement_runs_at_the_optimizers_simulation_conventions():
    seen = {}

    def simulate(resolved, variant_id, *, n_synth, seed):
        seen["n_synth"], seen["seed"], seen["variant_id"] = n_synth, seed, variant_id
        exposed, unexposed = _arms_giving_lift(0.1)
        return _fake_bundle(exposed, unexposed, sim_run_id="run_xyz")

    priced = sv.price_placement(base_planogram(), patches=(), creative_id=CREATIVE,
                                assumptions=hand_assumptions(), simulate=simulate)

    assert seen["n_synth"] == DEFAULT_N_SYNTH
    assert seen["seed"] == DEFAULT_SEED
    assert priced.n_synth == DEFAULT_N_SYNTH
    assert priced.seed == DEFAULT_SEED
    assert priced.variant_id == seen["variant_id"]
    assert priced.sim_run_id == "run_xyz"


def test_pricing_reads_the_slots_the_creative_actually_hangs_on_after_patching():
    base = base_planogram()
    move = (
        {"op": "set_ad_creative", "ad_slot_id": BASELINE_AD_SLOT, "creative_id": None},
        {"op": "set_ad_creative", "ad_slot_id": TOP_PICK_AD_SLOT, "creative_id": CREATIVE},
    )

    stayed = sv.price_placement(base, patches=(), creative_id=CREATIVE,
                                assumptions=hand_assumptions(),
                                simulate=_simulate_giving_lift(0.1))
    moved = sv.price_placement(base, patches=move, creative_id=CREATIVE,
                               assumptions=hand_assumptions(),
                               simulate=_simulate_giving_lift(0.1))

    assert stayed.ad_slot_ids == (BASELINE_AD_SLOT,)
    assert moved.ad_slot_ids == (TOP_PICK_AD_SLOT,)


# ---------------------------------------------------------------------------
# Undefined lift: None all the way down, never 0.0
# ---------------------------------------------------------------------------


def test_an_undefined_lift_gives_an_undefined_value_never_zero():
    """S24 hits this on the "creative unplaced" candidate. A value of 0.0 would
    read as "this slot is worth nothing"; the truth is that the question has no
    answer for this configuration."""
    assert sv.value_of_lift(None, hand_assumptions()) is None
    assert sv.incremental_units_per_store_week(None, hand_assumptions()) is None

    priced = sv.price_from_lift(None, hand_assumptions(), label="no answer")
    assert priced.lift is None
    assert priced.incremental_units_per_store_week is None
    assert priced.value is None
    assert priced.value_per_store_week is None

    text = sv.summary(priced)
    assert "undefined" in text.lower()
    assert "0.00" not in text


def test_the_unplaced_creative_is_undefined_rather_than_worthless():
    """Take AD_1 down and nobody is ad-exposed, so there is no exposed arm and
    `synth_lift` is None -- exercised through the real arm vectors, not a stub
    return value."""
    base = base_planogram()
    empty_arm = {sku["sku_id"]: 0.0 for sku in base["skus"]}

    def simulate(resolved, variant_id, *, n_synth, seed):
        return _fake_bundle(exposed=dict(empty_arm),
                            unexposed={**empty_arm, "SKU_001": 0.5, "SKU_003": 0.5})

    priced = sv.price_placement(
        base,
        patches=({"op": "set_ad_creative", "ad_slot_id": BASELINE_AD_SLOT, "creative_id": None},),
        creative_id=CREATIVE, assumptions=hand_assumptions(), simulate=simulate,
    )

    assert priced.ad_slot_ids == ()
    assert priced.lift is None
    assert priced.value is None


# ---------------------------------------------------------------------------
# Traceability: the printed sentence names what was assumed
# ---------------------------------------------------------------------------


def test_every_assumed_number_appears_in_the_summary_under_the_word_assumed():
    text = sv.summary(sv.price_from_lift(HAND_LIFT, hand_assumptions(), label="AD_1 somewhere"))
    lower = text.lower()

    assert "assumed" in lower
    assumed_line = next(line for line in text.splitlines() if "Assumed" in line)
    for token in ("120", "7.50", "4", "13", HAND_CURRENCY, "store-week"):
        assert token in assumed_line, (token, assumed_line)
    assert HAND_BASIS in text


def test_the_summary_separates_the_measured_lift_from_the_assumed_money():
    text = sv.summary(sv.price_from_lift(HAND_LIFT, hand_assumptions(), label="AD_1 somewhere"))

    measured_line = next(line for line in text.splitlines() if "Measured" in line)
    assumed_line = next(line for line in text.splitlines() if "Assumed" in line)

    assert "lift" in measured_line.lower()
    assert "+10.0%" in measured_line
    # The money is not on the measured line, and the lift is not on the
    # assumed line. Mixing them is exactly the confusion this module exists to
    # prevent.
    assert HAND_CURRENCY not in measured_line
    assert "lift" not in assumed_line.lower()


def test_the_summary_prints_the_value_the_currency_and_the_period():
    text = sv.summary(sv.price_from_lift(HAND_LIFT, hand_assumptions(), label="AD_1 somewhere"))

    assert "4,680.00" in text
    assert HAND_CURRENCY in text
    assert "52" in text and "store-week" in text
    assert "90.00" in text  # the per-store-week rate
    assert "AD_1 somewhere" in text


def test_the_summary_never_prints_a_confidence_interval():
    """The same line S24 draws, for the same reason (METHODOLOGY 12.7): a
    committed SimResult carries normalised shares, so there is nothing to
    resample and any interval printed here would be fabricated."""
    spread = SeedSpread(seeds=(42, 43, 44), values=(0.10, 0.06, 0.14), low=0.06, high=0.14)
    priced = sv.price_from_lift(HAND_LIFT, hand_assumptions(), label="AD_1", lift_spread=spread)

    text = sv.summary(priced)
    lower = text.lower()
    assert lower.count("confidence interval") == lower.count("not a confidence interval")
    assert "not a confidence interval" in lower
    assert "ci95" not in lower
    assert re.search(r"\bCI\b", text) is None


def test_the_summary_says_the_value_is_a_point_estimate_at_one_seed():
    """S24's ranking is not resolved at 10,000 shoppers, and a rand figure built
    on the seed-42 lift inherits that. The sentence has to carry it."""
    text = sv.summary(sv.price_from_lift(HAND_LIFT, hand_assumptions(), label="AD_1")).lower()

    assert "point estimate" in text
    assert "seed" in text
    assert "not resolved" in text


def test_the_module_and_its_assumptions_document_the_resolved_formula():
    doc = sv.__doc__.lower()
    assert "store-weeks" in doc
    assert "dimension" in doc

    assumptions_doc = sv.Assumptions.__doc__.lower()
    assert "assum" in assumptions_doc
    assert "not measured" in assumptions_doc or "never measured" in assumptions_doc


# ---------------------------------------------------------------------------
# Carrying S24's seed spread through to money
# ---------------------------------------------------------------------------


def test_the_value_spread_is_the_lift_spread_priced_with_the_same_assumptions():
    spread = SeedSpread(seeds=(42, 43, 44), values=(0.10, 0.06, 0.14), low=0.06, high=0.14)
    priced = sv.price_from_lift(HAND_LIFT, hand_assumptions(), label="AD_1", lift_spread=spread)

    assert priced.lift_spread is spread
    values = priced.value_spread
    assert values.seeds == (42, 43, 44)
    assert values.values == pytest.approx(
        tuple(sv.value_of_lift(v, hand_assumptions()) for v in spread.values))
    assert values.low == pytest.approx(sv.value_of_lift(0.06, hand_assumptions()))
    assert values.high == pytest.approx(sv.value_of_lift(0.14, hand_assumptions()))


def test_a_value_spread_is_printed_as_a_seed_spread_and_nothing_else():
    spread = SeedSpread(seeds=(42, 43), values=(0.10, 0.14), low=0.10, high=0.14)
    text = sv.summary(sv.price_from_lift(HAND_LIFT, hand_assumptions(), label="AD_1",
                                         lift_spread=spread)).lower()

    assert "seed spread" in text
    assert "monte carlo" in text


def test_no_lift_spread_means_no_value_spread():
    priced = sv.price_from_lift(HAND_LIFT, hand_assumptions(), label="AD_1")
    assert priced.lift_spread is None
    assert priced.value_spread is None


# ---------------------------------------------------------------------------
# Pricing a whole S24 ranking
# ---------------------------------------------------------------------------


def _ranking_on_lift(simulate, **kwargs):
    base = base_planogram()
    return base, rank_candidates(
        base, ad_placement_candidates(base, creative_ids=(CREATIVE,)),
        ad_purchase_lift_objective(CREATIVE), simulate=simulate, **kwargs)


def test_pricing_a_ranking_carries_the_rank_and_the_current_flag_through():
    base, ranking = _ranking_on_lift(_simulate_giving_lift(0.1), spread_seeds=())
    priced = sv.price_ranking(ranking, creative_id=CREATIVE, assumptions=hand_assumptions())

    assert len(priced) == ranking.n_candidates
    assert [row.rank for row in priced] == [entry.rank for entry in ranking.entries]
    assert [row.is_current for row in priced] == [entry.is_current for entry in ranking.entries]
    assert [row.label for row in priced] == [entry.candidate.label for entry in ranking.entries]
    for row, entry in zip(priced, ranking.entries):
        assert row.lift == entry.objective
        assert row.sim_run_id == entry.sim_run_id
        assert row.value == pytest.approx(sv.value_of_lift(entry.objective, hand_assumptions()))


def test_pricing_a_ranking_can_name_the_brand_the_units_belong_to():
    """The value is incremental units OF A BRAND, and a `Ranking` does not
    carry the planogram to look that up in. Hand one over and every priced row
    names the brand, so the baseline volume and the margin cannot be quietly
    applied to the wrong product."""
    base, ranking = _ranking_on_lift(_simulate_giving_lift(0.1), spread_seeds=())

    without = sv.price_ranking(ranking, creative_id=CREATIVE, assumptions=hand_assumptions())
    assert {row.brand for row in without} == {None}
    assert CREATIVE_BRAND not in sv.summary(without[0])

    with_brand = sv.price_ranking(ranking, creative_id=CREATIVE,
                                  assumptions=hand_assumptions(), planogram=base)
    assert {row.brand for row in with_brand} == {CREATIVE_BRAND}
    assert CREATIVE_BRAND in sv.summary(with_brand[0])

    # An unknown creative is the planogram's own error, not a silent None.
    with pytest.raises(ValueError, match="AD_9"):
        sv.price_ranking(ranking, creative_id="AD_9", assumptions=hand_assumptions(),
                         planogram=base)


def test_pricing_a_ranking_refuses_an_objective_that_is_not_that_creatives_lift():
    """A purchase-share ranking's numbers are shares, not lifts. Multiplying one
    by a baseline unit volume would produce a confident, meaningless rand
    figure, so the objective name is checked against the one the lift objective
    itself would have produced."""
    base = base_planogram()

    def simulate(resolved, variant_id, *, n_synth, seed):
        return _fake_bundle({}, {}, purchase_share={FOCAL_SKU: 0.05})

    shares = rank_candidates(base, ad_placement_candidates(base, creative_ids=(CREATIVE,)),
                             sku_purchase_share_objective(FOCAL_SKU), simulate=simulate,
                             spread_seeds=())
    with pytest.raises(ValueError, match="objective"):
        sv.price_ranking(shares, creative_id=CREATIVE, assumptions=hand_assumptions())

    # Right kind of objective, wrong creative: also refused.
    _, lift_ranking = _ranking_on_lift(_simulate_giving_lift(0.1), spread_seeds=())
    with pytest.raises(ValueError, match="objective"):
        sv.price_ranking(lift_ranking, creative_id="AD_2", assumptions=hand_assumptions())


def test_pricing_a_ranking_carries_each_rows_seed_spread_into_money():
    table = {42: 0.10, 43: 0.06, 44: 0.14}

    def simulate(resolved, variant_id, *, n_synth, seed):
        exposed, unexposed = _arms_giving_lift(table[seed])
        return _fake_bundle(exposed, unexposed, sim_run_id=f"{variant_id}:{seed}")

    _, ranking = _ranking_on_lift(simulate, seed=42, spread_seeds=(43, 44), spread_top_n=2)
    priced = sv.price_ranking(ranking, creative_id=CREATIVE, assumptions=hand_assumptions())

    top = priced[0]
    assert top.lift_spread is not None
    assert top.value_spread.low == pytest.approx(sv.value_of_lift(0.06, hand_assumptions()))
    assert top.value_spread.high == pytest.approx(sv.value_of_lift(0.14, hand_assumptions()))


def test_an_undefined_row_of_a_ranking_stays_undefined_when_priced():
    base = base_planogram()
    empty_arm = {sku["sku_id"]: 0.0 for sku in base["skus"]}

    def simulate(resolved, variant_id, *, n_synth, seed):
        showing = [ad["ad_slot_id"] for bay in resolved["bays"] for ad in bay["ad_slots"]
                   if ad["creative_id"] == CREATIVE]
        exposed = dict(empty_arm)
        if showing:
            exposed["SKU_001"] = 0.3
            exposed["SKU_003"] = 0.7
        return _fake_bundle(exposed, {**empty_arm, "SKU_001": 0.2, "SKU_003": 0.8})

    _, ranking = _ranking_on_lift(simulate, spread_seeds=())
    priced = sv.price_ranking(ranking, creative_id=CREATIVE, assumptions=hand_assumptions())

    undefined = [row for row in priced if row.lift is None]
    assert len(undefined) == 1
    assert undefined[0].value is None
    assert undefined[0].rank == len(priced)


# ---------------------------------------------------------------------------
# THE WORKED EXAMPLE (PLAN section 6, S25) -- the real seed planogram
# ---------------------------------------------------------------------------


def test_worked_example_prices_ad_1_at_its_current_placement_and_at_the_top_pick():
    """The deliverable sentence: what one ad slot is worth over a period, with
    every assumed number printed beside it.

    The lift is measured (10,000 synthetic shoppers through
    `api.app.simcache.population` and `analytics.lift.synth_lift`). The money is
    not: baseline volume, margin, store count and week count are all supplied
    here and would be supplied by a client in a real engagement.
    """
    base = base_planogram()
    assumptions = hand_assumptions()

    started = time.perf_counter()
    ranking = rank_candidates(base, ad_placement_candidates(base, creative_ids=(CREATIVE,)),
                              ad_purchase_lift_objective(CREATIVE), seed=DEFAULT_SEED)
    priced = sv.price_ranking(ranking, creative_id=CREATIVE, assumptions=assumptions,
                              planogram=base)
    elapsed = time.perf_counter() - started

    top = priced[0]
    current = next(row for row in priced if row.is_current)
    assert top.brand == current.brand == CREATIVE_BRAND

    assert top.lift is not None and current.lift is not None
    assert top.value is not None and current.value is not None
    assert top.value > current.value
    # The money moves with the lift and nothing else, because the assumptions
    # are identical on both rows.
    assert top.value / current.value == pytest.approx(top.lift / current.lift)

    print(f"\n{sv.assumptions_block(assumptions)}")
    print(f"\n{sv.summary(top)}")
    print(f"\n{sv.summary(current)}")
    print(f"\n{sv.table(priced)}")
    print(f"\n({elapsed:.1f}s for {ranking.n_candidates} placements at "
          f"{ranking.n_synth} shoppers)")
