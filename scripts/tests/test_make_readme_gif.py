"""Tests for scripts/make_readme_gif.py, the SPEC M8 what-if animation.

The rule this file defends: **every number the GIF draws is a number a
simulator run actually produced.**

A README GIF is the most-looked-at and least-checked artifact in the whole
repository. Nobody diffs an animation. So the two ways it could quietly start
lying are worth pinning down in tests rather than in a comment:

1. *Hardcoded numbers.* A generator that draws a pleasing curve someone typed
   in would produce exactly the same picture as one that runs the simulator.
   `test_drawn_numbers_move_with_n_synth` separates them: the bar heights are
   read back off the matplotlib artists, and re-running at a different
   `n_synth` must move them, because a Monte Carlo estimate over 200 shoppers
   is not the estimate over 400. A typed-in constant would not budge.

2. *Interpolated frames.* Tweening between two simulated states is the
   natural way to make an animation look smooth, and it would put a number on
   screen that no run produced -- the same failure `scripts/eval.py` refuses
   when it declines to draw an empty chart for a panel that does not exist.
   `test_no_frame_is_interpolated` asserts the GIF contains exactly as many
   frames as there were simulations, all of them distinct -- a hold is a
   frame delay, not a run of repeated frames, so there is nowhere for a
   tweened frame to hide.

The third rule here is smaller but load-bearing for the build: this script
must never touch RESULTS.md. `.github/workflows/ci.yml`'s evidence job fails
if RESULTS.md moves by a byte, and the figure code that this generator
borrows its conventions from lives in the script that *does* write it.
`test_generating_the_gif_leaves_results_md_alone` keeps the two apart.
"""
import hashlib
import json
import pathlib
import sys

import pytest
from PIL import Image, ImageSequence

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from api.app import simcache  # noqa: E402
from api.app.resolve import resolve  # noqa: E402
from scripts import make_readme_gif  # noqa: E402


# Small enough that five simulations finish in well under a second, large
# enough that the four personas produce visibly different numbers.
FAST_N_SYNTH = 200
SEED = 42


