"""Print the placement ranking for one creative, optionally priced.

The recording entry point for the optimizer shot (PLAN §6, Day 9 PM). S24 and
S25 are libraries with no route and no report row; without something to run,
"optimizer recommendation" is not a recordable shot. This is that something
and nothing more -- it composes `analytics/optimizer.py` and
`analytics/slot_value.py` and prints what they return. No maths lives here.

    python scripts/optimize.py --creative AD_1
    python scripts/optimize.py --creative AD_1 --focal-sku SKU_008

Pricing is opt-in and all-or-nothing. `analytics/slot_value.py` deliberately
gives its `Assumptions` no defaults, because margin and store traffic exist
nowhere in this repository and inventing them is the one thing this project
must not do. This script keeps that property: pass every commercial flag or
none, and passing some but not all is an error rather than a quiet fill-in.

    python scripts/optimize.py --creative AD_1 \
        --baseline-units 120 --margin-per-unit 7.5 --stores 4 --weeks 13 \
        --currency INR --basis "ILLUSTRATIVE ONLY -- round figures"
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--creative", default="AD_1", help="creative to optimise for")
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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    assumptions = assumptions_from(args)

    base = json.loads(PLANOGRAM.read_text(encoding="utf-8"))
    variant = json.loads((VARIANTS / f"{args.variant}.json").read_text(encoding="utf-8"))
    resolved = resolve(base, variant)

    candidates = optimizer.ad_placement_candidates(resolved, creative_ids=[args.creative])
    if args.focal_sku:
        candidates = candidates + optimizer.sku_level_candidates(resolved, args.focal_sku)

    ranking = optimizer.rank_candidates(
        resolved,
        candidates,
        optimizer.ad_purchase_lift_objective(args.creative),
        n_synth=args.n_synth,
        seed=args.seed,
    )

    print(optimizer.summary(ranking))
    for entry in ranking.entries:
        value = ranking.format_value(entry.objective) if entry.objective is not None else "undefined"
        marker = "  <- current" if entry.is_current else ""
        print(f"  {entry.rank:2d}. {entry.candidate.label:<58} {value}{marker}")

    for skipped in ranking.skipped:
        print(f"  skipped: {skipped.label} -- {skipped.reason}")

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
