"""Tests for analytics/lift.py -- the Ad-to-Purchase Lift (PLAN S18, the
headline metric, and on PLAN section 9's never-drop list).

    lift = (brand share among ad-exposed - among non-exposed) / non-exposed

Three groups of tests here:

1. **Arithmetic.** Hand-computed lifts, SKU shares aggregating up to brand
   shares, the zero-denominator guard returning None (never inf, never 0.0),
   `real: null` never becoming a fabricated number, and the two panels going
   through demonstrably the same function.

2. **Behaviour against the simulator** (the PLAN S18 acceptance criteria):
   `ad_receptivity = 0` gives lift ~ 0, and raising receptivity raises lift
   monotonically. These run on a purpose-built BRAND-SYMMETRIC single-bay
   store (see `symmetric_planogram` below) so the null really is zero, plus
   one sweep on the committed demo aisle.

3. **Contract.** The emitted block validates against the
   `ad_to_purchase_lift` shape in schemas/metrics.schema.json, and the same
   seed reproduces the CI exactly.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest
from jsonschema import Draft7Validator

from analytics.lift import (
    DEFAULT_N_BOOT,
    EXPOSURE_EVENT_TYPES,
    POPULATION_KEY,
    Shopper,
    ad_slots_showing,
    ad_to_purchase_lift,
    between_variant_lift,
    bootstrap_between_variant_mc95,
    bootstrap_lift_ci,
    bootstrap_synth_lift_ci,
    brand_share,
    creative_brand,
    lift,
    purchase_event_count,
    real_lift,
    sku_brands,
    split_panel,
    synth_lift,
)
from api.app.resolve import resolve
from sim.simulator import build_store, combine, run

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
SCHEMAS = ROOT / "schemas"

# Four SKUs, two brands, used by every hand-computed case below.
BRAND_OF_SKU = {"A1": "Crunch", "A2": "Crunch", "B1": "Zapp", "B2": "Zapp"}


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def metrics_schema() -> dict:
    return load_json(SCHEMAS / "metrics.schema.json")


@pytest.fixture(scope="module")
def demo_planogram() -> dict:
    """The committed demo aisle. Variant A has no patches, so this is A resolved."""
    return load_json(DATA / "planograms" / "demo_aisle.json")


@pytest.fixture(scope="module")
def demo_policies() -> dict:
    return {
        persona: load_json(DATA / "cache" / "policies" / f"{persona}_demo_aisle.json")
        for persona in ("mission", "browser", "loyalist", "switcher")
    }


def sim_result(exposed: dict[str, float], unexposed: dict[str, float], **extra) -> dict:
    """A SimResult carrying just the two fields this metric reads."""
    result = {
        "sim_run_id": "deadbeef0000",
        "variant_id": "A",
        "persona_id": "switcher",
        "n_runs": 10_000,
        "seed": 1,
        "fixation_prob": {},
        "dwell_ms_mean": {},
        "ad_slot_attention": {},
        "purchase_share": {},
        "ad_exposed_purchase_share": exposed,
        "ad_unexposed_purchase_share": unexposed,
        "path": {"stations_mean": 3.0, "duration_s_mean": 60.0},
    }
    result.update(extra)
    return result


# ---------------------------------------------------------------------------
# 1. arithmetic
# ---------------------------------------------------------------------------


def test_hand_computed_lift_from_chosen_shares():
    """Exposed 0.50, unexposed 0.25 -> (0.50 - 0.25) / 0.25 = +1.0 exactly."""
    assert lift(0.50, 0.25) == pytest.approx(1.0)
    # And the other direction: the ad group buys the brand half as often.
    assert lift(0.25, 0.50) == pytest.approx(-0.5)
    # A no-effect case is exactly zero, not "nearly".
    assert lift(0.4, 0.4) == 0.0


def test_hand_computed_lift_end_to_end_through_a_simresult():
    """The same hand-computed number, but reached the way the pipeline does:
    SKU shares -> brand shares -> lift."""
    exposed = {"A1": 0.30, "A2": 0.20, "B1": 0.30, "B2": 0.20}  # Crunch 0.50
    unexposed = {"A1": 0.15, "A2": 0.10, "B1": 0.45, "B2": 0.30}  # Crunch 0.25

    assert synth_lift(
        sim_result(exposed, unexposed), brand_of_sku=BRAND_OF_SKU, brand="Crunch"
    ) == pytest.approx(1.0)


def test_sku_shares_aggregate_to_brand_shares():
    """A brand with several SKUs sums them; the denominator is every purchase."""
    share = {"A1": 0.30, "A2": 0.20, "B1": 0.35, "B2": 0.15}
    assert brand_share(share, BRAND_OF_SKU, "Crunch") == pytest.approx(0.50)
    assert brand_share(share, BRAND_OF_SKU, "Zapp") == pytest.approx(0.50)

    # Three SKUs on one brand, and a share vector that does not already sum to
    # 1 -- the function must normalise by the observed total, not assume it.
    three = {"A1": 2.0, "A2": 3.0, "A3": 1.0, "B1": 4.0}
    brands = {"A1": "Crunch", "A2": "Crunch", "A3": "Crunch", "B1": "Zapp"}
    assert brand_share(three, brands, "Crunch") == pytest.approx(6.0 / 10.0)


def test_brand_shares_over_all_brands_sum_to_one():
    share = {"A1": 0.30, "A2": 0.20, "B1": 0.35, "B2": 0.15}
    total = sum(brand_share(share, BRAND_OF_SKU, b) for b in ("Crunch", "Zapp"))
    assert total == pytest.approx(1.0)


def test_unknown_sku_raises_rather_than_silently_shrinking_the_denominator():
    with pytest.raises(ValueError, match="GHOST"):
        brand_share({"A1": 0.5, "GHOST": 0.5}, BRAND_OF_SKU, "Crunch")


def test_zero_unexposed_share_gives_none_not_inf_and_not_zero():
    """The denominator guard. A persona that never buys the advertised brand
    unexposed is exactly where a naive ratio reports inf."""
    assert lift(0.4, 0.0) is None

    # ... and through the SimResult path: nobody unexposed bought Crunch.
    exposed = {"A1": 0.30, "A2": 0.20, "B1": 0.30, "B2": 0.20}
    unexposed = {"A1": 0.0, "A2": 0.0, "B1": 0.6, "B2": 0.4}
    value = synth_lift(sim_result(exposed, unexposed), brand_of_sku=BRAND_OF_SKU, brand="Crunch")
    assert value is None
    assert value != 0.0


def test_empty_arm_is_undefined_rather_than_a_minus_one_hundred_percent_lift():
    """An arm with NO purchases at all is undefined -- distinct from an arm
    that bought things but none of them the advertised brand."""
    empty = {"A1": 0.0, "A2": 0.0, "B1": 0.0, "B2": 0.0}
    populated = {"A1": 0.30, "A2": 0.20, "B1": 0.30, "B2": 0.20}

    assert brand_share(empty, BRAND_OF_SKU, "Crunch") is None
    assert synth_lift(sim_result(empty, populated), brand_of_sku=BRAND_OF_SKU, brand="Crunch") is None
    assert synth_lift(sim_result(populated, empty), brand_of_sku=BRAND_OF_SKU, brand="Crunch") is None

    # But an arm that bought only the OTHER brand is a real -100% lift.
    none_of_the_brand = {"A1": 0.0, "A2": 0.0, "B1": 0.6, "B2": 0.4}
    assert synth_lift(
        sim_result(none_of_the_brand, populated), brand_of_sku=BRAND_OF_SKU, brand="Crunch"
    ) == pytest.approx(-1.0)


def test_a_simresult_without_the_two_arm_vectors_raises():
    """Both fields are optional in schemas/simresult.schema.json. A SimResult
    from before S16 cannot answer this question, and must say so loudly rather
    than return None as if the arms were empty."""
    stale = sim_result({}, {})
    del stale["ad_exposed_purchase_share"]
    with pytest.raises(ValueError, match="ad_exposed_purchase_share"):
        synth_lift(stale, brand_of_sku=BRAND_OF_SKU, brand="Crunch")


# ---------------------------------------------------------------------------
# 1b. the real panel: same arithmetic, different input
# ---------------------------------------------------------------------------


def event(t_ms: int, type_: str, **payload) -> dict:
    return {"t_ms": t_ms, "type": type_, "station_id": "B3", "payload": payload}


def session(*, saw_ad: str | None, bought: list[str], exposure_type: str = "fixation") -> list[dict]:
    """One accepted session's events: optionally an ad-slot look, then a cart."""
    events: list[dict] = [event(0, "station_enter", station_id="B3")]
    if saw_ad is not None:
        if exposure_type == "fixation":
            events.append(event(10, "fixation", x=0.5, y=0.5, dur_ms=240, slot_id=saw_ad,
                                shelf_id=None))
        else:
            events.append(event(10, exposure_type, sku_id=None, slot_id=saw_ad))
    for i, sku_id in enumerate(bought):
        events.append(event(100 + i, "add_to_cart", sku_id=sku_id, slot_id=f"SLOT_{sku_id}"))
    return events


def test_real_panel_is_split_by_fixation_hover_or_pickup_on_a_creative_ad_slot():
    assert EXPOSURE_EVENT_TYPES == frozenset({"fixation", "hover", "pickup"})

    sessions = [
        session(saw_ad="B3_ENDCAP", bought=["A1"]),
        session(saw_ad="B3_ENDCAP", bought=["B1"], exposure_type="hover"),
        session(saw_ad="B3_ENDCAP", bought=["A2"], exposure_type="pickup"),
        session(saw_ad="B1_TALKER", bought=["B2"]),  # an ad slot NOT showing the creative
        session(saw_ad=None, bought=["B1"]),
    ]
    shoppers = split_panel(sessions, ad_slot_ids=["B3_ENDCAP"])

    assert [s.exposed for s in shoppers] == [True, True, True, False, False]
    assert [s.basket for s in shoppers] == [("A1",), ("B1",), ("A2",), ("B2",), ("B1",)]


