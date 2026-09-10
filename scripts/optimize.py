"""Print the placement ranking for one creative, optionally priced.

The recording entry point for the optimizer shot (PLAN §6, Day 9 PM). S24 and
S25 are libraries with no route and no report row; without something to run,
"optimizer recommendation" is not a recordable shot. This is that something
and nothing more -- it composes `analytics/optimizer.py` and
`analytics/slot_value.py` and prints what they return. No maths lives here.

    python scripts/optimize.py --creative AD_1
    python scripts/optimize.py --creative AD_1 --focal-sku SKU_008

Which estimator, and why the default moved
------------------------------------------
`analytics/lift.py` carries two Brand Lifts and docs/PHASE3.md P3.1 measured
them on this aisle: the WITHIN-run split (one run, divided by whether the
shopper fixated an ad slot) says +4.5 %, the BETWEEN-arm comparison (a treated
run against a run carrying no creative) says +0.9 %. Within-run "ad exposed" is
a selection -- those shoppers had already walked to the endcap -- not a
randomisation, so it overstates the ad by whatever the selection is worth.

This script ranked on the within-run one with no way to ask for the other.
`--objective` now names it and defaults to `between-arm-lift`, because a
default is what gets recorded and quoted:

    python scripts/optimize.py --creative AD_1 --objective within-run-lift
    python scripts/optimize.py --objective sku-purchase-share --focal-sku SKU_008

`sku-purchase-share` needs no creative at all, which makes it the only one of
the three that can rank a shelf whose advertising is unknown.

Pricing is opt-in and all-or-nothing. `analytics/slot_value.py` deliberately
gives its `Assumptions` no defaults, because margin and store traffic exist
nowhere in this repository and inventing them is the one thing this project
must not do. This script keeps that property: pass every commercial flag or
none, and passing some but not all is an error rather than a quiet fill-in.

    python scripts/optimize.py --creative AD_1 --objective within-run-lift \
        --baseline-units 120 --margin-per-unit 7.5 --stores 4 --weeks 13 \
        --currency INR --basis "ILLUSTRATIVE ONLY -- round figures"

Either lift can be priced, and every priced row names which one it was, because
the two differ several-fold on this aisle and money that does not say what is
under it is not checkable. The default `between-arm-lift` is the natural input:
incremental units are `baseline_units * lift`, and the between-arm number is
that quantity measured against a control run of the same shelf with the creative
taken down. The within-run split prices a contrast between two self-selected
halves of one population, so it overstates a store's incremental units by
whatever the selection is worth (docs/PHASE3.md P3.1).

A purchase share is not a lift and is refused here rather than crashing inside
slot_value or, worse, printing money against a ranking made on a different
metric.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analytics import optimizer, slot_value  # noqa: E402
from api.app.resolve import resolve  # noqa: E402

PLANOGRAM = ROOT / "data" / "planograms" / "demo_aisle.json"
VARIANTS = ROOT / "data" / "variants"

# Every flag that describes the caller's commercial situation rather than
# anything this project measured. All of them, or none.
COMMERCIAL_FLAGS = ("baseline_units", "margin_per_unit", "stores", "weeks", "currency", "basis")

# The three objectives, by the flag value that selects each. `between-arm-lift`
# is first and is the default: see the module docstring.
OBJECTIVES = ("between-arm-lift", "within-run-lift", "sku-purchase-share")
LIFT_OBJECTIVES = ("between-arm-lift", "within-run-lift")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--creative", default="AD_1", help="creative to optimise for")
    parser.add_argument("--objective", choices=OBJECTIVES, default=OBJECTIVES[0],
                        help="which metric to rank on; the two lifts differ several-fold")
    parser.add_argument("--variant", default="A", help="variant id to start from")
    parser.add_argument("--focal-sku", default=None,
                        help="also try this SKU at every shelf level")
    parser.add_argument("--n-synth", type=int, default=optimizer.DEFAULT_N_SYNTH)
    parser.add_argument("--seed", type=int, default=optimizer.DEFAULT_SEED)
    for flag in ("--baseline-units", "--margin-per-unit"):
        parser.add_argument(flag, type=float, default=None)
    for flag in ("--stores", "--weeks"):
        parser.add_argument(flag, type=int, default=None)
    parser.add_argument("--currency", default=None)
    parser.add_argument("--basis", default=None,
                        help="where the commercial numbers came from; printed verbatim")
    return parser


def assumptions_from(args: argparse.Namespace) -> slot_value.Assumptions | None:
    """The caller's commercial inputs, or None if they gave none.

    Raises SystemExit on a partial set: a half-specified price is worse than
    no price, because it looks like a result.
    """
    given = [name for name in COMMERCIAL_FLAGS if getattr(args, name) is not None]
    if not given:
        return None
    missing = [name for name in COMMERCIAL_FLAGS if getattr(args, name) is None]
    if missing:
        raise SystemExit(
            "error: pricing needs every commercial input or none. Missing: "
            + ", ".join("--" + name.replace("_", "-") for name in missing)
        )
    return slot_value.Assumptions(
        baseline_brand_units_per_store_week=args.baseline_units,
        margin_per_unit=args.margin_per_unit,
        n_stores=args.stores,
        n_weeks=args.weeks,
        currency=args.currency,
        basis=args.basis,
    )


def objective_from(args: argparse.Namespace):
    """The `Objective` named by `--objective`, or a SystemExit saying what is
    missing.

    `sku-purchase-share` ranks one product's share of purchases and there is no
    default product to fall back on, so asking for it without `--focal-sku` is
    an error rather than a quiet substitution -- the same rule the commercial
    flags follow, and for the same reason.
    """
    if args.objective == "between-arm-lift":
        return optimizer.between_arm_lift_objective(args.creative)
    if args.objective == "within-run-lift":
        return optimizer.ad_purchase_lift_objective(args.creative)

    if not args.focal_sku:
        raise SystemExit(
            "error: --objective sku-purchase-share ranks one product's share of "
            "purchases and needs --focal-sku; there is no default product"
        )
    return optimizer.sku_purchase_share_objective(args.focal_sku)


def check_pricing_is_possible(args: argparse.Namespace) -> None:
    """Refuse to price a ranking `analytics/slot_value.py` cannot price.

    `price_ranking` accepts only `ad_purchase_lift_objective(creative).name` and
    raises ValueError on anything else, deliberately: multiplying a purchase
    share by a baseline unit volume produces a confident, meaningless number.
    That guard is right, and it means the honest between-arm lift has no priced
    view in this repository yet.

    Letting it raise would print a traceback after several minutes of
    simulation. Saying so before any of that runs, and naming the one objective
    that can be priced, is the same refusal made useful.
    """
    if args.objective in ("between-arm-lift", "within-run-lift"):
        return
    raise SystemExit(
        f"error: --objective {args.objective} cannot be priced. Incremental units are "
        "baseline_units * lift, so the objective has to BE a lift: use between-arm-lift "
        "(the randomised comparison, and the natural input to this arithmetic) or "
        "within-run-lift (the selection-confounded one -- docs/PHASE3.md P3.1). A purchase "
        "share is not a lift, and multiplying one by a baseline unit volume produces a "
        "confident, meaningless number."
    )


def format_row(ranking, entry) -> str:
    """One ranked placement, carrying its own seed spread and its own verdict.

    The table used to print a rank, a label and a percentage, which sorted
    descending reads as certainty. Two things travel with the number now:

    * **the row's seed range**, where it has one -- `spread_top_n` bounds how
      many rows are re-scored, so most rows honestly have none and print `--`;
    * **UNRESOLVED**, when that range contains no effect at all. A between-arm
      lift running from -0.4 % to +0.7 % has not been shown to do anything,
      whatever it sorted above, and on this aisle at 10,000 shoppers that
      describes most of the space.
    """
    value = ranking.format_value(entry.objective) if entry.objective is not None else "undefined"
    spread = entry.seed_spread
    if spread is None:
        range_text = "spread --"
    else:
        range_text = (f"seeds {ranking.format_value(spread.low)}"
                      f"..{ranking.format_value(spread.high)}")
    verdict = "  UNRESOLVED" if entry.spread_clears_no_effect is False else ""
    marker = "  <- current" if entry.is_current else ""
    return f"  {entry.rank:2d}. {entry.candidate.label:<58} {value:>10}  {range_text}{verdict}{marker}"


def format_skipped(skipped) -> str:
    """One line for a candidate the planogram could not express.

    `Skipped` carries candidate_id / kind / reason / detail -- and no `label`.
    This printed `skipped.label`, which raised AttributeError the moment
    anything was skipped. Nothing caught it because the default candidate space
    on the committed aisle skips nothing, so the loop body never ran in any
    test or in any run I made.

    The reason is the part worth printing: a level silently missing from a
    ranking reads as "we tried it and it was bad", which is a different and far
    more useful-sounding claim than "there was no move to try".
    """
    return f"  skipped: {skipped.candidate_id} ({skipped.kind}) -- {skipped.reason}"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    assumptions = assumptions_from(args)
    objective = objective_from(args)
    if assumptions is not None:
        check_pricing_is_possible(args)

    base = json.loads(PLANOGRAM.read_text(encoding="utf-8"))
    variant = json.loads((VARIANTS / f"{args.variant}.json").read_text(encoding="utf-8"))
    resolved = resolve(base, variant)

    # Every creative, not just the one being scored. Moving AD_2 can displace
    # AD_1, so a search restricted to AD_1's own placements would hide the
    # candidate that makes the advertised brand worse. `--creative` selects the
    # objective; it does not narrow the search space.
    candidates = optimizer.ad_placement_candidates(resolved)
    if args.focal_sku:
        candidates = candidates + optimizer.sku_level_candidates(resolved, args.focal_sku)

    ranking = optimizer.rank_candidates(
        resolved,
        candidates,
        objective,
        n_synth=args.n_synth,
        seed=args.seed,
    )

    print(optimizer.summary(ranking))
    for entry in ranking.entries:
        print(format_row(ranking, entry))

    for skipped in ranking.skipped:
        print(format_skipped(skipped))

    if assumptions is not None:
        priced = slot_value.price_ranking(
            ranking, creative_id=args.creative, assumptions=assumptions
        )
        print()
        print(slot_value.assumptions_block(assumptions))
        print(slot_value.table(priced))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
