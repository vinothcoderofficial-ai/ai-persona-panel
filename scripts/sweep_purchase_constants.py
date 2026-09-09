"""How far the headline ad-to-purchase lift moves when the purchase model's
constants move.

Why this exists
---------------
The Ad-to-Purchase Lift is the number this project puts first (PLAN section 9's
never-drop list), and RESULTS.md reports it as a point estimate: browser 0.32 on
variant `A`. That number is produced by about fifteen lines of
`sim/simulator.py` -- the `for rank in range(...)` block that scores the top-N
fixated goal SKUs -- and every constant in those lines was WRITTEN, not fitted.
`docs/SPEC.md` M4 mandates them, but SPEC M4 is a design document we authored;
no shopper data was ever regressed to produce a Gumbel scale of 0.1. There is no
test anywhere in the repository that pins any of them, because there is nothing
to pin them against.

So the honest question a judge will ask -- "how much of that 0.32 is the ad, and
how much is your choice of 0.1?" -- had no answer in this repository. This
script is that answer. It re-runs the committed planogram, the committed
variants and the committed persona policies across a defensible range of each
purchase constant it can reach, and prints what the headline metrics do. It
fits nothing, tunes nothing and recommends no value: the committed constants
stay committed. It only makes the dependence visible.

`docs/SENSITIVITY.md` is this script's stdout plus a reading of it, and carries
the exact command at the top.

What can be swept from outside `sim/simulator.py`, and what cannot
------------------------------------------------------------------
Two of the constants are module globals read at call time inside `run()`
(`PURCHASE_GUMBEL_SCALE` at the `rng.gumbel` call, `MAX_PURCHASE_CANDIDATES` at
the `for rank in range(...)` header), so setting the module attribute and
putting it back is enough; `_patched` does exactly that and restores in a
`finally`, and `scripts/tests/test_sweep.py` pins that it restores.

The ad term's coefficient is a literal inside the function:

    ad_pull = 0.2 * ad_receptivity * brand_seen[rows_here, store.sku_brand[sku]]

`ad_receptivity` is read from the policy and appears NOWHERE ELSE in
`sim/simulator.py` -- not in the relevance blend, not in the station choice.
Multiplying the policy field by `m` is therefore arithmetically identical to
multiplying that `0.2` by `m`: the sweep reports the product `0.2 x
ad_receptivity` as one effective coefficient, which is the only form in which
the model can distinguish it anyway. `test_sweep.py` pins the invariance the
claim rests on (changing `ad_receptivity` leaves `fixation_prob` untouched).

`purchase_threshold` is not a constant of the code at all -- it is a per-persona
policy field, produced once by an LLM at temperature 0 from the prompt "set
purchase_threshold so a neutral shopper converts near {baseline_conv}". Nothing
measured it either, and a second call to a different model would produce a
different number, so it is swept as an additive offset over every persona.

The three utility WEIGHTS -- `0.4 * brand_affinity`, `0.25 * (1 - price_norm) *
price_sensitivity`, `0.15 * promo * promo_sensitivity` -- are literals that
cannot be reached without editing `sim/simulator.py`, and the policy fields they
multiply also feed the attention layer, so scaling those is not the same
experiment. They are NOT swept here. That is a gap, and `docs/SENSITIVITY.md`
says so in those words rather than letting the table imply full coverage.

Reading the output
------------------
Every cell is the mean over `--seeds`. A sweep that reported one seed could not
tell a constant's effect from Monte Carlo noise, and at 10,000 shoppers the
noise is not small: the browser lift moves by more than 0.1 between seeds with
every constant held still. The "Monte Carlo yardstick" table is that noise
measured at the committed constants, and no movement in any other table means
anything until it is compared against it.
"""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import json
import pathlib
import statistics
import sys
import time
from typing import Any, Callable, Iterator, Mapping, Sequence

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analytics.lift import (  # noqa: E402
    POPULATION_KEY,
    ad_slots_showing,
    creative_brand,
    sku_brands,
    synth_lift,
)
from api.app import simcache  # noqa: E402
from api.app.resolve import resolve  # noqa: E402
# The focal SKU and creative come from scripts/eval.py rather than being
# restated here: a sweep of a DIFFERENT creative than the one RESULTS.md
# headlines would be worse than no sweep at all.
from scripts.eval import FOCAL_CREATIVE, FOCAL_SKU  # noqa: E402
from sim import simulator  # noqa: E402