def test_real_lift_is_the_same_arithmetic_as_the_synthetic_lift():
    """Feed the two panels the same brand shares by construction and demand
    the identical number -- the point of decision 5."""
    # Exposed: 2 of 4 purchases are Crunch (0.50). Unexposed: 2 of 8 (0.25).
    shoppers = [
        Shopper(exposed=True, basket=("A1", "B1")),
        Shopper(exposed=True, basket=("A2", "B2")),
        Shopper(exposed=False, basket=("A1", "B1", "B2", "B1")),
        Shopper(exposed=False, basket=("A2", "B2", "B1", "B2")),
    ]
    from_real = real_lift(shoppers, brand_of_sku=BRAND_OF_SKU, brand="Crunch")

    from_synth = synth_lift(
        sim_result({"A1": 0.25, "A2": 0.25, "B1": 0.25, "B2": 0.25},
                   {"A1": 0.125, "A2": 0.125, "B1": 0.375, "B2": 0.375}),
        brand_of_sku=BRAND_OF_SKU,
        brand="Crunch",
    )

    assert from_real == pytest.approx(1.0)
    assert from_real == pytest.approx(from_synth)


def test_real_is_none_not_zero_when_the_panel_has_no_exposed_shoppers(metrics_schema):
    only_unexposed = [
        Shopper(exposed=False, basket=("A1",)),
        Shopper(exposed=False, basket=("B1",)),
    ]
    assert real_lift(only_unexposed, brand_of_sku=BRAND_OF_SKU, brand="Crunch") is None

    block = ad_to_purchase_lift(
        {"switcher": sim_result({"A1": 0.5, "B1": 0.5}, {"A1": 0.25, "B1": 0.75})},
        brand_of_sku=BRAND_OF_SKU,
        brand="Crunch",
        real={"switcher": only_unexposed},
        seed=7,
    )
    assert block["switcher"]["real"] is None
    assert block["switcher"]["real"] != 0.0
    # An undefined point estimate carries no interval to report.
    assert "ci95" not in block["switcher"]


def test_real_is_none_not_zero_when_the_panel_has_no_unexposed_shoppers():
    only_exposed = [
        Shopper(exposed=True, basket=("A1",)),
        Shopper(exposed=True, basket=("B1",)),
    ]
    assert real_lift(only_exposed, brand_of_sku=BRAND_OF_SKU, brand="Crunch") is None


def test_real_is_none_when_no_unexposed_shopper_bought_the_brand():
    shoppers = [
        Shopper(exposed=True, basket=("A1",)),
        Shopper(exposed=False, basket=("B1", "B2")),
    ]
    assert real_lift(shoppers, brand_of_sku=BRAND_OF_SKU, brand="Crunch") is None


def test_no_result_anywhere_is_infinite_or_nan():
    """Sweep the degenerate combinations and demand a finite number or None."""
    arms = [
        {"A1": 0.0, "A2": 0.0, "B1": 0.0, "B2": 0.0},
        {"A1": 0.0, "A2": 0.0, "B1": 0.6, "B2": 0.4},
        {"A1": 1.0, "A2": 0.0, "B1": 0.0, "B2": 0.0},
        {"A1": 0.25, "A2": 0.25, "B1": 0.25, "B2": 0.25},
    ]
    for exposed in arms:
        for unexposed in arms:
            value = synth_lift(sim_result(exposed, unexposed),
                               brand_of_sku=BRAND_OF_SKU, brand="Crunch")
            assert value is None or math.isfinite(value), (exposed, unexposed, value)


# ---------------------------------------------------------------------------
# 2. behaviour against the simulator -- the PLAN S18 acceptance criteria
# ---------------------------------------------------------------------------
#
# The demo aisle cannot carry the `ad_receptivity = 0` null on its own:
# exposure there is a SELECTION, not a randomisation, so the exposed and
# unexposed arms differ in composition before the ad does anything. These two
# tests therefore run on a store built to remove that confound.

SYMMETRIC_BRANDS = {"K1": "Crunch", "K2": "Crunch", "Z1": "Zapp", "Z2": "Zapp"}

# Measured null distribution of the lift at ad_receptivity = 0 on this store
# at N_SYMMETRIC_RUNS, over 30 seeds: mean +0.0004, sd 0.011, largest |lift|
# 0.024. The tolerance below is ~4.5 sd of that -- wide enough that a pass is
# not luck, and still four times smaller than the +0.21 the same store shows
# at ad_receptivity = 0.25.
N_SYMMETRIC_RUNS = 100_000
NULL_TOLERANCE = 0.05
RECEPTIVITY_SWEEP = (0.0, 0.25, 0.5, 0.75, 1.0)


def symmetric_planogram() -> dict:
    """A single bay whose two brands are INTERCHANGEABLE.

    Every saliency term is matched across the two brands: the bay is 1.2 m
    wide and the two slots on each shelf sit at x = 0.05 and x = 0.65, so
    both slot centres are exactly 0.30 m from the bay centre and score the
    same centrality; facings, size, colour and shelf level all match; and the
    left/right order of the brands alternates between the two shelves.

    With `brand_affinity` equal for both brands, the whole model is invariant
    under swapping the Crunch and Zapp labels when `ad_receptivity` is 0.
    The advertised brand's share is then 0.5 in BOTH arms by symmetry, so the
    lift's null is exactly zero and the only thing left is Monte Carlo noise.

    A single bay also makes the receptivity sweep a PAIRED comparison: with
    one station there is no purchase-driven change in who is still shopping,
    so the random stream is identical at every receptivity and the unexposed
    arm is bit-for-bit unchanged across the sweep (asserted below).
    """

    def make_sku(sku_id: str, brand: str) -> dict:
        return {
            "sku_id": sku_id, "name": sku_id, "brand": brand, "category": "chips",
            "price": 30.0, "promo": False, "texture_url": f"/textures/{sku_id}.png",
            "color_lab": [60.0, 10.0, 10.0],
        }

    def make_slot(slot_id: str, sku_id: str, x_m: float) -> dict:
        return {"slot_id": slot_id, "sku_id": sku_id, "facings": 3,
                "x_m": x_m, "width_m": 0.5, "height_m": 0.22}

    return {
        "planogram_id": "symmetric_bay",
        "name": "Brand-symmetric single bay",
        "source": "manual",
        "skus": [make_sku("K1", "Crunch"), make_sku("Z1", "Zapp"),
                 make_sku("Z2", "Zapp"), make_sku("K2", "Crunch")],
        "creatives": [{"creative_id": "AD_1", "brand": "Crunch",
                       "texture_url": "/textures/ad_1.png"}],
        "bays": [{
            "bay_id": "S1", "type": "shelf", "width_m": 1.2, "height_m": 1.8,
            "station": {"camera_pos": [0.0, 1.5, 2.2], "look_at": [0.0, 1.1, 0.0]},
            "shelves": [
                {"shelf_id": "SH1", "height_m": 1.4, "level": "eye",
                 "slots": [make_slot("P1", "K1", 0.05), make_slot("P2", "Z1", 0.65)]},
                {"shelf_id": "SH2", "height_m": 1.0, "level": "below_eye",
                 "slots": [make_slot("P3", "Z2", 0.05), make_slot("P4", "K2", 0.65)]},
            ],
            "ad_slots": [{"ad_slot_id": "AD_SLOT", "type": "screen", "attached_to": "SH1",
                          "x_m": 0.35, "width_m": 0.5, "creative_id": "AD_1"}],
        }],
    }


def symmetric_policy(ad_receptivity: float) -> dict:
    """Equal brand affinity, so nothing but the ad can separate the brands."""
    return {
        "persona_id": "switcher",
        "goal_categories": ["chips"],
        "time_budget_s": {"mean": 120.0, "sd": 5.0},
        "exploration": 0.9,
        "brand_affinity": {"_default": 0.5, "Crunch": 0.5, "Zapp": 0.5},
        "price_sensitivity": 0.5,
        "promo_sensitivity": 0.0,
        "ad_receptivity": ad_receptivity,
        "purchase_threshold": 0.42,
        "dwell_ms": {"mu": 6.0, "sigma": 0.5},
        "fixations_per_station": {"lam": 8.0},
    }


@pytest.fixture(scope="module")
def symmetric_store():
    return build_store(symmetric_planogram())


def symmetric_run(store, ad_receptivity: float, seed: int) -> dict:
    return run(store, symmetric_policy(ad_receptivity), n_runs=N_SYMMETRIC_RUNS,
               seed=seed, variant_id="A")


def test_symmetric_store_really_is_symmetric(symmetric_store):
    """Guards the two acceptance tests below: if this bay ever stops being
    brand-symmetric, the null stops being zero and they would fail for a
    reason that has nothing to do with analytics/lift.py."""
    bay = symmetric_store.saliency["S1"]
    p = dict(zip(bay.target_ids, (float(v) for v in bay.p_saliency)))
    assert p["P1"] == pytest.approx(p["P2"])  # eye shelf: Crunch vs Zapp
    assert p["P3"] == pytest.approx(p["P4"])  # below-eye shelf: Zapp vs Crunch
    assert p["AD_SLOT"] > 0.0


