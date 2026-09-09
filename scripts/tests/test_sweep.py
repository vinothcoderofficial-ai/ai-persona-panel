"""Tests for scripts/sweep_purchase_constants.py, the purchase-model sensitivity sweep.

The sweep exists to say an uncomfortable thing about the headline number, and it
is only worth anything if two properties hold.

**It must be deterministic.** The output is pasted into `docs/SENSITIVITY.md`
and read as evidence. A table whose last decimal moved between two runs of the
same command would be worthless as evidence and impossible to review in a diff,
so `test_the_same_arguments_render_byte_identical_text` runs the whole thing
twice in one process and compares the strings.

**It must report a range and not a point.** The failure mode this whole track
guards against is a sweep that quietly collapses back to "the answer is 0.32":
one value reported, the dependence hidden again. So the headline sentence is
computed from the sweep's own extremes, and the tests below pin that it spans
them and names the constant responsible.

Two more properties are here because breaking them would corrupt work outside
this file. The sweep rebinds `sim.simulator`'s module globals -- the same module
object the API, `scripts/eval.py` and every other test import -- so it must put
them back even when a run raises, and it must never mutate the policy documents
`api/app/simcache.py` caches for the life of the process.

Most tests run at a small `n_synth` because they are pinning the machinery, not
the numbers. The one test that pins a number
(`test_the_committed_constants_still_reproduce_the_reported_headline`) runs at
the full 10,000 shoppers at seed 42, because that is the only configuration
whose value appears in RESULTS.md.
"""
import dataclasses
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts import sweep_purchase_constants as sweep  # noqa: E402
from sim import simulator  # noqa: E402

# Small enough to keep the suite quick, large enough that a persona still
# records purchases in both arms and the lift is defined.
FAST = dict(variant_id="A", n_synth=1_500, seeds=(42, 43))


@pytest.fixture
def fast_sweeper():
    return sweep.Sweeper(**FAST)


def test_the_committed_config_is_read_from_the_simulator_not_restated():
    """The baseline follows sim/simulator.py, so it cannot go stale.

    A literal 0.1 in the sweep would mean that the day someone changed
    `PURCHASE_GUMBEL_SCALE`, the table would still claim to have measured the
    shipped value -- and the row marked "committed" would be the wrong row.
    """
    committed = sweep.Config.committed()
    assert committed.gumbel_scale == simulator.PURCHASE_GUMBEL_SCALE
    assert committed.max_candidates == simulator.MAX_PURCHASE_CANDIDATES
    assert committed.ad_pull_multiplier == 1.0
    assert committed.threshold_offset == 0.0


def test_every_dimension_includes_the_committed_value():
    """A sweep that skipped the shipped value could not anchor anything.

    Each table has to contain the row a reader can check against RESULTS.md,
    or the reader has no way to tell the sweep is measuring the same model.
    """
    committed = sweep.Config.committed()
    for dimension in sweep.DIMENSIONS:
        configs = [config for _, config in dimension.configs(committed)]
        assert committed in configs, f"{dimension.key} does not sweep through the shipped value"


def test_the_simulator_globals_are_restored_even_when_the_body_raises():
    """`_patched` must not leak, because it patches a module everyone shares."""
    before = (simulator.PURCHASE_GUMBEL_SCALE, simulator.MAX_PURCHASE_CANDIDATES)
    config = dataclasses.replace(sweep.Config.committed(), gumbel_scale=0.4, max_candidates=4)

    with sweep._patched(config):
        assert simulator.PURCHASE_GUMBEL_SCALE == 0.4
        assert simulator.MAX_PURCHASE_CANDIDATES == 4

    assert (simulator.PURCHASE_GUMBEL_SCALE, simulator.MAX_PURCHASE_CANDIDATES) == before

    with pytest.raises(RuntimeError):
        with sweep._patched(config):
            raise RuntimeError("a persona policy blew up mid-sweep")

    assert (simulator.PURCHASE_GUMBEL_SCALE, simulator.MAX_PURCHASE_CANDIDATES) == before


def test_a_swept_policy_is_a_copy_and_the_cached_document_is_untouched():
    """`simcache.load_policy` hands out one shared dict per persona, forever."""
    sweeper = sweep.Sweeper(**FAST)
    original = sweeper.policies["browser"]
    before = dict(original)

    moved = sweep.adjust_policy(original, dataclasses.replace(
        sweep.Config.committed(), ad_pull_multiplier=2.0, threshold_offset=0.1
    ))

    assert original == before
    assert moved is not original
    assert moved["ad_receptivity"] == pytest.approx(before["ad_receptivity"] * 2.0)
    assert moved["purchase_threshold"] == pytest.approx(before["purchase_threshold"] + 0.1)