PLANOGRAMS_DIR = ROOT / "data" / "planograms"
VARIANTS_DIR = ROOT / "data" / "variants"
BASE_PLANOGRAM_ID = "demo_aisle"

DEFAULT_VARIANT = "A"
DEFAULT_N_SYNTH = 10_000

# Seed 42 first, and deliberately: it is `api/app/prediction.py`'s seed, the one
# every committed prediction lock and every number in RESULTS.md was run at. It
# has no special status here beyond being the one a reader can look up.
DEFAULT_SEEDS = (42, 43, 44, 45, 46)


# ---------------------------------------------------------------------------
# one point in constant-space
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Config:
    """One assignment of the purchase model's free constants.

    `ad_pull_multiplier` and `threshold_offset` are relative rather than
    absolute because they do not have one value to be absolute about:
    `ad_receptivity` and `purchase_threshold` differ per persona, and the
    experiment worth running is "every persona's, scaled together".
    """

    gumbel_scale: float
    max_candidates: int
    ad_pull_multiplier: float
    threshold_offset: float

    @classmethod
    def committed(cls) -> "Config":
        """The constants as `sim/simulator.py` and the cached policies hold them.

        Read from the module, never copied into a literal here. A second copy of
        `PURCHASE_GUMBEL_SCALE` in this file would go stale the day someone
        changed the first one, and the sweep would quietly stop containing the
        value the project actually ships.
        """
        return cls(
            gumbel_scale=float(simulator.PURCHASE_GUMBEL_SCALE),
            max_candidates=int(simulator.MAX_PURCHASE_CANDIDATES),
            ad_pull_multiplier=1.0,
            threshold_offset=0.0,
        )


@contextlib.contextmanager
def _patched(config: Config) -> Iterator[None]:
    """Hold `sim/simulator.py`'s two module globals at `config` for one block.

    `run()` reads both by name every call, so rebinding the module attribute is
    the whole mechanism. The `finally` is the load-bearing part: this script
    imports the same module object the API and `scripts/eval.py` import, and a
    sweep that left `PURCHASE_GUMBEL_SCALE` at 0.4 behind would silently change
    every later run in the same process -- including, in a test session, other
    people's tests.
    """
    saved = (simulator.PURCHASE_GUMBEL_SCALE, simulator.MAX_PURCHASE_CANDIDATES)
    simulator.PURCHASE_GUMBEL_SCALE = float(config.gumbel_scale)
    simulator.MAX_PURCHASE_CANDIDATES = int(config.max_candidates)
    try:
        yield
    finally:
        simulator.PURCHASE_GUMBEL_SCALE, simulator.MAX_PURCHASE_CANDIDATES = saved


def adjust_policy(policy: Mapping[str, Any], config: Config) -> dict[str, Any]:
    """A copy of one persona policy with the two policy-side constants moved.

    A copy, never an in-place edit: `simcache.load_policy` caches the loaded
    document for the life of the process and hands out the same dict to every
    caller, so mutating it here would corrupt every subsequent config in the
    sweep and anything else in the process that asked for a policy.
    """
    moved = dict(policy)
    moved["ad_receptivity"] = float(policy["ad_receptivity"]) * config.ad_pull_multiplier
    moved["purchase_threshold"] = float(policy["purchase_threshold"]) + config.threshold_offset
    return moved


# ---------------------------------------------------------------------------
# the dimensions
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Dimension:
    """One constant, the range it is swept over, and what it is called."""

    key: str                       # --only name
    field: str                     # the Config field it varies
    constant: str                  # what it is called in the code
    location: str                  # where a reader finds it
    spec: str                      # what docs/SPEC.md M4 says about it
    values: tuple                  # what this sweep tries
    label: str                     # column heading for the value
    show: Callable[[Any], str]     # how one value prints

    def configs(self, baseline: Config) -> list[tuple[Any, Config]]:
        return [(value, dataclasses.replace(baseline, **{self.field: value}))
                for value in self.values]