def test_ad_receptivity_zero_gives_a_lift_of_approximately_zero(symmetric_store):
    """PLAN S18 acceptance: `ad_receptivity = 0` yields lift ~ 0.

    It cannot be exactly 0: the arms are two finite samples of shoppers, so
    the difference of their brand shares is binomial noise. NULL_TOLERANCE is
    ~4.5 standard deviations of the measured null (see the constant).
    """
    result = symmetric_run(symmetric_store, 0.0, seed=20250905)
    value = synth_lift(result, brand_of_sku=SYMMETRIC_BRANDS, brand="Crunch")

    assert value is not None
    print(f"\nad_receptivity=0.0 -> lift={value:+.5f} (tolerance +/-{NULL_TOLERANCE})")
    assert abs(value) < NULL_TOLERANCE


def test_raising_ad_receptivity_raises_lift_monotonically(symmetric_store):
    """PLAN S18 acceptance: raising receptivity raises the lift monotonically.

    Asserted as strictly non-decreasing over the whole sweep at a fixed seed.
    """
    lifts = []
    unexposed_shares = []
    for receptivity in RECEPTIVITY_SWEEP:
        result = symmetric_run(symmetric_store, receptivity, seed=20250905)
        lifts.append(synth_lift(result, brand_of_sku=SYMMETRIC_BRANDS, brand="Crunch"))
        unexposed_shares.append(
            brand_share(result["ad_unexposed_purchase_share"], SYMMETRIC_BRANDS, "Crunch")
        )

    print("\nreceptivity sweep (symmetric bay): "
          + "  ".join(f"{r}={v:+.4f}" for r, v in zip(RECEPTIVITY_SWEEP, lifts)))

    # The comparison is paired: only the exposed arm may move.
    assert unexposed_shares == pytest.approx([unexposed_shares[0]] * len(RECEPTIVITY_SWEEP))
    assert all(v is not None for v in lifts)
    assert lifts == sorted(lifts), f"lift is not monotonic in ad_receptivity: {lifts}"


def test_demo_aisle_sweep_is_monotonic_for_the_browser_persona(demo_planogram, demo_policies):
    """The same sweep on the COMMITTED demo aisle and a committed policy.

    The browser persona is the honest choice here: its purchase threshold
    leaves the ad pull unsaturated across the whole 0..1 range, so the true
    effect dominates the noise. See the S18 report for the two personas whose
    response saturates and is therefore not monotonic on this aisle.
    """
    store = build_store(demo_planogram)
    brand_of_sku = sku_brands(demo_planogram)
    base = demo_policies["browser"]

    lifts = []
    for receptivity in RECEPTIVITY_SWEEP:
        result = run(store, dict(base, ad_receptivity=receptivity), n_runs=10_000,
                     seed=20250905, variant_id="A", archetype="browser")
        lifts.append(synth_lift(result, brand_of_sku=brand_of_sku, brand="Crunch"))

    print("\nreceptivity sweep (demo aisle, browser): "
          + "  ".join(f"{r}={v:+.4f}" for r, v in zip(RECEPTIVITY_SWEEP, lifts)))

    # Monotonicity only. The null is NOT asserted here: on a three-bay aisle
    # the two arms differ in composition before the ad does anything (who
    # reaches the endcap is not random), and NULL_TOLERANCE was calibrated on
    # the symmetric bay at a different sample size. The null belongs to the
    # test above, where it really is zero.
    assert lifts == sorted(lifts), f"lift is not monotonic in ad_receptivity: {lifts}"


def test_planogram_helpers_read_the_committed_demo_aisle(demo_planogram):
    brand_of_sku = sku_brands(demo_planogram)
    assert brand_of_sku["SKU_001"] == "Crunch"
    assert brand_of_sku["SKU_002"] == "Zapp"
    assert len(brand_of_sku) == 24

    assert creative_brand(demo_planogram, "AD_1") == "Crunch"
    assert creative_brand(demo_planogram, "AD_2") == "Zapp"
    with pytest.raises(ValueError, match="AD_9"):
        creative_brand(demo_planogram, "AD_9")

    # Only B3_ENDCAP carries AD_1 in variant A; AD_2 is not placed at all.
    assert ad_slots_showing(demo_planogram, "AD_1") == ("B3_ENDCAP",)
    assert ad_slots_showing(demo_planogram, "AD_2") == ()


# ---------------------------------------------------------------------------
# 3. the bootstrap and the emitted block
# ---------------------------------------------------------------------------


def real_panel(n_exposed: int, n_unexposed: int, *, exposed_crunch: int,
               unexposed_crunch: int) -> list[Shopper]:
    """A panel with exactly the requested number of single-item baskets."""
    shoppers = []
    for i in range(n_exposed):
        shoppers.append(Shopper(exposed=True, basket=("A1" if i < exposed_crunch else "B1",)))
    for i in range(n_unexposed):
        shoppers.append(Shopper(exposed=False, basket=("A1" if i < unexposed_crunch else "B1",)))
    return shoppers


def test_bootstrap_ci_brackets_the_point_estimate():
    panel = real_panel(120, 120, exposed_crunch=60, unexposed_crunch=40)
    point = real_lift(panel, brand_of_sku=BRAND_OF_SKU, brand="Crunch")
    interval = bootstrap_lift_ci(panel, brand_of_sku=BRAND_OF_SKU, brand="Crunch", seed=3)

    assert point == pytest.approx((0.5 - 1 / 3) / (1 / 3))
    assert interval is not None
    low, high = interval
    assert low < point < high


def test_same_seed_reproduces_the_ci_exactly():
    panel = real_panel(80, 80, exposed_crunch=48, unexposed_crunch=32)
    kwargs = dict(brand_of_sku=BRAND_OF_SKU, brand="Crunch", n_boot=500)

    first = bootstrap_lift_ci(panel, seed=42, **kwargs)
    second = bootstrap_lift_ci(panel, seed=42, **kwargs)
    other = bootstrap_lift_ci(panel, seed=43, **kwargs)

    assert first == second  # exact equality, not approx
    assert first != other


def test_same_seed_reproduces_the_whole_block_exactly():
    panel = real_panel(80, 80, exposed_crunch=48, unexposed_crunch=32)
    synth = {"switcher": sim_result({"A1": 0.5, "B1": 0.5}, {"A1": 0.25, "B1": 0.75})}
    kwargs = dict(brand_of_sku=BRAND_OF_SKU, brand="Crunch", real={"switcher": panel})

    assert (ad_to_purchase_lift(synth, seed=11, **kwargs)
            == ad_to_purchase_lift(synth, seed=11, **kwargs))


def test_a_panel_with_no_variation_has_a_zero_width_interval():
    """Every exposed shopper bought Crunch and every unexposed one bought
    Zapp-then-Crunch in the same proportion: resampling cannot move it."""
    panel = [Shopper(exposed=True, basket=("A1",)) for _ in range(20)]
    panel += [Shopper(exposed=False, basket=("A1", "B1")) for _ in range(20)]
    interval = bootstrap_lift_ci(panel, brand_of_sku=BRAND_OF_SKU, brand="Crunch", seed=5)

    assert interval == pytest.approx((1.0, 1.0))


def test_bootstrap_returns_none_rather_than_an_interval_it_cannot_support():
    """No unexposed shopper bought the advertised brand: every resample's
    denominator is zero, so there is no interval -- not (inf, inf)."""
    panel = [Shopper(exposed=True, basket=("A1",)) for _ in range(20)]
    panel += [Shopper(exposed=False, basket=("B1",)) for _ in range(20)]
    assert bootstrap_lift_ci(panel, brand_of_sku=BRAND_OF_SKU, brand="Crunch", seed=5) is None


def test_bootstrap_rejects_a_bad_n_boot_or_ci():
    panel = real_panel(10, 10, exposed_crunch=6, unexposed_crunch=4)
    kwargs = dict(brand_of_sku=BRAND_OF_SKU, brand="Crunch", seed=1)
    with pytest.raises(ValueError, match="n_boot"):
        bootstrap_lift_ci(panel, n_boot=0, **kwargs)
    with pytest.raises(ValueError, match="ci"):
        bootstrap_lift_ci(panel, ci=1.0, **kwargs)


def test_seed_is_required_and_keyword_only():
    panel = real_panel(10, 10, exposed_crunch=6, unexposed_crunch=4)
    with pytest.raises(TypeError):
        bootstrap_lift_ci(panel, brand_of_sku=BRAND_OF_SKU, brand="Crunch")  # no seed


def test_block_reports_every_persona_and_the_population():
    synth = {
        "switcher": sim_result({"A1": 0.5, "B1": 0.5}, {"A1": 0.25, "B1": 0.75}),
        "loyalist": sim_result({"A1": 0.8, "B1": 0.2}, {"A1": 0.75, "B1": 0.25}),
        POPULATION_KEY: sim_result({"A1": 0.6, "B1": 0.4}, {"A1": 0.5, "B1": 0.5}),
    }
    block = ad_to_purchase_lift(synth, brand_of_sku=BRAND_OF_SKU, brand="Crunch", seed=1)

    assert set(block) == {"switcher", "loyalist", POPULATION_KEY}
    assert block["switcher"]["synth"] == pytest.approx(1.0)
    assert block["loyalist"]["synth"] == pytest.approx((0.8 - 0.75) / 0.75)
    assert block[POPULATION_KEY]["synth"] == pytest.approx(0.2)
    # No real panel was supplied, so no row claims a real number either way.
    assert all("real" not in row for row in block.values())