def test_ad_receptivity_touches_the_purchase_model_and_nothing_else(fast_sweeper):
    """The claim the ad-pull column rests on, checked rather than asserted in prose.

    The sweep moves the literal `0.2` in `ad_pull = 0.2 * ad_receptivity * ...`
    by scaling the policy field instead, which is only the same experiment if
    `ad_receptivity` reaches nothing but that term. If it ever also fed the
    attention layer, the ad-pull table would be measuring two changes at once
    and `docs/SENSITIVITY.md` would be reporting a confound as a result.
    """
    committed = sweep.Config.committed()
    policy = fast_sweeper.policies["browser"]

    def once(config):
        return simulator.run(
            fast_sweeper.store, sweep.adjust_policy(policy, config),
            n_runs=800, seed=42, variant_id="A", archetype="browser",
        )

    # With the threshold lifted out of reach nobody ever buys, so the purchase
    # branch has no effect on anything downstream. If `ad_receptivity` reached
    # any other part of the model, these two runs would diverge; they must be
    # identical fixation for fixation.
    never_buys = dataclasses.replace(committed, threshold_offset=10.0)
    quiet = once(never_buys)
    loud = once(dataclasses.replace(never_buys, ad_pull_multiplier=5.0))
    assert sum(quiet["purchase_share"].values()) == 0.0, "the threshold did not suppress buying"
    assert loud["fixation_prob"] == quiet["fixation_prob"]

    # And with buying switched back on it must move something, or the ad-pull
    # column is measuring nothing at all.
    assert once(dataclasses.replace(committed, ad_pull_multiplier=2.0))["purchase_share"] \
        != once(committed)["purchase_share"]


def test_the_same_arguments_render_byte_identical_text():
    """Determinism, end to end: two full renders of the same sweep."""
    args = sweep.build_parser().parse_args(["--only", "gumbel-scale", "--n-synth", "1500",
                                            "--seeds", "42", "43"])
    dimensions = sweep.chosen_dimensions(args.only)
    command = sweep.command_line(args)

    first = sweep.render(sweep.Sweeper(**FAST), dimensions, command=command)
    second = sweep.render(sweep.Sweeper(**FAST), dimensions, command=command)

    assert first == second


def test_the_headline_reports_a_range_and_names_the_constant_behind_it(fast_sweeper):
    """The point of the whole track: not one number, a span with a cause."""
    text = sweep.headline(fast_sweeper, sweep.DIMENSIONS)
    committed = sweep.Config.committed()

    means = []
    for dimension in sweep.DIMENSIONS:
        for _, config in dimension.configs(committed):
            cell = fast_sweeper.column(config)["lift:browser"]
            if cell.mean is not None:
                means.append(cell.mean)

    assert len(means) > 1
    assert min(means) < max(means), "a sweep that moves nothing is not a sensitivity analysis"
    assert f"{min(means):.3f} to {max(means):.3f}" in text
    assert "widest single driver" in text
    assert any(dimension.constant in text for dimension in sweep.DIMENSIONS)


def test_the_rendered_report_carries_the_range_summary_and_the_yardstick(fast_sweeper):
    """Both honesty tables have to survive into the doc, not just the headline."""
    text = sweep.render(fast_sweeper, sweep.DIMENSIONS, command="x")

    assert "Monte Carlo yardstick" in text
    assert "Range summary" in text
    # Every swept constant gets its own table, named as it is named in the code.
    for dimension in sweep.DIMENSIONS:
        assert dimension.constant in text
    # And the committed configuration is marked in each of them.
    assert text.count("<- committed") == len(sweep.DIMENSIONS)


def test_the_committed_constants_still_reproduce_the_reported_headline():
    """Seed 42, 10,000 shoppers: the 0.32 that RESULTS.md prints.

    This is the anchor the rest of the sweep hangs off. If `sim/simulator.py`
    or a persona policy changes so that the browser lift is no longer 0.32,
    every number in `docs/SENSITIVITY.md` is stale and the doc has to be
    regenerated -- which is exactly what this failing would be telling you.
    """
    sweeper = sweep.Sweeper(variant_id="A", n_synth=10_000, seeds=(42,))
    lift = sweeper.one(sweep.Config.committed(), 42)["lift:browser"]

    assert lift is not None
    assert round(lift, 2) == 0.32


def test_an_unknown_constant_is_refused():
    """`--only` is a closed set; a typo must not silently sweep everything."""
    with pytest.raises(SystemExit):
        sweep.build_parser().parse_args(["--only", "gumble-scale"])


def test_the_command_line_it_prints_is_the_one_that_reproduces_it():
    """The doc's top line has to be runnable, so it is built from the args."""
    argv = ["--only", "ad-pull", "--n-synth", "2000", "--seeds", "1", "2"]
    args = sweep.build_parser().parse_args(argv)
    printed = sweep.command_line(args)

    assert "--only ad-pull" in printed
    assert "--n-synth 2000" in printed
    assert "--seeds 1 2" in printed

    # And the defaults stay off the line, so the plain command stays plain.
    plain = sweep.command_line(sweep.build_parser().parse_args([]))
    assert plain == ".venv/Scripts/python.exe scripts/sweep_purchase_constants.py"


def test_it_runs_as_a_script_and_prints_a_table():
    """End to end through the real CLI, small enough to stay in the suite."""
    done = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "sweep_purchase_constants.py"),
         "--only", "gumbel-scale", "--n-synth", "1000", "--seeds", "42", "--quiet"],
        capture_output=True, text=True, cwd=str(ROOT),
    )

    assert done.returncode == 0, done.stderr
    assert "PURCHASE_GUMBEL_SCALE" in done.stdout
    assert "Range summary" in done.stdout