DIMENSIONS: tuple[Dimension, ...] = (
    Dimension(
        key="gumbel-scale",
        field="gumbel_scale",
        constant="PURCHASE_GUMBEL_SCALE",
        location="sim/simulator.py, module level",
        spec="mandated: SPEC M4 writes the utility's noise term as Gumbel(0, 0.1)",
        # A Gumbel scale is a taste parameter of a discrete-choice model. 0.05
        # is a near-deterministic shopper; 0.4 is one whose choice is mostly
        # noise. The committed 0.1 sits at the low end of that, and the range
        # is the band a discrete-choice modeller would call arguable.
        values=(0.05, 0.10, 0.15, 0.25, 0.40),
        label="scale",
        show=lambda v: f"{v:.2f}",
    ),
    Dimension(
        key="max-candidates",
        field="max_candidates",
        constant="MAX_PURCHASE_CANDIDATES",
        location="sim/simulator.py, module level",
        spec="mandated: SPEC M4 says 'for top-2 fixated SKUs matching a goal category'",
        # How many of a bay's fixated goal SKUs get a purchase roll. 1 is
        # "consider only the most-looked-at pack"; 4 is a shopper who weighs
        # most of what they saw. Nothing measured 2.
        values=(1, 2, 3, 4),
        label="candidates",
        show=lambda v: f"{v:d}",
    ),
    Dimension(
        key="ad-pull",
        field="ad_pull_multiplier",
        constant="the 0.2 in `ad_pull = 0.2 * ad_receptivity * brand_seen`",
        location="sim/simulator.py, inside run().visit()",
        spec="mandated: SPEC M4 writes `+ 0.2*ad_exposure*ad_receptivity*(ad.brand == sku.brand)`",
        # Swept via the policy field, which is arithmetically the same thing --
        # see the module docstring. 0.0 is the informative end: it is the model
        # with the ad's purchase effect switched off entirely, so whatever lift
        # survives there is the SELECTION in the exposed/unexposed split rather
        # than anything the ad did.
        values=(0.0, 0.5, 1.0, 1.5, 2.0),
        label="0.2 x mult",
        show=lambda v: f"{0.2 * v:.2f}",
    ),
    Dimension(
        key="threshold",
        field="threshold_offset",
        constant="purchase_threshold (per-persona policy field)",
        location="data/cache/policies/*.json",
        spec="free: SPEC M4 asks the LLM for it; no value is mandated and none was fitted",
        # +/- 0.1 on a threshold the personas hold between 0.25 and 0.45. This
        # is the width of the disagreement you would expect between two LLM
        # calls answering the same prompt, which is the only process that has
        # ever produced this number.
        values=(-0.10, -0.05, 0.0, 0.05, 0.10),
        label="offset",
        show=lambda v: f"{v:+.2f}",
    ),
)

DIMENSION_KEYS = tuple(d.key for d in DIMENSIONS)


# ---------------------------------------------------------------------------
# measurement
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Metric:
    """One reported column: how to pull it out of a run, and how to print it."""

    key: str
    heading: str
    digits: int


@dataclasses.dataclass(frozen=True)
class Cell:
    """One metric at one config, summarised over the seeds.

    `undefined` counts the seeds where the lift had no value at all -- an empty
    arm or a zero unexposed share, which `analytics/lift.py` returns None for
    and which must never be averaged in as 0.0. The count is printed rather
    than hidden, because "two of five seeds could not answer" is the single
    most important thing to know about a row.
    """

    mean: float | None
    low: float | None
    high: float | None
    undefined: int
    seeds: int