def test_undefined_synth_lift_omits_the_key_rather_than_writing_null():
    """schemas/metrics.schema.json types `synth` as a plain number, so an
    undefined synthetic lift has to be an absent key, not null."""
    synth = {"mission": sim_result({"A1": 0.5, "B1": 0.5}, {"A1": 0.0, "B1": 1.0})}
    block = ad_to_purchase_lift(synth, brand_of_sku=BRAND_OF_SKU, brand="Crunch", seed=1)

    assert "synth" not in block["mission"]
    assert block["mission"] == {}


def test_a_real_row_with_no_matching_synth_row_raises():
    synth = {"switcher": sim_result({"A1": 0.5, "B1": 0.5}, {"A1": 0.25, "B1": 0.75})}
    with pytest.raises(ValueError, match="loyalist"):
        ad_to_purchase_lift(
            synth,
            brand_of_sku=BRAND_OF_SKU,
            brand="Crunch",
            real={"loyalist": real_panel(4, 4, exposed_crunch=2, unexposed_crunch=1)},
            seed=1,
        )


def test_emitted_block_validates_against_the_metrics_schema(metrics_schema):
    panel = real_panel(60, 60, exposed_crunch=36, unexposed_crunch=24)
    synth = {
        "switcher": sim_result({"A1": 0.5, "B1": 0.5}, {"A1": 0.25, "B1": 0.75}),
        "mission": sim_result({"A1": 0.5, "B1": 0.5}, {"A1": 0.0, "B1": 1.0}),  # undefined
        POPULATION_KEY: sim_result({"A1": 0.6, "B1": 0.4}, {"A1": 0.5, "B1": 0.5}),
    }
    block = ad_to_purchase_lift(
        synth,
        brand_of_sku=BRAND_OF_SKU,
        brand="Crunch",
        real={"switcher": panel, "mission": real_panel(4, 4, exposed_crunch=2, unexposed_crunch=0)},
        seed=13,
    )

    # It must survive a JSON round trip: no numpy scalars, no tuples, no NaN.
    round_tripped = json.loads(json.dumps(block, allow_nan=False))
    assert round_tripped == block

    Draft7Validator(metrics_schema["properties"]["ad_to_purchase_lift"]).validate(block)

    document = {
        "experiment_id": "E1",
        "fit_variant": "A",
        "holdout_variants": ["B", "C"],
        "per_variant": {"A": {"attention_spearman": 0.6, "heatmap_kl": 0.1,
                              "purchase_share_mae": 0.02}},
        "decision_agreement": {"kpi": "focal_sku_purchase_share", "winner_real": "B",
                               "winner_synth": "B", "agree": True},
        "noise_ceiling": {"spearman_mean": 0.7, "ci95": [0.5, 0.85], "n_splits": 200},
        "ad_to_purchase_lift": block,
        "n_real_accepted": 30,
        "n_real_rejected": 4,
        "n_synth": 10_000,
    }
    Draft7Validator(metrics_schema).validate(document)

    assert block["switcher"]["real"] == pytest.approx(0.5)
    assert len(block["switcher"]["ci95"]) == 2
    assert block["switcher"]["ci95"][0] <= block["switcher"]["real"] <= block["switcher"]["ci95"][1]
    assert block["mission"]["real"] is None
    assert "ci95" not in block["mission"]


def test_block_defaults_to_a_thousand_bootstrap_resamples():
    assert DEFAULT_N_BOOT == 1000


def test_end_to_end_on_the_committed_demo_aisle(demo_planogram, demo_policies, metrics_schema):
    """The shape the S19 eval will actually call: four committed policies over
    the committed planogram, plus a hand-built real panel, through one call."""
    store = build_store(demo_planogram)
    brand_of_sku = sku_brands(demo_planogram)
    brand = creative_brand(demo_planogram, "AD_1")
    ad_slot_ids = ad_slots_showing(demo_planogram, "AD_1")

    synth = {
        persona: run(store, policy, n_runs=4_000, seed=20250905, variant_id="A",
                     archetype=persona)
        for persona, policy in demo_policies.items()
    }

    crunch = [s["sku_id"] for s in demo_planogram["skus"] if s["brand"] == "Crunch"]
    zapp = [s["sku_id"] for s in demo_planogram["skus"] if s["brand"] == "Zapp"]
    # 24 sessions: half saw the endcap creative, and those who did bought
    # Crunch two thirds of the time against one third for those who did not,
    # so the panel carries a real +100 % lift to bracket.
    sessions = []
    for i in range(24):
        saw_ad = i % 2 == 0
        buys_crunch = (i % 6) in (0, 2) if saw_ad else (i % 6) == 1
        sessions.append(session(
            saw_ad=ad_slot_ids[0] if saw_ad else None,
            bought=[crunch[i % len(crunch)] if buys_crunch else zapp[i % len(zapp)]],
        ))
    shoppers = split_panel(sessions, ad_slot_ids=ad_slot_ids)
    assert sum(s.exposed for s in shoppers) == 12

    block = ad_to_purchase_lift(
        synth,
        brand_of_sku=brand_of_sku,
        brand=brand,
        real={"switcher": shoppers},
        seed=20250905,
    )

    Draft7Validator(metrics_schema["properties"]["ad_to_purchase_lift"]).validate(block)
    assert set(block) == set(demo_policies)
    for persona, row in block.items():
        print(f"\n{persona:9s} synth={row.get('synth')} real={row.get('real')} "
              f"ci95={row.get('ci95')}")
        assert "synth" in row
        assert math.isfinite(row["synth"])

    switcher = block["switcher"]
    assert switcher["real"] == pytest.approx(1.0)  # 2/3 exposed vs 1/3 unexposed
    assert switcher["ci95"][0] <= switcher["real"] <= switcher["ci95"][1]
    assert all("real" not in block[p] for p in ("browser", "loyalist", "mission"))


# ---------------------------------------------------------------------------
# 4. the SYNTHETIC arm's interval
#
# A SimResult now carries `n_purchases_exposed` / `n_purchases_unexposed`, so
# the synthetic arms can be resampled. What comes back is Monte Carlo spread
# at this run size -- NOT a confidence interval over a population, and NOT a
# replacement for `ci95`, which stays the real panel's. These tests pin all
# three of those claims.
# ---------------------------------------------------------------------------

# Crunch = 0.50 exposed, 0.25 unexposed -> a hand-checkable lift of exactly +1.
ARM_EXPOSED = {"A1": 0.30, "A2": 0.20, "B1": 0.30, "B2": 0.20}
ARM_UNEXPOSED = {"A1": 0.15, "A2": 0.10, "B1": 0.45, "B2": 0.30}


def counted(n_exposed: int, n_unexposed: int, *, exposed=None, unexposed=None) -> dict:
    """A SimResult carrying both arm vectors AND their purchase-event counts."""
    return sim_result(
        exposed if exposed is not None else ARM_EXPOSED,
        unexposed if unexposed is not None else ARM_UNEXPOSED,
        n_purchases_exposed=n_exposed,
        n_purchases_unexposed=n_unexposed,
    )


def test_synth_interval_brackets_the_synthetic_point_estimate():
    result = counted(400, 600)
    point = synth_lift(result, brand_of_sku=BRAND_OF_SKU, brand="Crunch")
    interval = bootstrap_synth_lift_ci(
        result, brand_of_sku=BRAND_OF_SKU, brand="Crunch", seed=3
    )

    assert point == pytest.approx(1.0)
    assert interval is not None
    low, high = interval
    assert low < point < high


def test_synth_interval_matches_an_explicit_multinomial_bootstrap():
    """The implementation resamples each arm's purchase events as
    Binomial(n, brand share). That IS the advertised brand's marginal of a
    Multinomial(n, per-SKU share) resample summed over the brand's SKUs, so it
    must agree with a written-out multinomial bootstrap over the SKU vector.
    Checked numerically rather than asserted in a comment.
    """
    n_exposed, n_unexposed = 400, 600
    interval = bootstrap_synth_lift_ci(
        counted(n_exposed, n_unexposed),
        brand_of_sku=BRAND_OF_SKU, brand="Crunch", seed=17, n_boot=20_000,
    )

    order = ["A1", "A2", "B1", "B2"]
    crunch = [BRAND_OF_SKU[s] == "Crunch" for s in order]
    rng = np.random.default_rng(101)
    exposed = rng.multinomial(n_exposed, [ARM_EXPOSED[s] for s in order], size=20_000)
    unexposed = rng.multinomial(n_unexposed, [ARM_UNEXPOSED[s] for s in order], size=20_000)
    share_e = exposed[:, crunch].sum(axis=1) / n_exposed
    share_u = unexposed[:, crunch].sum(axis=1) / n_unexposed
    reference = (share_e - share_u) / share_u
    low, high = np.percentile(reference, [2.5, 97.5])

    assert interval[0] == pytest.approx(float(low), abs=0.03)
    assert interval[1] == pytest.approx(float(high), abs=0.03)


def test_synth_interval_narrows_as_the_purchase_event_counts_grow():
    """The whole point of carrying the counts. The two arm SHARE vectors are
    identical here -- only the number of events behind them differs -- so a
    method that ignored the counts could not tell these two runs apart."""
    def width(n: int) -> float:
        low, high = bootstrap_synth_lift_ci(
            counted(n, n), brand_of_sku=BRAND_OF_SKU, brand="Crunch", seed=5, n_boot=4000
        )
        return high - low

    small, large = width(100), width(10_000)
    print(f"\nsynthetic interval width: n=100 -> {small:.4f}, n=10,000 -> {large:.4f}")
    assert large < small / 5.0


