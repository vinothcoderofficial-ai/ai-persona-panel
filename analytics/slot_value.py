"""Ad slot value -- what one placement is worth in money (PLAN S25).

S24 ranks placements by Ad-to-Purchase Lift. This module puts a price on the
winner, so a shelf or an endcap can be sold, budgeted or argued about before
anything is on it. It adds no new attention model, no new lift arithmetic and
no new simulation: the lift comes from `analytics.lift.synth_lift` over an
`api.app.simcache.population` run, exactly as `analytics/optimizer.py` scores
its candidates, and everything here is multiplication on top of that number.

PLAN's formula, and how it was resolved
---------------------------------------
PLAN section 6 writes:

    slot value/week = predicted incremental units x margin x store-weeks

That is dimensionally loose: a per-week value multiplied by a store-week count
is not a per-week value, and reading it literally would either double-count the
weeks or leave the result in no unit at all. The coherent reading, and the one
implemented here, is

    value over the period  =  incremental units per store-week
                              x  margin per unit
                              x  store-weeks                       [currency]

    store-weeks            =  n_stores x n_weeks
    incremental units      =  baseline brand units per store-week x lift

so the dimensions cancel as (units / store-week) x (currency / unit) x
(store-weeks) = currency. The output is therefore money **over the whole
period asked for**, not money per week. The per-week figure PLAN's left-hand
side names is recoverable and is reported alongside as
`SlotValue.value_per_store_week` -- the value divided by the store-weeks,
which is invariant to how many store-weeks were requested. Every parameter
and every returned number names its unit in the signature and the docstring
below, because the whole failure mode here is a number in the wrong unit that
still looks plausible.

Two of the three inputs are assumptions, and this module says so
---------------------------------------------------------------
**Neither margin nor sales volume exists anywhere in this repo.** SKUs in
`data/planograms/demo_aisle.json` carry `price` and `promo` and nothing else
commercial -- no cost, no margin, no margin rate, and `price` does not even
carry a currency. The simulator reports normalised *shares*
(`ad_exposed_purchase_share`, `ad_unexposed_purchase_share`,
`purchase_share`), and `n_runs` counts shoppers rather than purchases, so
there is no unit volume and no store traffic figure to read either.

Margin, baseline volume, store count and week count are therefore **commercial
assumptions supplied by the caller**, not measurements, and this module is
built so that cannot be forgotten:

* `Assumptions` has **no defaults on any field.** There is no "industry
  standard 30%" here and no assumed shopper count per store-week. A caller who
  wants margin from `price` calls `margin_per_unit_from_price` and names the
  rate; nothing derives margin from price implicitly.
* `Assumptions` also requires a `currency` label and a one-line `basis`, so a
  printed figure carries both its unit and its provenance. A rand number with
  no stated source is the thing this module exists not to produce.
* `summary()` prints the measured lift and the assumed money on separate,
  labelled lines, and `assumptions_block()` prints the four numbers on their
  own. A reader must not be able to mistake any of this for a measured result.

Undefined is not zero
---------------------
`synth_lift` returns None when a configuration has no exposed arm, no
unexposed arm, or an unexposed arm that bought none of the advertised brand --
S24 hits this on the "creative unplaced" candidate. A value built on a None
lift is None all the way down, never 0.0: "this slot is worth nothing" and
"this question has no answer here" are different claims and only one of them
is true.

What the number inherits from S24
---------------------------------
The underlying ranking is **not resolved** at 10,000 shoppers.
docs/METHODOLOGY.md section 12.13 records it: re-rolling the same simulation at
seeds 42-46 moves the top pick between +6.2% and +14.2% and the current
placement between +1.3% and +8.9%, and those ranges overlap. A value built on
the seed-42 lift carries exactly that uncertainty, so every `summary()` says
the figure is a point estimate at one seed whose underlying lift is not
resolved.

`SlotValue` carries S24's `SeedSpread` through to money: because value is
linear in lift at fixed assumptions, pricing each seed's lift gives the range
of values those seeds produce. **That is Monte Carlo run-to-run variability,
not a confidence interval,** for the reason section 12.7 gives -- a committed
SimResult holds normalised shares, so there is nothing to resample and any
interval printed here would be fabricated. The disclaimer is printed every
time the range is.

Pure: no HTTP, no file I/O of its own, no globals, no wall-clock randomness.
The one dependency that touches disk is `api.app.simcache.population`, which
is injectable as `simulate`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Sequence

from analytics.lift import ad_slots_showing, creative_brand, sku_brands, synth_lift
from analytics.optimizer import (
    DEFAULT_N_SYNTH,
    DEFAULT_SEED,
    Ranking,
    SeedSpread,
    ad_purchase_lift_objective,
    variant_id_for,
)
from api.app import simcache
from api.app.resolve import resolve

# What the measured input is called wherever it is printed. It is the ONLY
# measured quantity in this module; everything else on a printed line is an
# assumption or arithmetic over one.
MEASURED_METRIC = "ad-to-purchase lift"


# ---------------------------------------------------------------------------
# The commercial assumptions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Assumptions:
    """The commercial inputs. **None of these is measured by this project.**

    Nothing in `schemas/`, `data/` or a `SimResult` supplies margin or sales
    volume: SKUs carry `price` (with no currency) and the simulator reports
    normalised purchase shares. Every field below is therefore an assumption
    the caller supplies -- asserted, not measured -- and every field is
    required. A default here would be an invented margin rate or an invented
    store count that a reader would have no way to spot.

    Fields, with their units:

    `baseline_brand_units_per_store_week` -- **units per store per week.** The
        advertised brand's unit sales in one store in one week WITHOUT the ad
        exposure the lift is measured against. It is a brand-level baseline,
        not a category one: `synth_lift` is a relative lift on the advertised
        brand's share of purchases in the unexposed arm, so scaling anything
        wider by it overstates the answer.
    `margin_per_unit` -- **currency per unit.** Contribution margin on one unit
        of the advertised brand. Not price, and not derived from price unless
        the caller does it themselves via `margin_per_unit_from_price`.
    `n_stores` -- **stores.** How many stores the placement would run in.
    `n_weeks` -- **weeks.** How long it would run for.
    `currency` -- the unit `margin_per_unit` and every reported value are in.
        Required because the planogram's own `price` field carries no currency,
        so there is nothing to inherit one from.
    `basis` -- one line saying where these four numbers came from: a category
        report, a client's own sell-through, or a stated hypothesis. Required
        and printed, because a rand figure whose provenance is unrecorded is
        indistinguishable from one that was guessed.

    Non-positive volume or margin, and fewer than one store or one week, raise
    ValueError. They are not configurations this module models, and silently
    accepting them would produce a confident zero or a sign flip.
    """

    baseline_brand_units_per_store_week: float
    margin_per_unit: float
    n_stores: int
    n_weeks: int
    currency: str
    basis: str

    def __post_init__(self) -> None:
        if not self.baseline_brand_units_per_store_week > 0:
            raise ValueError(
                "baseline_brand_units_per_store_week must be positive (units per store per "
                f"week), got {self.baseline_brand_units_per_store_week!r}"
            )
        if not self.margin_per_unit > 0:
            raise ValueError(
                f"margin_per_unit must be positive (currency per unit), got "
                f"{self.margin_per_unit!r}"
            )
        if int(self.n_stores) < 1:
            raise ValueError(f"n_stores must be at least 1 store, got {self.n_stores!r}")
        if int(self.n_weeks) < 1:
            raise ValueError(f"n_weeks must be at least 1 week, got {self.n_weeks!r}")
        if not str(self.currency).strip():
            raise ValueError(
                "currency must be a non-empty label; the planogram's price field carries no "
                "currency, so a reported value has no unit without one"
            )
        if not str(self.basis).strip():
            raise ValueError(
                "basis must say in one line where these numbers came from; they are assumed, "
                "not measured, and an unrecorded source cannot be checked"
            )

    @property
    def store_weeks(self) -> float:
        """`n_stores x n_weeks`, in **store-weeks** -- the period the value covers."""
        return float(self.n_stores) * float(self.n_weeks)


def margin_per_unit_from_price(
    planogram: Mapping[str, Any],
    sku_id: str,
    *,
    margin_rate: float,
) -> float:
    """`price x margin_rate` for one SKU, in **currency per unit**.

    The planogram's `price` is data; the rate that turns it into margin is not,
    so `margin_rate` is keyword-only and has no default. This function exists
    precisely so that going from price to margin is an explicit, visible step
    the caller took -- nothing else in this module ever looks at `price`.

    One SKU, not a brand: a brand with several SKUs at several prices would
    need a sales-mix assumption on top, and that assumption belongs to the
    caller rather than being averaged in here where nobody would see it.

    `margin_rate` is a fraction of price in (0, 1]. Raises ValueError outside
    that, and for a SKU the planogram does not have.
    """
    if not 0.0 < float(margin_rate) <= 1.0:
        raise ValueError(
            f"margin_rate must be a fraction of price in (0, 1], got {margin_rate!r}"
        )
    for sku in planogram["skus"]:
        if sku["sku_id"] == sku_id:
            return float(sku["price"]) * float(margin_rate)
    raise ValueError(f"planogram has no sku {sku_id!r}")


# ---------------------------------------------------------------------------
# The formula
# ---------------------------------------------------------------------------


def incremental_units_per_store_week(
    lift: Optional[float],
    assumptions: Assumptions,
) -> Optional[float]:
    """`baseline units per store-week x lift`, in **units per store-week**.

    The first link of the chain: a relative lift on the advertised brand's
    purchase share becomes a unit volume only once the caller says what volume
    it is relative to. A negative lift gives negative incremental units, which
    is a real answer -- the placement costs sales.

    Returns None when `lift` is None (the lift is undefined for that
    configuration), never 0.0.
    """
    if lift is None:
        return None
    return float(assumptions.baseline_brand_units_per_store_week) * float(lift)


def value_of_lift(lift: Optional[float], assumptions: Assumptions) -> Optional[float]:
    """The whole formula, in **currency over the period**.

        incremental units per store-week
          x margin per unit
          x store-weeks (= n_stores x n_weeks)

    (units / store-week) x (currency / unit) x (store-weeks) = currency, so the
    result is money over the period the assumptions describe -- NOT money per
    week. Divide by `assumptions.store_weeks` for the per-store-week rate;
    `SlotValue.value_per_store_week` does exactly that.

    Returns None when `lift` is None. See the module docstring: undefined is
    not zero.
    """
    units = incremental_units_per_store_week(lift, assumptions)
    if units is None:
        return None
    return units * float(assumptions.margin_per_unit) * assumptions.store_weeks


def _price_spread(lift_spread: Optional[SeedSpread],
                  assumptions: Assumptions) -> Optional[SeedSpread]:
    """S24's per-seed lifts, each priced with the same assumptions.

    Value is linear in lift at fixed assumptions, so this is the same range
    expressed in money. `low`/`high` are recomputed as the min and max of the
    priced values rather than scaled from the lift bounds, so a range spanning
    zero comes out the right way round.

    Still Monte Carlo run-to-run spread and still not a confidence interval --
    see `SeedSpread`, which carries that documentation, and section 12.7.
    """
    if lift_spread is None:
        return None
    values = tuple(float(value_of_lift(value, assumptions)) for value in lift_spread.values)
    return SeedSpread(seeds=lift_spread.seeds, values=values,
                      low=min(values), high=max(values))


# ---------------------------------------------------------------------------
# One priced placement
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SlotValue:
    """One placement, priced. What is measured and what is assumed, side by side.

    Measured (simulated): `lift` -- `analytics.lift.synth_lift` over an
    `api.app.simcache.population` run at `n_synth` shoppers and `seed`. It is
    the only number here that came from the model.

    Assumed: everything in `assumptions`.

    Derived: `incremental_units_per_store_week` (units per store-week), `value`
    (currency over `assumptions.store_weeks`) and `value_per_store_week`
    (currency per store-week).

    All four of `lift`, `incremental_units_per_store_week`, `value` and
    `value_per_store_week` are None together when the lift is undefined for
    this configuration. None of them is ever 0.0 standing in for that.

    `lift_spread` and `value_spread` are S24's `SeedSpread` -- Monte Carlo
    run-to-run variability across simulation seeds, **not a confidence
    interval**. `rank` and `is_current` are populated only when the row came
    from an optimizer `Ranking`.
    """

    label: str
    assumptions: Assumptions
    lift: Optional[float]
    incremental_units_per_store_week: Optional[float]
    value: Optional[float]
    creative_id: Optional[str] = None
    brand: Optional[str] = None
    ad_slot_ids: tuple[str, ...] = ()
    n_synth: Optional[int] = None
    seed: Optional[int] = None
    variant_id: Optional[str] = None
    sim_run_id: Optional[str] = None
    lift_spread: Optional[SeedSpread] = None
    value_spread: Optional[SeedSpread] = None
    rank: Optional[int] = None
    is_current: Optional[bool] = None

    @property
    def value_per_store_week(self) -> Optional[float]:
        """`value / store_weeks`, in **currency per store-week**.

        This is the per-week figure PLAN section 6's left-hand side names. It
        is invariant to how many store-weeks were asked for, which is the point
        of reporting both.
        """
        if self.value is None:
            return None
        return self.value / self.assumptions.store_weeks


def price_from_lift(
    lift: Optional[float],
    assumptions: Assumptions,
    *,
    label: str,
    lift_spread: Optional[SeedSpread] = None,
    creative_id: Optional[str] = None,
    brand: Optional[str] = None,
    ad_slot_ids: Sequence[str] = (),
    n_synth: Optional[int] = None,
    seed: Optional[int] = None,
    variant_id: Optional[str] = None,
    sim_run_id: Optional[str] = None,
    rank: Optional[int] = None,
    is_current: Optional[bool] = None,
) -> SlotValue:
    """Price a lift that has already been computed. Pure arithmetic, no simulation.

    The one place a `SlotValue` is constructed, so both entry points below --
    and any caller holding a lift from somewhere else -- go through the same
    formula and the same None propagation.
    """
    return SlotValue(
        label=label,
        assumptions=assumptions,
        lift=None if lift is None else float(lift),
        incremental_units_per_store_week=incremental_units_per_store_week(lift, assumptions),
        value=value_of_lift(lift, assumptions),
        creative_id=creative_id,
        brand=brand,
        ad_slot_ids=tuple(ad_slot_ids),
        n_synth=n_synth,
        seed=seed,
        variant_id=variant_id,
        sim_run_id=sim_run_id,
        lift_spread=lift_spread,
        value_spread=_price_spread(lift_spread, assumptions),
        rank=rank,
        is_current=is_current,
    )


def price_placement(
    base: Mapping[str, Any],
    *,
    patches: Sequence[Mapping[str, Any]] = (),
    creative_id: str,
    assumptions: Assumptions,
    label: Optional[str] = None,
    n_synth: int = DEFAULT_N_SYNTH,
    seed: int = DEFAULT_SEED,
    simulate: Callable[..., Any] = simcache.population,
) -> SlotValue:
    """Simulate one configuration and price the creative's lift in it.

    The chain, all of it borrowed:

      1. `api.app.resolve.resolve(base, patches)` -- the configuration.
      2. `simulate(resolved, variant_id, n_synth=..., seed=...)` -- by default
         `api.app.simcache.population`, the same cached call a prediction lock
         and an optimizer row are built from, at `analytics/optimizer.py`'s
         `DEFAULT_N_SYNTH` and `DEFAULT_SEED`.
      3. `analytics.lift.synth_lift` -- the project's one lift formula.
      4. `value_of_lift` -- the only new arithmetic in this module.

    `label` defaults to the creative and the ad slots it actually hangs on in
    the RESOLVED planogram, so a printed row describes what was simulated
    rather than what was asked for.

    Returns a `SlotValue` whose `value` is None when the lift is undefined --
    for instance when the patches leave the creative unplaced, so no shopper is
    ad-exposed and there is no exposed arm to compare.
    """
    resolved = resolve(base, {
        "variant_id": f"slotvalue_{creative_id}",
        "base_planogram_id": base["planogram_id"],
        "name": label or f"slot value for {creative_id}",
        "patches": [dict(patch) for patch in patches],
    })
    variant_id = variant_id_for(base, patches)
    bundle = simulate(resolved, variant_id, n_synth=n_synth, seed=seed)

    brand = creative_brand(resolved, creative_id)
    showing = ad_slots_showing(resolved, creative_id)
    lift = synth_lift(bundle.population, brand_of_sku=sku_brands(resolved), brand=brand)

    if label is None:
        label = (f"{creative_id} on {', '.join(showing)}" if showing
                 else f"{creative_id} unplaced (no ad slot carries it)")

    return price_from_lift(
        lift, assumptions, label=label, creative_id=creative_id, brand=brand,
        ad_slot_ids=showing, n_synth=int(n_synth), seed=int(seed), variant_id=variant_id,
        sim_run_id=str(bundle.population["sim_run_id"]),
    )


def price_ranking(
    ranking: Ranking,
    *,
    creative_id: str,
    assumptions: Assumptions,
    planogram: Optional[Mapping[str, Any]] = None,
) -> tuple[SlotValue, ...]:
    """Price every row of an S24 `Ranking`, in rank order. No re-simulation.

    The ranking's objectives ARE the lifts, so this reads them rather than
    running anything again, and each row's `SeedSpread` is priced with the same
    assumptions.

    **The ranking must have been made on this creative's ad-to-purchase lift.**
    A ranking on `sku_purchase_share_objective` carries shares, and multiplying
    a share by a baseline unit volume would produce a confident, meaningless
    number. The check compares `ranking.objective_name` against the name
    `ad_purchase_lift_objective(creative_id)` itself produces, so the two
    cannot drift apart silently.

    `planogram` is optional and is used for one thing: naming the brand the
    incremental units belong to. A `Ranking` does not carry the planogram, and
    `Assumptions.baseline_brand_units_per_store_week` and `margin_per_unit` are
    both per-unit-of-that-brand -- so pass it and every printed row says which
    product the money is about. Left out, `SlotValue.brand` is None and the
    summary simply does not claim one.
    """
    expected = ad_purchase_lift_objective(creative_id).name
    if ranking.objective_name != expected:
        raise ValueError(
            f"ranking was made on objective {ranking.objective_name!r}, not {expected!r}; "
            "only that creative's ad-to-purchase lift can be priced as incremental units"
        )

    brand = None if planogram is None else creative_brand(planogram, creative_id)

    return tuple(
        price_from_lift(
            entry.objective, assumptions,
            label=entry.candidate.label,
            lift_spread=entry.seed_spread,
            creative_id=creative_id,
            brand=brand,
            ad_slot_ids=tuple(
                slot for slot in (entry.candidate.detail.get("ad_slot_id"),) if slot
            ),
            n_synth=ranking.n_synth,
            seed=ranking.seed,
            variant_id=entry.variant_id,
            sim_run_id=entry.sim_run_id,
            rank=entry.rank,
            is_current=entry.is_current,
        )
        for entry in ranking.entries
    )


# ---------------------------------------------------------------------------
# Printed output -- every sentence traceable to what was assumed
# ---------------------------------------------------------------------------


def assumptions_block(assumptions: Assumptions) -> str:
    """The four commercial numbers on their own, labelled as assumptions.

    Print this next to any table of values. On its own a column of money says
    nothing about which numbers were asserted to produce it.
    """
    return "\n".join([
        "Assumed commercial inputs (supplied by the caller; NOT measured by this project -- "
        "nothing in schemas/, data/ or a SimResult carries margin or unit volume):",
        f"  baseline volume : {_units(assumptions.baseline_brand_units_per_store_week)} units "
        "per store-week, advertised brand, without the ad exposure",
        f"  margin          : {_money(assumptions.margin_per_unit)} {assumptions.currency} "
        "per unit",
        f"  footprint       : {assumptions.n_stores} stores x {assumptions.n_weeks} weeks "
        f"= {_units(assumptions.store_weeks)} store-weeks",
        f"  basis           : {assumptions.basis}",
    ])


def _assumed_line(assumptions: Assumptions) -> str:
    return (
        f"Assumed (not measured): baseline {_units(assumptions.baseline_brand_units_per_store_week)}"
        f" units per store-week; margin {_money(assumptions.margin_per_unit)} "
        f"{assumptions.currency} per unit; {assumptions.n_stores} stores; "
        f"{assumptions.n_weeks} weeks = {_units(assumptions.store_weeks)} store-weeks."
    )


def summary(priced: SlotValue) -> str:
    """One placement's value in words, with its provenance attached.

    Four things every summary carries, because a money figure quoted without
    them is not a result:

      * the value, its currency and the period it covers, plus the
        per-store-week rate PLAN section 6's left-hand side names;
      * the MEASURED input on its own line -- the ad-to-purchase lift, and the
        simulation it came from;
      * the ASSUMED inputs on their own line, plus the stated basis;
      * that this is a point estimate at one seed whose underlying lift is not
        resolved (docs/METHODOLOGY.md section 12.13).

    A seed spread is printed when there is one, always with the disclaimer
    that it is Monte Carlo run-to-run variability and not a confidence
    interval. **No confidence interval is ever printed** -- section 12.7 gives
    the reason a committed SimResult cannot support one.
    """
    assumptions = priced.assumptions
    lines: list[str] = []

    if priced.value is None:
        lines.append(
            f"{priced.label}: value undefined. The {MEASURED_METRIC} has no answer for this "
            "configuration (no ad-exposed arm, no unexposed arm, or an unexposed arm that "
            "bought none of the advertised brand), so there is no incremental volume to "
            "price. Undefined is not zero."
        )
    else:
        lines.append(
            f"{priced.label}: {_money(priced.value)} {assumptions.currency} over "
            f"{_units(assumptions.store_weeks)} store-weeks "
            f"({assumptions.n_stores} stores x {assumptions.n_weeks} weeks), "
            f"= {_money(priced.value_per_store_week)} {assumptions.currency} per store-week."
        )

    measured = f"Measured: {MEASURED_METRIC} "
    measured += "undefined" if priced.lift is None else f"{priced.lift:+.1%}"
    if priced.brand:
        measured += f" for brand {priced.brand}"
    if priced.n_synth is not None and priced.seed is not None:
        measured += f" ({priced.n_synth:,} synthetic shoppers, seed {priced.seed})"
    lines.append(measured + ".")

    if priced.incremental_units_per_store_week is not None:
        lines.append(
            f"Derived: {_units(priced.incremental_units_per_store_week)} incremental units per "
            f"store-week x {_money(assumptions.margin_per_unit)} {assumptions.currency} per unit "
            f"x {_units(assumptions.store_weeks)} store-weeks."
        )

    lines.append(_assumed_line(assumptions))
    lines.append(f"Basis: {assumptions.basis}")

    if priced.value_spread is not None:
        spread = priced.value_spread
        lines.append(
            f"Seed spread {_money(spread.low)} to {_money(spread.high)} "
            f"{assumptions.currency} over seeds "
            f"{', '.join(str(s) for s in spread.seeds)} -- Monte Carlo run-to-run variability "
            "at a fixed panel size, not a confidence interval."
        )

    at_seed = "at one seed" if priced.seed is None else f"at seed {priced.seed}"
    lines.append(
        f"This is a point estimate {at_seed}. The ordering the underlying lift comes from is "
        "not resolved at this panel size -- re-rolling the simulation seed moves the top pick "
        "and the current placement into each other's range "
        "(docs/METHODOLOGY.md section 12.13) -- and the value moves with it."
    )

    return "\n".join(lines)


def table(rows: Sequence[SlotValue]) -> str:
    """The priced rows as aligned text, in the order given.

    The header names the currency and the period once, so no cell is a bare
    number. An undefined row prints "undefined" rather than a zero, and the
    footer repeats that the money column rests on assumptions.
    """
    if not rows:
        return "No placements priced."

    assumptions = rows[0].assumptions
    width = max(len(row.label) for row in rows)
    header = (f"{'#':>3}  {'placement':<{width}}  {MEASURED_METRIC:>21}  "
              f"value over {_units(assumptions.store_weeks)} store-weeks ({assumptions.currency})")
    lines = [header, "-" * len(header)]

    for i, row in enumerate(rows, start=1):
        rank = row.rank if row.rank is not None else i
        lift = "undefined" if row.lift is None else f"{row.lift:+.1%}"
        value = "undefined" if row.value is None else _money(row.value)
        marker = "  <- current" if row.is_current else ""
        lines.append(f"{rank:>3}  {row.label:<{width}}  {lift:>21}  {value:>16}{marker}")

    lines.append("")
    lines.append("The lift column is measured (simulated). The value column is that lift times "
                 "the assumed inputs below; it is not a measurement.")
    lines.append(_assumed_line(assumptions))
    return "\n".join(lines)


def _money(amount: float) -> str:
    """Two decimal places and thousands separators. The currency is printed by
    the caller -- there is no default currency here either."""
    return f"{amount:,.2f}"


def _units(count: float) -> str:
    """A unit or period count: whole numbers stay whole, so "120 units" does not
    print as "120.00 units" and get mistaken for money."""
    value = float(count)
    if value == int(value):
        return f"{int(value):,}"
    return f"{value:,.2f}"