class Sweeper:
    """Runs configs against one resolved variant, caching by (config, seed).

    The store -- saliency, slot geometry, the whole attention layer -- depends
    on the planogram and not on any purchase constant, so it is built once. The
    cache matters because the committed config appears in every dimension's
    table and would otherwise be simulated four times over.
    """

    def __init__(self, *, variant_id: str, n_synth: int, seeds: Sequence[int],
                 progress=None) -> None:
        base = json.loads(
            (PLANOGRAMS_DIR / f"{BASE_PLANOGRAM_ID}.json").read_text(encoding="utf-8")
        )
        variant = json.loads(
            (VARIANTS_DIR / f"{variant_id}.json").read_text(encoding="utf-8")
        )
        self.planogram = resolve(base, variant)
        self.variant_id = variant_id
        self.n_synth = int(n_synth)
        self.seeds = tuple(int(s) for s in seeds)
        self.progress = progress

        self.store = simulator.build_store(self.planogram)
        self.personas = simcache.load_personas()
        self.policies = {
            persona["persona_id"]: simcache.load_policy(
                persona["persona_id"], self.planogram["planogram_id"]
            )
            for persona in self.personas
        }
        self.brand_of_sku = sku_brands(self.planogram)
        self.brand = creative_brand(self.planogram, FOCAL_CREATIVE)
        self.ad_slots = ad_slots_showing(self.planogram, FOCAL_CREATIVE)
        if not self.ad_slots:
            raise SystemExit(
                f"error: variant {variant_id!r} shows no {FOCAL_CREATIVE!r} creative, so it has "
                "no ad-to-purchase lift to be sensitive about"
            )

        self.metrics: tuple[Metric, ...] = tuple(
            [Metric(f"lift:{p['persona_id']}", p["persona_id"], 3) for p in self.personas]
            + [
                Metric(f"lift:{POPULATION_KEY}", POPULATION_KEY, 3),
                Metric(f"share:{FOCAL_SKU}", f"{FOCAL_SKU} share", 4),
                Metric(f"attention:{FOCAL_CREATIVE}", f"{FOCAL_CREATIVE} attention", 4),
            ]
        )
        self._cache: dict[tuple[Config, int], dict[str, float | None]] = {}
        self.runs = 0

    def one(self, config: Config, seed: int) -> dict[str, float | None]:
        """Every reported metric for one config at one seed."""
        cached = self._cache.get((config, seed))
        if cached is not None:
            return cached

        results: list[dict] = []
        shares: list[float] = []
        values: dict[str, float | None] = {}
        with _patched(config):
            for persona in self.personas:
                persona_id = persona["persona_id"]
                policy = adjust_policy(self.policies[persona_id], config)
                result = simulator.run(
                    self.store, policy, n_runs=self.n_synth, seed=seed,
                    variant_id=self.variant_id, archetype=persona["archetype"],
                )
                results.append(result)
                shares.append(float(persona["share_of_population"]))
                values[f"lift:{persona_id}"] = synth_lift(
                    result, brand_of_sku=self.brand_of_sku, brand=self.brand
                )

        population = simulator.combine(results, shares)
        values[f"lift:{POPULATION_KEY}"] = synth_lift(
            population, brand_of_sku=self.brand_of_sku, brand=self.brand
        )
        values[f"share:{FOCAL_SKU}"] = float(population["purchase_share"].get(FOCAL_SKU, 0.0))
        # Mean over the slots showing the creative. Variant A shows it in one
        # place, so this is that slot; with several it is their average
        # attention and not the probability of seeing the creative at all,
        # which no SimResult field can answer.
        values[f"attention:{FOCAL_CREATIVE}"] = statistics.fmean(
            float(population["ad_slot_attention"].get(slot, 0.0)) for slot in self.ad_slots
        )

        self.runs += 1
        if self.progress is not None:
            self.progress(config, seed, self.runs)
        self._cache[(config, seed)] = values
        return values

    def column(self, config: Config) -> dict[str, Cell]:
        """Every metric at one config, summarised over every seed."""
        per_seed = [self.one(config, seed) for seed in self.seeds]
        summary: dict[str, Cell] = {}
        for metric in self.metrics:
            got = [row[metric.key] for row in per_seed]
            defined = [float(v) for v in got if v is not None]
            summary[metric.key] = Cell(
                mean=statistics.fmean(defined) if defined else None,
                low=min(defined) if defined else None,
                high=max(defined) if defined else None,
                undefined=len(got) - len(defined),
                seeds=len(got),
            )
        return summary


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def _table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    """A GitHub-flavoured markdown table, column-aligned so stdout reads too."""
    widths = [len(h) for h in header]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    lines = ["| " + " | ".join(h.ljust(widths[i]) for i, h in enumerate(header)) + " |",
             "|" + "|".join("-" * (widths[i] + 2) for i in range(len(header))) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)) + " |")
    return "\n".join(lines)