@pytest.fixture(scope="module")
def base():
    return json.loads(make_readme_gif.PLANOGRAM_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def states(base):
    return make_readme_gif.simulate_states(base, n_synth=FAST_N_SYNTH, seed=SEED)


def read_frames(path):
    """Every frame of the GIF as (composited RGB bytes, delay in ms), in order."""
    with Image.open(path) as image:
        assert image.format == "GIF"
        return [(frame.convert("RGB").tobytes(), frame.info.get("duration"))
                for frame in ImageSequence.Iterator(image)]


# ---------------------------------------------------------------------------
# The states themselves
# ---------------------------------------------------------------------------


def test_one_state_per_bay_one_shelf_level(states):
    """Five shelf levels in bay 1, so five states -- and one of them is the
    baseline, the level SKU_008 already occupies in the seed planogram."""
    assert [state.level for state in states] == [
        "top", "above_eye", "eye", "below_eye", "bottom",
    ]
    assert sum(1 for state in states if state.is_baseline) == 1


def test_every_state_carries_its_own_run_stamp(states):
    """A frame is traceable only if its stamp is its own. `sim_run_id` is
    built from the variant id, so five states must mean five variant ids --
    reuse one and every frame would carry the same id while showing different
    numbers, which is worse than no stamp at all."""
    assert len({state.variant_id for state in states}) == len(states)
    assert len({state.sim_run_id for state in states}) == len(states)
    for state in states:
        assert state.n_synth == FAST_N_SYNTH
        assert state.seed == SEED


def test_state_numbers_match_an_independent_simulation(base, states):
    """Rebuild each state's patch from scratch and re-simulate it.

    `simcache.population` memoises on (variant_id, planogram content, n_synth,
    seed), so a naive recompute would be handed the generator's own cached
    object and compare it against itself. The cache is cleared first so this
    is a real second run.
    """
    simcache._simulations.clear()

    for state in states:
        variant = make_readme_gif.variant_for(base, state.patches)
        bundle = simcache.population(
            resolve(base, variant), variant["variant_id"],
            n_synth=FAST_N_SYNTH, seed=SEED,
        )
        assert bundle.population["sim_run_id"] == state.sim_run_id
        for persona_id, drawn in state.fixation_prob.items():
            assert drawn == bundle.per_persona[persona_id]["fixation_prob"][state.slot_id]


def test_moving_the_sku_actually_changes_the_numbers(states):
    """If every state produced the same vector the animation would be a still
    frame, and the what-if it claims to show would not exist."""
    vectors = {tuple(sorted(state.fixation_prob.items())) for state in states}
    assert len(vectors) == len(states)


# ---------------------------------------------------------------------------
# What lands in the GIF
# ---------------------------------------------------------------------------


def test_writes_a_gif_that_holds_every_state(tmp_path, states):
    """Each state gets one frame, and that frame's own delay is the hold."""
    out = tmp_path / "whatif.gif"
    summary = make_readme_gif.render_gif(states, out, hold_seconds=1.5)

    assert out.exists()
    frames = read_frames(out)
    assert len(frames) == summary["n_frames"] == len(states)
    assert [delay for _image, delay in frames] == [1500] * len(states)
    assert summary["duration_s"] == pytest.approx(len(states) * 1.5)


def test_no_frame_is_interpolated(tmp_path, states):
    """Exactly one distinct rendered image per simulated state.

    A tweened frame between two states would show a fixation probability no
    run produced -- the same failure `scripts/eval.py` refuses when it
    declines to draw a chart for a panel that does not exist. Counting
    distinct rendered images is the cheapest honest test of that: interpolate
    anything, anywhere, and the count exceeds the number of simulations
    immediately.
    """
    out = tmp_path / "whatif.gif"
    make_readme_gif.render_gif(states, out, hold_seconds=1.0)
    images = [image for image, _delay in read_frames(out)]

    assert len(images) == len(states)
    assert len(set(images)) == len(states)


def test_drawn_bar_heights_are_the_simulated_numbers(tmp_path, states):
    """Read the bar heights back off the matplotlib artists.

    Everything above tests the data the generator *computed*. This tests what
    it actually put on the canvas, which is the only thing a reader sees.
    """
    out = tmp_path / "whatif.gif"
    summary = make_readme_gif.render_gif(states, out, hold_seconds=1.0)

    assert len(summary["states"]) == len(states)
    for drawn, state in zip(summary["states"], states):
        assert drawn["sim_run_id"] == state.sim_run_id
        assert drawn["drawn_values"] == pytest.approx(
            {**state.fixation_prob, "population": state.population_fixation_prob}
        )


def test_drawn_numbers_move_with_n_synth(base, tmp_path):
    """The proof that the simulator is in the loop.

    Two runs of the same five states at different shopper counts. A hardcoded
    or interpolated set of numbers would be identical across both; Monte Carlo
    estimates over 200 shoppers and over 400 are not.
    """
    small = make_readme_gif.render_gif(
        make_readme_gif.simulate_states(base, n_synth=FAST_N_SYNTH, seed=SEED),
        tmp_path / "small.gif", hold_seconds=1.0,
    )
    large = make_readme_gif.render_gif(
        make_readme_gif.simulate_states(base, n_synth=FAST_N_SYNTH * 2, seed=SEED),
        tmp_path / "large.gif", hold_seconds=1.0,
    )

    assert [s["drawn_values"] for s in small["states"]] != [
        s["drawn_values"] for s in large["states"]
    ]
    # ...and the stamps move with them, so no frame can claim a run it is not from.
    assert [s["sim_run_id"] for s in small["states"]] != [
        s["sim_run_id"] for s in large["states"]
    ]


def test_frame_says_it_is_synthetic(tmp_path, states):
    """`data/sessions/anon/` is empty -- there is no real panel. A frame that
    let a reader assume these bars were measured on people would be the single
    most damaging thing this repository could publish."""
    out = tmp_path / "whatif.gif"
    summary = make_readme_gif.render_gif(states, out, hold_seconds=1.0)
    for drawn in summary["states"]:
        assert make_readme_gif.SYNTHETIC_NOTICE in drawn["caption"]
        assert drawn["sim_run_id"] in drawn["caption"]
        assert str(drawn["n_synth"]) in drawn["caption"]


# ---------------------------------------------------------------------------
# What must NOT happen
# ---------------------------------------------------------------------------


def test_generating_the_gif_leaves_results_md_alone(tmp_path, states):
    """This generator is deliberately not part of scripts/eval.py.

    `analytics/report.py` writes the name of every figure it draws into
    RESULTS.md, and CI's evidence job fails if RESULTS.md moves by a byte.
    Wiring the GIF into eval would move it.
    """
    results = ROOT / "RESULTS.md"
    before = hashlib.md5(results.read_bytes()).hexdigest()
    make_readme_gif.render_gif(states, tmp_path / "whatif.gif", hold_seconds=1.0)
    assert hashlib.md5(results.read_bytes()).hexdigest() == before


def test_generator_writes_no_prediction_lock(tmp_path, states):
    """Prediction locks are evidence; `predictions/` holds one file per real
    session and nothing else. Drawing a picture must not add to it."""
    predictions = ROOT / "predictions"
    before = sorted(p.name for p in predictions.iterdir())
    make_readme_gif.render_gif(states, tmp_path / "whatif.gif", hold_seconds=1.0)
    assert sorted(p.name for p in predictions.iterdir()) == before