def test_synth_interval_is_reproducible_from_its_seed():
    kwargs = dict(brand_of_sku=BRAND_OF_SKU, brand="Crunch")
    result = counted(400, 600)

    first = bootstrap_synth_lift_ci(result, seed=42, **kwargs)
    second = bootstrap_synth_lift_ci(result, seed=42, **kwargs)
    other = bootstrap_synth_lift_ci(result, seed=43, **kwargs)

    assert first == second
    assert first != other


def test_synth_interval_requires_an_explicit_seed():
    with pytest.raises(TypeError):
        bootstrap_synth_lift_ci(counted(400, 600), brand_of_sku=BRAND_OF_SKU, brand="Crunch")


def test_synth_interval_is_none_when_either_arm_recorded_no_purchases():
    """Nothing to resample. Same stance as an empty real panel."""
    kwargs = dict(brand_of_sku=BRAND_OF_SKU, brand="Crunch", seed=5)
    empty = {"A1": 0.0, "A2": 0.0, "B1": 0.0, "B2": 0.0}

    assert bootstrap_synth_lift_ci(counted(0, 600, exposed=empty), **kwargs) is None
    assert bootstrap_synth_lift_ci(counted(400, 0, unexposed=empty), **kwargs) is None


def test_synth_interval_is_none_when_too_few_resamples_have_a_denominator():
    """A thin unexposed arm that almost never buys the brand: most resamples
    land on a zero denominator, and the percentiles would then describe a
    minority of the draws. Same MIN_DEFINED_FRACTION rule as the real panel."""
    barely = {"A1": 0.02, "A2": 0.0, "B1": 0.98, "B2": 0.0}
    interval = bootstrap_synth_lift_ci(
        counted(400, 10, unexposed=barely),
        brand_of_sku=BRAND_OF_SKU, brand="Crunch", seed=5,
    )
    assert interval is None


def test_synth_interval_raises_on_a_simresult_that_carries_no_counts():
    """Same stance as `synth_lift` on a missing arm vector: a run that cannot
    answer the question says so, rather than returning None as if the arms
    were empty."""
    with pytest.raises(ValueError, match="n_purchases_exposed"):
        bootstrap_synth_lift_ci(
            sim_result(ARM_EXPOSED, ARM_UNEXPOSED),
            brand_of_sku=BRAND_OF_SKU, brand="Crunch", seed=5,
        )


def test_synth_interval_never_returns_inf_or_nan():
    arms = [
        {"A1": 0.0, "A2": 0.0, "B1": 0.0, "B2": 0.0},
        {"A1": 0.0, "A2": 0.0, "B1": 0.6, "B2": 0.4},
        {"A1": 1.0, "A2": 0.0, "B1": 0.0, "B2": 0.0},
        {"A1": 0.25, "A2": 0.25, "B1": 0.25, "B2": 0.25},
    ]
    for exposed in arms:
        for unexposed in arms:
            for n_exposed, n_unexposed in ((0, 0), (1, 1), (50, 50), (5000, 5000)):
                interval = bootstrap_synth_lift_ci(
                    counted(n_exposed, n_unexposed, exposed=exposed, unexposed=unexposed),
                    brand_of_sku=BRAND_OF_SKU, brand="Crunch", seed=2, n_boot=200,
                )
                assert interval is None or all(math.isfinite(v) for v in interval)


# --- the emitted block -------------------------------------------------------


def test_the_block_carries_the_synthetic_interval_under_its_own_key():
    block = ad_to_purchase_lift(
        {"switcher": counted(400, 600)},
        brand_of_sku=BRAND_OF_SKU, brand="Crunch", seed=11,
    )
    row = block["switcher"]

    assert row["synth"] == pytest.approx(1.0)
    assert len(row["synth_mc95"]) == 2
    assert row["synth_mc95"][0] < row["synth"] < row["synth_mc95"][1]
    # It is NOT ci95: that name is the real panel's, and no real panel was passed.
    assert "ci95" not in row
    assert "real" not in row


def test_the_synthetic_interval_does_not_displace_the_real_panels_ci95():
    """Decision: `ci95` keeps meaning the REAL panel's bootstrap, exactly as
    `noise_ceiling.ci95` does. Adding counts to the SimResult must leave it
    byte-identical, and add a separately named key instead."""
    panel = real_panel(60, 60, exposed_crunch=36, unexposed_crunch=24)
    kwargs = dict(brand_of_sku=BRAND_OF_SKU, brand="Crunch",
                  real={"switcher": panel}, seed=13)

    without = ad_to_purchase_lift({"switcher": sim_result(ARM_EXPOSED, ARM_UNEXPOSED)}, **kwargs)
    with_counts = ad_to_purchase_lift({"switcher": counted(400, 600)}, **kwargs)

    assert with_counts["switcher"]["real"] == without["switcher"]["real"]
    assert with_counts["switcher"]["ci95"] == without["switcher"]["ci95"]
    assert "synth_mc95" not in without["switcher"]
    assert "synth_mc95" in with_counts["switcher"]


def test_the_block_omits_the_synthetic_interval_when_the_synthetic_lift_is_undefined():
    """An undefined point estimate carries no interval -- the same rule the
    real side already follows."""
    undefined = counted(400, 600, unexposed={"A1": 0.0, "A2": 0.0, "B1": 0.6, "B2": 0.4})
    block = ad_to_purchase_lift(
        {"mission": undefined}, brand_of_sku=BRAND_OF_SKU, brand="Crunch", seed=1
    )

    assert block["mission"] == {}


def test_the_block_with_a_synthetic_interval_validates_against_the_schema(metrics_schema):
    block = ad_to_purchase_lift(
        {
            "switcher": counted(400, 600),
            POPULATION_KEY: counted(900, 900),
        },
        brand_of_sku=BRAND_OF_SKU,
        brand="Crunch",
        real={"switcher": real_panel(60, 60, exposed_crunch=36, unexposed_crunch=24)},
        seed=13,
    )

    round_tripped = json.loads(json.dumps(block, allow_nan=False))
    assert round_tripped == block
    Draft7Validator(metrics_schema["properties"]["ad_to_purchase_lift"]).validate(block)

    assert set(block["switcher"]) == {"synth", "synth_mc95", "real", "ci95"}
    assert set(block[POPULATION_KEY]) == {"synth", "synth_mc95"}


def test_the_block_is_reproducible_with_both_intervals():
    kwargs = dict(brand_of_sku=BRAND_OF_SKU, brand="Crunch",
                  real={"switcher": real_panel(60, 60, exposed_crunch=36, unexposed_crunch=24)})
    synth = {"switcher": counted(400, 600)}

    assert (ad_to_purchase_lift(synth, seed=11, **kwargs)
            == ad_to_purchase_lift(synth, seed=11, **kwargs))


def test_end_to_end_the_committed_simulator_now_supplies_its_own_interval(
    demo_planogram, demo_policies, metrics_schema
):
    """The counts come out of `run()` untouched by hand, so the synthetic
    interval is available for every row of the real eval."""
    store = build_store(demo_planogram)
    brand = creative_brand(demo_planogram, "AD_1")
    synth = {
        persona: run(store, policy, n_runs=4_000, seed=20250905, variant_id="A",
                     archetype=persona)
        for persona, policy in demo_policies.items()
    }

    block = ad_to_purchase_lift(
        synth, brand_of_sku=sku_brands(demo_planogram), brand=brand, seed=20250905
    )
    Draft7Validator(metrics_schema["properties"]["ad_to_purchase_lift"]).validate(block)

    for persona, row in block.items():
        interval = row["synth_mc95"]
        print(f"\n{persona:9s} synth={row['synth']:+.4f} "
              f"mc95=[{interval[0]:+.4f}, {interval[1]:+.4f}]  "
              f"n_exposed={synth[persona]['n_purchases_exposed']} "
              f"n_unexposed={synth[persona]['n_purchases_unexposed']}")
        assert all(math.isfinite(v) for v in interval)
        assert interval[0] <= row["synth"] <= interval[1]


# ---------------------------------------------------------------------------
# 5. the BETWEEN-VARIANT brand lift, and the control arm it needs
#
# `synth_lift` splits ONE run into ad-exposed and non-exposed trips. On a real
# store that split is a SELECTION -- who reaches the endcap is not random --
# which is why the null above needs a purpose-built symmetric bay before it is
# really zero.
#
# The Brand Lift a client commissions is not that. It is BETWEEN ARMS: the
# advertised brand's purchase share in a cell that saw the ad against a cell
# that did not. That needs an unexposed arm, and until data/variants/D.json
# there was none -- A and C BOTH carry AD_1, C only relocates it, so A-vs-C
# is a placement comparison.
# ---------------------------------------------------------------------------

VARIANTS_DIR = DATA / "variants"


@pytest.fixture(scope="module")
def variant_schema() -> dict:
    return load_json(SCHEMAS / "variant.schema.json")


def load_variant(variant_id: str) -> dict:
    return load_json(VARIANTS_DIR / f"{variant_id}.json")


def ad_creatives(planogram: dict) -> dict:
    """`ad_slot_id -> creative_id` for every ad slot, in planogram order."""
    return {ad["ad_slot_id"]: ad["creative_id"]
            for bay in planogram["bays"] for ad in bay["ad_slots"]}


def run_result(purchase_share: dict, *, variant_id: str,
               persona_id: str = "switcher", **extra) -> dict:
    """A SimResult carrying a whole-run `purchase_share`, which is what the
    between-variant comparison reads. The exposed/unexposed arm vectors are
    filled with values the between-variant number must NOT touch."""
    result = sim_result({"A1": 1.0, "A2": 0.0, "B1": 0.0, "B2": 0.0},
                        {"A1": 0.0, "A2": 0.0, "B1": 1.0, "B2": 0.0})
    result["variant_id"] = variant_id
    result["persona_id"] = persona_id
    result["purchase_share"] = purchase_share
    result.update(extra)
    return result