def _number(value: float | None, digits: int) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def _cell(cell: Cell, digits: int) -> str:
    text = _number(cell.mean, digits)
    if cell.undefined:
        text += f" ({cell.undefined}/{cell.seeds} undefined)"
    return text


def render(sweeper: Sweeper, dimensions: Sequence[Dimension], *, command: str) -> str:
    """The whole report, as one markdown string.

    Deterministic by construction: fixed seeds, fixed iteration order, fixed
    float formatting. `scripts/tests/test_sweep.py` pins that two calls with the
    same arguments render byte-identical text, because a table that shifted in
    the last decimal between runs could not be committed to a doc.
    """
    baseline = Config.committed()
    parts: list[str] = []

    parts.append(f"Regenerated by:\n\n    {command}\n")
    parts.append(
        f"Variant `{sweeper.variant_id}`, {sweeper.n_synth:,} shoppers x "
        f"{len(sweeper.personas)} personas, seeds "
        f"{', '.join(str(s) for s in sweeper.seeds)}. Committed planogram, committed variants, "
        f"committed persona policies. Lift is the within-run ad-to-purchase lift for "
        f"`{FOCAL_CREATIVE}`'s brand (`{sweeper.brand}`), the same "
        f"`analytics/lift.py:synth_lift` RESULTS.md calls.\n"
    )

    parts.append("## The constants being swept\n")
    parts.append(_table(
        ["constant", "where", "committed", "swept over", "status in SPEC M4"],
        [[d.constant, d.location,
          d.show(getattr(baseline, d.field)),
          ", ".join(d.show(v) for v in d.values),
          d.spec] for d in dimensions],
    ))

    headings = [m.heading for m in sweeper.metrics]

    parts.append("\n## Monte Carlo yardstick: the committed constants, one seed to the next\n")
    parts.append(
        "Nothing below this table means anything unless it is wider than this table. Same "
        "constants, same planogram, only the seed changes.\n"
    )
    reference = sweeper.column(baseline)
    parts.append(_table(
        ["metric", "mean over seeds", f"seed {sweeper.seeds[0]}", "min", "max", "spread"],
        [[m.heading,
          _number(reference[m.key].mean, m.digits),
          _number(sweeper.one(baseline, sweeper.seeds[0])[m.key], m.digits),
          _number(reference[m.key].low, m.digits),
          _number(reference[m.key].high, m.digits),
          _number(None if reference[m.key].high is None
                  else reference[m.key].high - reference[m.key].low, m.digits)]
         for m in sweeper.metrics],
    ))

    extremes: dict[str, list[tuple[float, str]]] = {m.key: [] for m in sweeper.metrics}
    for dimension in dimensions:
        parts.append(f"\n## {dimension.constant}\n")
        rows = []
        for value, config in dimension.configs(baseline):
            column = sweeper.column(config)
            marker = "  <- committed" if config == baseline else ""
            rows.append([dimension.show(value) + marker]
                        + [_cell(column[m.key], m.digits) for m in sweeper.metrics])
            for metric in sweeper.metrics:
                if column[metric.key].mean is not None:
                    extremes[metric.key].append(
                        (column[metric.key].mean, f"{dimension.key} = {dimension.show(value)}")
                    )
        parts.append(_table([dimension.label] + headings, rows))

    parts.append("\n## Range summary: every metric across the whole sweep\n")
    summary_rows = []
    for metric in sweeper.metrics:
        found = extremes[metric.key]
        if not found:
            summary_rows.append([metric.heading, "n/a", "n/a", "n/a", "n/a"])
            continue
        low = min(found)
        high = max(found)
        summary_rows.append([
            metric.heading,
            _number(reference[metric.key].mean, metric.digits),
            f"{_number(low[0], metric.digits)} ({low[1]})",
            f"{_number(high[0], metric.digits)} ({high[1]})",
            _number(high[0] - low[0], metric.digits),
        ])
    parts.append(_table(
        ["metric", "at committed constants", "sweep low", "sweep high", "width"], summary_rows
    ))

    parts.append("\n" + headline(sweeper, dimensions) + "\n")
    return "\n".join(parts)


def headline(sweeper: Sweeper, dimensions: Sequence[Dimension]) -> str:
    """One sentence naming the headline metric's range and its widest driver.

    Computed from the sweep, never written by hand: a sentence typed next to a
    table is a sentence that stops matching it.
    """
    baseline = Config.committed()
    key = "lift:browser" if any(m.key == "lift:browser" for m in sweeper.metrics) else \
        f"lift:{POPULATION_KEY}"
    reference = sweeper.column(baseline)[key]

    widest: tuple[float, str, float, float] | None = None
    overall: list[float] = []
    for dimension in dimensions:
        means = [sweeper.column(config)[key].mean for _, config in dimension.configs(baseline)]
        means = [m for m in means if m is not None]
        if not means:
            continue
        overall.extend(means)
        width = max(means) - min(means)
        if widest is None or width > widest[0]:
            widest = (width, dimension.constant, min(means), max(means))

    if widest is None or not overall:
        return "**Headline:** every configuration left the lift undefined; there is no range."
    return (
        f"**Headline.** The `{key.split(':', 1)[1]}` ad-to-purchase lift is "
        f"{reference.mean:.3f} at the committed constants (and {reference.low:.3f} to "
        f"{reference.high:.3f} across seeds with those constants held still). Across this "
        f"sweep it runs {min(overall):.3f} to {max(overall):.3f}. The widest single driver is "
        f"{widest[1]}, which alone moves it {widest[2]:.3f} to {widest[3]:.3f}."
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--variant", default=DEFAULT_VARIANT,
                        help="variant to sweep on (default: %(default)s, the one RESULTS.md "
                             "headlines)")
    parser.add_argument("--n-synth", type=int, default=DEFAULT_N_SYNTH,
                        help="shoppers per persona per run (default: %(default)s)")
    parser.add_argument("--seeds", type=int, nargs="+", default=list(DEFAULT_SEEDS),
                        help="seeds to average each cell over (default: %(default)s)")
    parser.add_argument("--only", choices=DIMENSION_KEYS, action="append", default=None,
                        help="sweep only this constant; repeatable (default: all of them)")
    parser.add_argument("--quiet", action="store_true",
                        help="no progress lines on stderr")
    return parser


def chosen_dimensions(only: Sequence[str] | None) -> tuple[Dimension, ...]:
    if not only:
        return DIMENSIONS
    wanted = list(dict.fromkeys(only))
    return tuple(d for d in DIMENSIONS if d.key in wanted)


def command_line(args: argparse.Namespace) -> str:
    """The command that regenerates this report, printed at the top of it."""
    parts = [".venv/Scripts/python.exe scripts/sweep_purchase_constants.py"]
    if args.variant != DEFAULT_VARIANT:
        parts += ["--variant", args.variant]
    if args.n_synth != DEFAULT_N_SYNTH:
        parts += ["--n-synth", str(args.n_synth)]
    if tuple(args.seeds) != DEFAULT_SEEDS:
        parts += ["--seeds"] + [str(s) for s in args.seeds]
    for key in dict.fromkeys(args.only or ()):
        parts += ["--only", key]
    return " ".join(parts)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    dimensions = chosen_dimensions(args.only)
    started = time.perf_counter()

    def progress(config: Config, seed: int, runs: int) -> None:
        print(f"  run {runs:3d}  seed {seed}  {config}", file=sys.stderr)

    sweeper = Sweeper(
        variant_id=args.variant, n_synth=args.n_synth, seeds=args.seeds,
        progress=None if args.quiet else progress,
    )
    text = render(sweeper, dimensions, command=command_line(args))
    print(text)
    if not args.quiet:
        print(f"  {sweeper.runs} simulations in {time.perf_counter() - started:.1f} s",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