def counted_run(purchase_share: dict, n_events: int, *, variant_id: str,
                persona_id: str = "switcher", exposed_part=None) -> dict:
    """`run_result` plus two arm counts that add back up to `n_events`."""
    exposed = n_events // 2 if exposed_part is None else exposed_part
    return run_result(purchase_share, variant_id=variant_id, persona_id=persona_id,
                      n_purchases_exposed=exposed,
                      n_purchases_unexposed=n_events - exposed)


# --- the control arm ---------------------------------------------------------


def test_variant_d_validates_against_the_variant_schema(variant_schema):
    Draft7Validator(variant_schema).validate(load_variant("D"))


def test_variant_d_is_a_true_control_with_no_creative_anywhere(demo_planogram):
    """The property the whole Brand Lift rests on: resolved variant D shows no
    creative at all, so nobody in that arm can be ad-exposed."""
    resolved = resolve(demo_planogram, load_variant("D"))

    assert all(creative is None for creative in ad_creatives(resolved).values())
    assert ad_slots_showing(resolved, "AD_1") == ()
    assert ad_slots_showing(resolved, "AD_2") == ()


def test_variant_d_keeps_every_ad_slot_object_rather_than_deleting_it(demo_planogram):
    """CLAUDE.md's rule for empty shelf positions -- a real slot object with
    `sku_id: null` -- applied to ad slots. Deleting the slot would change the
    fixture as well as the creative."""
    resolved = resolve(demo_planogram, load_variant("D"))
    assert list(ad_creatives(resolved)) == list(ad_creatives(demo_planogram))

    base_ads = [ad for bay in demo_planogram["bays"] for ad in bay["ad_slots"]]
    resolved_ads = [ad for bay in resolved["bays"] for ad in bay["ad_slots"]]
    for before, after in zip(base_ads, resolved_ads):
        assert ({k: v for k, v in after.items() if k != "creative_id"}
                == {k: v for k, v in before.items() if k != "creative_id"})


def test_variant_a_and_variant_d_differ_only_in_ad_exposure(demo_planogram):
    """D is a control for the AD, not for the shelf. Blank both resolved
    planograms' ad creatives and what is left must be deep-equal: same SKUs,
    same prices, same slots, same facings, same shelf levels."""
    a = resolve(demo_planogram, load_variant("A"))
    d = resolve(demo_planogram, load_variant("D"))

    assert ad_creatives(a) != ad_creatives(d)  # they differ in exactly one thing

    for planogram in (a, d):
        for bay in planogram["bays"]:
            for ad in bay["ad_slots"]:
                ad["creative_id"] = None
    assert a == d


def test_a_and_c_are_both_exposed_arms_which_is_why_d_exists(demo_planogram):
    """The structural fact that made the Brand Lift unrunnable: A and C both
    carry AD_1. C relocates it, so A-vs-C measures PLACEMENT, not exposure."""
    a = resolve(demo_planogram, load_variant("A"))
    c = resolve(demo_planogram, load_variant("C"))

    assert ad_slots_showing(a, "AD_1") == ("B3_ENDCAP",)
    assert ad_slots_showing(c, "AD_1") == ("B1_TALKER",)


# --- the between-variant formula --------------------------------------------


def test_between_variant_lift_is_brand_share_and_lift_applied_to_two_runs():
    """Treated run: Crunch is 0.50 of purchases. Control run: 0.25.
    (0.50 - 0.25) / 0.25 = +1.0, reached through the same two helpers the
    within-run number uses."""
    treated = run_result({"A1": 0.30, "A2": 0.20, "B1": 0.30, "B2": 0.20}, variant_id="A")
    control = run_result({"A1": 0.15, "A2": 0.10, "B1": 0.45, "B2": 0.30}, variant_id="D")

    value = between_variant_lift(treated, control, brand_of_sku=BRAND_OF_SKU, brand="Crunch")
    assert value == pytest.approx(1.0)
    assert value == pytest.approx(lift(
        brand_share(treated["purchase_share"], BRAND_OF_SKU, "Crunch"),
        brand_share(control["purchase_share"], BRAND_OF_SKU, "Crunch"),
    ))


def test_between_variant_lift_reads_the_whole_run_where_synth_lift_reads_the_split():
    """The two numbers must not be confusable. `run_result` deliberately fills
    the exposed/unexposed arms with a pattern the between-variant number never
    sees, and the two answers come out different."""
    treated = run_result({"A1": 0.30, "A2": 0.20, "B1": 0.30, "B2": 0.20}, variant_id="A")
    control = run_result({"A1": 0.15, "A2": 0.10, "B1": 0.45, "B2": 0.30}, variant_id="D")

    within = synth_lift(treated, brand_of_sku=BRAND_OF_SKU, brand="Crunch")
    between = between_variant_lift(treated, control, brand_of_sku=BRAND_OF_SKU, brand="Crunch")

    assert within is None  # its unexposed arm bought no Crunch at all
    assert between == pytest.approx(1.0)

    # And changing only the arm vectors leaves the between-variant number alone.
    moved = dict(treated, ad_exposed_purchase_share={"A1": 0.0, "A2": 0.0, "B1": 0.5, "B2": 0.5})
    assert between_variant_lift(moved, control, brand_of_sku=BRAND_OF_SKU,
                                brand="Crunch") == pytest.approx(between)


def test_between_variant_lift_is_none_when_the_control_bought_none_of_the_brand():
    """A zero denominator is unanswerable, not infinite -- the same stance
    `lift` already takes for the within-run split."""
    treated = run_result({"A1": 0.5, "A2": 0.0, "B1": 0.5, "B2": 0.0}, variant_id="A")
    control = run_result({"A1": 0.0, "A2": 0.0, "B1": 0.6, "B2": 0.4}, variant_id="D")

    assert between_variant_lift(treated, control,
                                brand_of_sku=BRAND_OF_SKU, brand="Crunch") is None


def test_between_variant_lift_is_none_when_either_run_recorded_no_purchases():
    empty = {"A1": 0.0, "A2": 0.0, "B1": 0.0, "B2": 0.0}
    populated = {"A1": 0.5, "A2": 0.0, "B1": 0.5, "B2": 0.0}
    kwargs = dict(brand_of_sku=BRAND_OF_SKU, brand="Crunch")

    assert between_variant_lift(run_result(empty, variant_id="A"),
                                run_result(populated, variant_id="D"), **kwargs) is None
    assert between_variant_lift(run_result(populated, variant_id="A"),
                                run_result(empty, variant_id="D"), **kwargs) is None

    # An arm that bought only the other brand is a real -100 %, not undefined.
    only_zapp = {"A1": 0.0, "A2": 0.0, "B1": 0.6, "B2": 0.4}
    assert between_variant_lift(run_result(only_zapp, variant_id="A"),
                                run_result(populated, variant_id="D"),
                                **kwargs) == pytest.approx(-1.0)


def test_between_variant_lift_refuses_two_runs_of_the_same_variant():
    """Comparing a variant with itself is not an experiment; at best it reports
    0 and at worst it launders a seed difference as an ad effect."""
    treated = run_result({"A1": 0.5, "A2": 0.0, "B1": 0.5, "B2": 0.0}, variant_id="A")
    with pytest.raises(ValueError, match="same variant"):
        between_variant_lift(treated, dict(treated), brand_of_sku=BRAND_OF_SKU, brand="Crunch")


def test_between_variant_lift_refuses_to_compare_two_different_personas():
    """Mission-under-A against browser-under-D confounds the persona with the
    ad, and the result would still be reported as a Brand Lift."""
    treated = run_result({"A1": 0.5, "A2": 0.0, "B1": 0.5, "B2": 0.0},
                         variant_id="A", persona_id="mission")
    control = run_result({"A1": 0.25, "A2": 0.0, "B1": 0.75, "B2": 0.0},
                         variant_id="D", persona_id="browser")
    with pytest.raises(ValueError, match="persona"):
        between_variant_lift(treated, control, brand_of_sku=BRAND_OF_SKU, brand="Crunch")


def test_between_variant_lift_never_returns_inf_or_nan():
    arms = [
        {"A1": 0.0, "A2": 0.0, "B1": 0.0, "B2": 0.0},
        {"A1": 0.0, "A2": 0.0, "B1": 0.6, "B2": 0.4},
        {"A1": 1.0, "A2": 0.0, "B1": 0.0, "B2": 0.0},
        {"A1": 0.25, "A2": 0.25, "B1": 0.25, "B2": 0.25},
    ]
    for treated in arms:
        for control in arms:
            value = between_variant_lift(
                run_result(treated, variant_id="A"), run_result(control, variant_id="D"),
                brand_of_sku=BRAND_OF_SKU, brand="Crunch",
            )
            assert value is None or math.isfinite(value), (treated, control, value)


# --- the run's purchase-event count -----------------------------------------


def test_purchase_event_count_is_the_two_arms_added_back_together():
    """`purchase_share` covers the whole run, so the count behind it is both
    arms. Only the total matters -- how it splits is the within-run number's
    business."""
    assert purchase_event_count(counted_run({"A1": 1.0}, 1000, variant_id="A")) == 1000
    assert purchase_event_count(
        counted_run({"A1": 1.0}, 1000, variant_id="A", exposed_part=0)) == 1000
    assert purchase_event_count(
        counted_run({"A1": 1.0}, 1000, variant_id="A", exposed_part=997)) == 1000
    assert purchase_event_count(counted_run({"A1": 1.0}, 0, variant_id="D")) == 0


def test_purchase_event_count_raises_when_the_counts_are_absent():
    """Both fields are optional in schemas/simresult.schema.json. A run from
    before they existed cannot say how many events it saw, and must say so
    rather than report 0."""
    with pytest.raises(ValueError, match="n_purchases_exposed"):
        purchase_event_count(run_result({"A1": 1.0}, variant_id="A"))


# --- the between-variant Monte Carlo spread ----------------------------------

TREATED_SHARE = {"A1": 0.30, "A2": 0.20, "B1": 0.30, "B2": 0.20}   # Crunch 0.50
CONTROL_SHARE = {"A1": 0.15, "A2": 0.10, "B1": 0.45, "B2": 0.30}   # Crunch 0.25


def ab_pair(n_treated: int, n_control: int, *, treated=None, control=None):
    return (
        counted_run(TREATED_SHARE if treated is None else treated,
                    n_treated, variant_id="A"),
        counted_run(CONTROL_SHARE if control is None else control,
                    n_control, variant_id="D"),
    )


def test_between_variant_mc95_brackets_the_point_estimate():
    treated, control = ab_pair(400, 600)
    point = between_variant_lift(treated, control, brand_of_sku=BRAND_OF_SKU, brand="Crunch")
    spread = bootstrap_between_variant_mc95(
        treated, control, brand_of_sku=BRAND_OF_SKU, brand="Crunch", seed=3
    )

    assert point == pytest.approx(1.0)
    assert spread is not None
    low, high = spread
    assert low < point < high


def test_between_variant_mc95_is_the_same_resampling_as_the_within_run_spread():
    """CLAUDE.md forbids a second lift formula, and that has to include the
    resampling. Given the same two shares and the same two counts, the
    within-run and between-variant spreads must be BYTE-identical."""
    within = bootstrap_synth_lift_ci(
        sim_result(TREATED_SHARE, CONTROL_SHARE,
                   n_purchases_exposed=400, n_purchases_unexposed=600),
        brand_of_sku=BRAND_OF_SKU, brand="Crunch", seed=97,
    )
    between = bootstrap_between_variant_mc95(
        *ab_pair(400, 600), brand_of_sku=BRAND_OF_SKU, brand="Crunch", seed=97,
    )

    assert within is not None
    assert between == within


def test_between_variant_mc95_matches_an_explicit_multinomial_bootstrap():
    """Same identity the within-run spread relies on: the advertised brand's
    marginal of a Multinomial(n, per-SKU share) is Binomial(n, brand share).
    Checked numerically over `purchase_share` rather than the arm vectors."""
    n_treated, n_control = 400, 600
    spread = bootstrap_between_variant_mc95(
        *ab_pair(n_treated, n_control),
        brand_of_sku=BRAND_OF_SKU, brand="Crunch", seed=17, n_boot=20_000,
    )

    order = ["A1", "A2", "B1", "B2"]
    crunch = [BRAND_OF_SKU[s] == "Crunch" for s in order]
    rng = np.random.default_rng(101)
    treated = rng.multinomial(n_treated, [TREATED_SHARE[s] for s in order], size=20_000)
    control = rng.multinomial(n_control, [CONTROL_SHARE[s] for s in order], size=20_000)
    share_t = treated[:, crunch].sum(axis=1) / n_treated
    share_c = control[:, crunch].sum(axis=1) / n_control
    reference = (share_t - share_c) / share_c
    low, high = np.percentile(reference, [2.5, 97.5])

    assert spread[0] == pytest.approx(float(low), abs=0.03)
    assert spread[1] == pytest.approx(float(high), abs=0.03)


def test_between_variant_mc95_narrows_as_the_runs_grow():
    """The two share vectors are identical here; only the event counts behind
    them differ. A method that ignored the counts could not tell them apart."""
    def width(n: int) -> float:
        low, high = bootstrap_between_variant_mc95(
            *ab_pair(n, n), brand_of_sku=BRAND_OF_SKU, brand="Crunch", seed=5, n_boot=4000
        )
        return high - low

    small, large = width(100), width(10_000)
    print(f"\nbetween-variant spread width: n=100 -> {small:.4f}, n=10,000 -> {large:.4f}")
    assert large < small / 5.0


def test_between_variant_mc95_is_reproducible_from_its_seed():
    kwargs = dict(brand_of_sku=BRAND_OF_SKU, brand="Crunch")
    pair = ab_pair(400, 600)

    first = bootstrap_between_variant_mc95(*pair, seed=42, **kwargs)
    second = bootstrap_between_variant_mc95(*pair, seed=42, **kwargs)
    other = bootstrap_between_variant_mc95(*pair, seed=43, **kwargs)

    assert first == second
    assert first != other


def test_between_variant_mc95_requires_an_explicit_seed():
    with pytest.raises(TypeError):
        bootstrap_between_variant_mc95(*ab_pair(400, 600),
                                       brand_of_sku=BRAND_OF_SKU, brand="Crunch")


def test_between_variant_mc95_raises_on_runs_that_carry_no_counts():
    with pytest.raises(ValueError, match="n_purchases_exposed"):
        bootstrap_between_variant_mc95(
            run_result(TREATED_SHARE, variant_id="A"),
            run_result(CONTROL_SHARE, variant_id="D"),
            brand_of_sku=BRAND_OF_SKU, brand="Crunch", seed=5,
        )


def test_between_variant_mc95_is_none_when_a_run_recorded_no_purchases():
    kwargs = dict(brand_of_sku=BRAND_OF_SKU, brand="Crunch", seed=5)
    empty = {"A1": 0.0, "A2": 0.0, "B1": 0.0, "B2": 0.0}

    assert bootstrap_between_variant_mc95(*ab_pair(0, 600, treated=empty), **kwargs) is None
    assert bootstrap_between_variant_mc95(*ab_pair(400, 0, control=empty), **kwargs) is None


def test_between_variant_mc95_is_none_when_too_few_draws_have_a_denominator():
    """A thin control that almost never buys the brand: most draws land on a
    zero denominator, and the percentiles would describe a minority."""
    barely = {"A1": 0.02, "A2": 0.0, "B1": 0.98, "B2": 0.0}
    assert bootstrap_between_variant_mc95(
        *ab_pair(400, 10, control=barely),
        brand_of_sku=BRAND_OF_SKU, brand="Crunch", seed=5,
    ) is None


def test_between_variant_mc95_never_returns_inf_or_nan():
    arms = [
        {"A1": 0.0, "A2": 0.0, "B1": 0.0, "B2": 0.0},
        {"A1": 0.0, "A2": 0.0, "B1": 0.6, "B2": 0.4},
        {"A1": 1.0, "A2": 0.0, "B1": 0.0, "B2": 0.0},
        {"A1": 0.25, "A2": 0.25, "B1": 0.25, "B2": 0.25},
    ]
    for treated in arms:
        for control in arms:
            for n_treated, n_control in ((0, 0), (1, 1), (50, 50), (5000, 5000)):
                spread = bootstrap_between_variant_mc95(
                    *ab_pair(n_treated, n_control, treated=treated, control=control),
                    brand_of_sku=BRAND_OF_SKU, brand="Crunch", seed=2, n_boot=200,
                )
                assert spread is None or all(math.isfinite(v) for v in spread)


def test_between_variant_mc95_rejects_a_bad_n_boot_or_ci():
    kwargs = dict(brand_of_sku=BRAND_OF_SKU, brand="Crunch", seed=1)
    with pytest.raises(ValueError, match="n_boot"):
        bootstrap_between_variant_mc95(*ab_pair(400, 600), n_boot=0, **kwargs)
    with pytest.raises(ValueError, match="ci"):
        bootstrap_between_variant_mc95(*ab_pair(400, 600), ci=1.0, **kwargs)


def test_the_between_variant_number_is_not_emitted_into_the_metrics_block():
    """schemas/metrics.schema.json has no key for it, and the schema is the only
    cross-track contract. The between-variant number is returned to its caller;
    it never quietly appears in the reported block."""
    block = ad_to_purchase_lift(
        {"switcher": counted(400, 600)}, brand_of_sku=BRAND_OF_SKU, brand="Crunch", seed=1
    )
    assert set(block["switcher"]) == {"synth", "synth_mc95"}


# --- acceptance: the estimator against a store with a known answer -----------


def symmetric_control_planogram() -> dict:
    """The brand-symmetric bay with its creative removed -- exactly what
    data/variants/D.json does to the demo aisle, through the same
    `set_ad_creative -> null` patch and the same `resolve()`."""
    return resolve(symmetric_planogram(), {
        "variant_id": "D_SYM",
        "base_planogram_id": "symmetric_bay",
        "name": "Brand-symmetric bay, no creative",
        "patches": [{"op": "set_ad_creative", "ad_slot_id": "AD_SLOT", "creative_id": None}],
    })


@pytest.fixture(scope="module")
def symmetric_control_store():
    return build_store(symmetric_control_planogram())


@pytest.fixture(scope="module")
def symmetric_control_result(symmetric_control_store):
    """The control arm. `ad_receptivity` reaches the utility ONLY through
    `brand_seen`, and nothing is advertised here, so this one run serves the
    whole sweep -- asserted below rather than assumed."""
    return run(symmetric_control_store, symmetric_policy(0.0), n_runs=N_SYMMETRIC_RUNS,
               seed=20250905, variant_id="D")


def test_the_control_store_has_no_ad_target_at_all(symmetric_store, symmetric_control_store):
    assert symmetric_store.is_ad.any()
    assert not symmetric_control_store.is_ad.any()
    assert "AD_SLOT" in symmetric_control_store.ad_slot_ids  # the slot object survives


def test_the_control_arm_records_no_exposed_purchases(symmetric_control_result):
    """Which is exactly why the within-run split cannot answer this question on
    a control arm, and why the between-variant function has to exist."""
    assert symmetric_control_result["n_purchases_exposed"] == 0
    assert symmetric_control_result["n_purchases_unexposed"] > 0
    assert synth_lift(symmetric_control_result,
                      brand_of_sku=SYMMETRIC_BRANDS, brand="Crunch") is None


def test_the_control_arm_does_not_move_with_ad_receptivity(symmetric_control_store,
                                                           symmetric_control_result):
    """`ad_receptivity` only ever multiplies a term gated on having seen an ad
    at this bay. With no creative there is nothing to see, so the control run is
    bit-for-bit identical across the sweep."""
    for receptivity in (0.5, 1.0):
        other = run(symmetric_control_store, symmetric_policy(receptivity),
                    n_runs=N_SYMMETRIC_RUNS, seed=20250905, variant_id="D")
        assert other["purchase_share"] == symmetric_control_result["purchase_share"]


def test_between_variant_null_is_zero_when_nobody_is_receptive(symmetric_store,
                                                               symmetric_control_result):
    """The between-variant analogue of the PLAN S18 null. On a bay whose two
    brands are interchangeable, removing the creative removes attention mass
    symmetrically, so the true between-variant lift at `ad_receptivity = 0` is
    exactly zero and only Monte Carlo noise is left."""
    treated = symmetric_run(symmetric_store, 0.0, seed=20250905)
    value = between_variant_lift(treated, symmetric_control_result,
                                 brand_of_sku=SYMMETRIC_BRANDS, brand="Crunch")

    assert value is not None
    print(f"\nbetween-variant null (receptivity=0): {value:+.5f} "
          f"(tolerance +/-{NULL_TOLERANCE})")
    assert abs(value) < NULL_TOLERANCE


def test_between_variant_lift_rises_monotonically_with_ad_receptivity(symmetric_store,
                                                                      symmetric_control_result):
    """PLAN S18's monotonicity acceptance, applied between arms instead of
    within one run. The control is held fixed, so the whole movement is the
    treated arm responding to the ad."""
    lifts = []
    for receptivity in RECEPTIVITY_SWEEP:
        treated = symmetric_run(symmetric_store, receptivity, seed=20250905)
        lifts.append(between_variant_lift(treated, symmetric_control_result,
                                          brand_of_sku=SYMMETRIC_BRANDS, brand="Crunch"))

    print("\nbetween-variant receptivity sweep (symmetric bay): "
          + "  ".join(f"{r}={v:+.4f}" for r, v in zip(RECEPTIVITY_SWEEP, lifts)))
    assert all(v is not None for v in lifts)
    assert lifts == sorted(lifts), f"between-variant lift is not monotonic: {lifts}"


# --- the committed demo aisle: A against D -----------------------------------

EVAL_RUNS = 10_000
EVAL_SEED = 20250905
PERSONA_SHARES = {"mission": 0.35, "browser": 0.25, "loyalist": 0.25, "switcher": 0.15}


@pytest.fixture(scope="module")
def committed_arms(demo_planogram, demo_policies):
    """`(treated, control)` -- one SimResult per persona plus the share-weighted
    population row, for variant A and for variant D, at one run size and one
    seed so every test below reads the same two arms."""
    arms = {}
    for variant_id in ("A", "D"):
        store = build_store(resolve(demo_planogram, load_variant(variant_id)))
        per_persona = {
            persona: run(store, policy, n_runs=EVAL_RUNS, seed=EVAL_SEED,
                         variant_id=variant_id, archetype=persona)
            for persona, policy in demo_policies.items()
        }
        per_persona[POPULATION_KEY] = combine(
            [per_persona[p] for p in PERSONA_SHARES],
            [PERSONA_SHARES[p] for p in PERSONA_SHARES],
        )
        arms[variant_id] = per_persona
    return arms["A"], arms["D"]


def test_the_committed_control_arm_has_no_exposed_shoppers(demo_planogram, demo_policies):
    """Variant D on the real aisle: no creative, therefore no ad fixation
    target, therefore no exposed purchases and no within-run lift."""
    store = build_store(resolve(demo_planogram, load_variant("D")))
    assert not store.is_ad.any()

    result = run(store, demo_policies["browser"], n_runs=EVAL_RUNS, seed=EVAL_SEED,
                 variant_id="D", archetype="browser")
    assert result["n_purchases_exposed"] == 0
    assert result["n_purchases_unexposed"] > 0
    assert all(v == 0.0 for v in result["ad_slot_attention"].values())
    assert synth_lift(result, brand_of_sku=sku_brands(demo_planogram), brand="Crunch") is None


def test_between_variant_brand_lift_on_the_committed_a_and_d_variants(demo_planogram,
                                                                      committed_arms):
    """The number this whole exercise exists to produce: Crunch's purchase share
    under A (ad present) against D (ad absent), per persona and for the
    population, with the honest Monte Carlo resolution beside it.

    No sign is asserted. At this run size most rows' spreads straddle zero, and
    a test that demanded a positive lift would be asserting a result the data
    does not resolve. What IS asserted is that every row is finite, defined, and
    bracketed by its own spread; the printed `resolved` / `UNRESOLVED` column is
    the finding.
    """
    brand_of_sku = sku_brands(demo_planogram)
    brand = creative_brand(demo_planogram, "AD_1")
    treated_arm, control_arm = committed_arms

    assert brand == "Crunch"
    print(f"\nBetween-variant Brand Lift -- A (AD_1 on "
          f"{ad_slots_showing(demo_planogram, 'AD_1')[0]}) vs D (no creative), "
          f"{brand}, n={EVAL_RUNS} shoppers per persona per arm:")
    for key in list(PERSONA_SHARES) + [POPULATION_KEY]:
        treated, control = treated_arm[key], control_arm[key]
        share_a = brand_share(treated["purchase_share"], brand_of_sku, brand)
        share_d = brand_share(control["purchase_share"], brand_of_sku, brand)
        value = between_variant_lift(treated, control, brand_of_sku=brand_of_sku, brand=brand)
        spread = bootstrap_between_variant_mc95(
            treated, control, brand_of_sku=brand_of_sku, brand=brand, seed=EVAL_SEED
        )
        verdict = "resolved" if spread[0] > 0.0 or spread[1] < 0.0 else "UNRESOLVED"
        print(f"  {key:10s} A={share_a:.5f}  D={share_d:.5f}  lift={value:+.4f}  "
              f"mc95=[{spread[0]:+.4f}, {spread[1]:+.4f}]  "
              f"n_A={purchase_event_count(treated)} n_D={purchase_event_count(control)}  "
              f"{verdict}")

        assert value is not None and math.isfinite(value)
        assert spread is not None and all(math.isfinite(v) for v in spread)
        assert spread[0] <= value <= spread[1]


def test_the_within_run_split_overstates_the_between_arm_brand_lift(demo_planogram,
                                                                    committed_arms):
    """The reason the control arm had to exist.

    Within one run, ad exposure is a SELECTION: a shopper only fixates the
    B3_ENDCAP creative if they walked to that endcap, and walking there is
    already correlated with wanting what is on it. So the within-run exposed
    arm was likelier to buy the advertised brand before the ad did anything,
    and `synth_lift` charges the ad for all of it.

    A-vs-D has no such selection -- the two arms are the same population, and
    the only difference is a creative on the wall. On the committed aisle the
    within-run number comes out roughly an order of magnitude larger.
    """
    brand_of_sku = sku_brands(demo_planogram)
    brand = creative_brand(demo_planogram, "AD_1")
    treated_arm, control_arm = committed_arms

    treated = treated_arm[POPULATION_KEY]
    within = synth_lift(treated, brand_of_sku=brand_of_sku, brand=brand)
    between = between_variant_lift(treated, control_arm[POPULATION_KEY],
                                   brand_of_sku=brand_of_sku, brand=brand)

    print(f"\npopulation: within-run split={within:+.4f}  A-vs-D={between:+.4f}  "
          f"selection accounts for {within - between:+.4f}")
    assert within is not None and between is not None
    assert within > between + 0.03


def test_a_population_rows_event_count_never_overstates_its_precision(committed_arms):
    """`purchase_event_count` on a `combine` result adds two Kish effective
    counts, which is not the Kish count of the pooled run. It is provably
    conservative -- Kish's n_eff is concave and homogeneous of degree 1, hence
    superadditive -- and this pins that on the committed arms rather than
    leaving it as a claim in a docstring. Exact for the control arm, whose
    exposed count is 0.
    """
    def kish_over_totals(per_persona: dict) -> int:
        live = [(PERSONA_SHARES[p], purchase_event_count(per_persona[p]))
                for p in PERSONA_SHARES if purchase_event_count(per_persona[p]) > 0]
        weight = sum(share for share, _ in live)
        return round(weight * weight / sum(share * share / n for share, n in live))

    treated_arm, control_arm = committed_arms

    for label, per_persona in (("A", treated_arm), ("D", control_arm)):
        reported = purchase_event_count(per_persona[POPULATION_KEY])
        pooled = kish_over_totals(per_persona)
        print(f"\n{label}: population count reported={reported} "
              f"Kish over per-persona totals={pooled}")
        assert reported <= pooled

    # The control arm has no exposed events at all, so its sum of one live Kish
    # count is exactly the Kish count of the whole run.
    assert purchase_event_count(control_arm[POPULATION_KEY]) == kish_over_totals(control_arm)
    assert control_arm[POPULATION_KEY]["n_purchases_exposed"] == 0
