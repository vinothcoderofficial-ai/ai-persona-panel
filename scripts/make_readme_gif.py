"""`make readme-gif` -- the 15-second what-if animation SPEC M8 asks for.

Run: `python scripts/make_readme_gif.py` (exit 0 on success). Writes
`docs/figures/whatif_eye_level.gif`.

SPEC M8 lists, among the things README.md must carry, a "15-second GIF of the
what-if". This is that GIF: SKU_008 walks down the five shelf levels of bay 1,
and the four persona panels' attention moves with it.

Why this is generated, not screen-recorded
------------------------------------------
The obvious way to make this file is to record somebody dragging a SKU around
the 3D store. That was rejected for two reasons. The practical one is that
this repository has no screen recorder, no video encoder beyond Pillow, and no
browser automation -- Playwright was explicitly cut (docs/PLAN.md:150). The
real one is that a screen capture is not checkable. Nobody diffs an
animation, so a recording of numbers is a claim with no evidence behind it,
while an animation generated from `api/app/simcache.py` can be regenerated,
tested, and traced back to the runs that produced it.

So every frame is stamped with its own `sim_run_id`, `n_synth` and `seed`.
Those are the same ids `POST /whatif` returns for the same patch, because the
variant id is built by the router's own helpers rather than a second copy of
the recipe -- paste a frame's id into the what-if endpoint and the numbers
come back identical.

Five real states, held. Never interpolated.
-------------------------------------------
Each shelf level is a real simulation, and its frame is grabbed repeatedly
without redrawing, so the held frames are byte-identical by construction.
Nothing is tweened between states. A smoothed intermediate frame would put a
fixation probability on screen that no run produced, which is the same failure
`scripts/eval.py` refuses when it declines to draw an empty chart for a real
panel that does not exist (see `_draw_figures` there).

Synthetic only
--------------
`data/sessions/anon/` is empty. There is no real panel, and every frame says
so in as many words. The bars are four AI personas and their share-weighted
population -- not people.

Deliberately not part of scripts/eval.py
----------------------------------------
`analytics/report.py` writes the name of every figure eval draws into
RESULTS.md, and the evidence job in `.github/workflows/ci.yml` fails if
RESULTS.md moves by a byte. This generator therefore lives on its own target
and shares nothing with eval but its drawing conventions.

Formulas
--------
None live here. The simulation is `api/app/simcache.py`, `resolve()` is
`api/app/resolve.py`, and the maths under both is `sim/saliency.py` and
`sim/simulator.py`. This module chooses five planogram states, asks for them,
and draws what comes back.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import matplotlib

matplotlib.use("Agg")  # before pyplot: this runs headless, same as eval.py

import matplotlib.animation as animation  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from api.app import prediction, simcache  # noqa: E402
from api.app.resolve import resolve  # noqa: E402

# The what-if variant id is imported, not re-derived. `_variant_id` folds the
# base planogram's content hash and the patch list into the id that
# `sim_run_id` is then built from, so a second copy of that recipe here would
# eventually disagree with the API and the stamps on these frames would stop
# meaning anything. These names are private by convention, and reusing them is
# the lesser evil: whatif.py's own `_canonical` docstring makes the same
# argument for one recipe project-wide.
from api.app.routers.whatif import (  # noqa: E402
    _document_hash,
    _variant_document,
    _variant_id,
)

PLANOGRAM_PATH = ROOT / "data" / "planograms" / "demo_aisle.json"
DEFAULT_OUTPUT = ROOT / "docs" / "figures" / "whatif_eye_level.gif"

# The SKU that walks the shelf. SKU_008 sits on the bottom shelf in the seed
# planogram, which makes "move it to eye level" a move with somewhere to go.
SKU_ID = "SKU_008"
BAY_ID = "B1"

N_SYNTH = prediction.N_SYNTH
SEED = prediction.SEED

# Five shelf levels at 3.0s each is the 15 seconds SPEC M8 names.
HOLD_SECONDS = 3.0
DPI = 100

SYNTHETIC_NOTICE = "synthetic personas only - no real panel was measured"


@dataclass(frozen=True)
class HeldState:
    """One shelf level: the patch that puts SKU_ID there, the run that
    measured it, and the numbers that run produced."""

    level: str
    slot_id: str
    is_baseline: bool
    patches: List[Dict[str, Any]]
    variant_id: str
    sim_run_id: str
    n_synth: int
    seed: int
    fixation_prob: Dict[str, float]
    population_fixation_prob: float

    @property
    def caption(self) -> str:
        """The provenance line drawn under the chart. Everything needed to go
        and re-run this exact frame."""
        return (f"{SYNTHETIC_NOTICE}  |  sim_run_id {self.sim_run_id}  |  "
                f"variant {self.variant_id}  |  n_synth {self.n_synth} per persona  |  "
                f"seed {self.seed}")


def variant_for(base: Mapping[str, Any], patches: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """The unsaved what-if variant document for `patches` against `base`.

    Same id the what-if endpoint would give it, so the stamp on a frame is a
    handle a reader can actually use.
    """
    patch_list = [dict(patch) for patch in patches]
    variant_id = _variant_id(_document_hash(base), patch_list)
    return _variant_document(base["planogram_id"], variant_id, patch_list)


def shelf_states(base: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """One target per shelf level in `BAY_ID`, top to bottom, in planogram order.

    The first slot of each shelf is the destination. Using the same position
    on every shelf keeps one thing varying across the animation -- the height
    SKU_ID is displayed at -- which is the whole point of the what-if.
    """
    bay = next((b for b in base["bays"] if b["bay_id"] == BAY_ID), None)
    if bay is None:
        raise SystemExit(f"{PLANOGRAM_PATH.name}: no bay {BAY_ID!r} to walk")

    targets = []
    for shelf in bay["shelves"]:
        if not shelf["slots"]:
            raise SystemExit(f"shelf {shelf['shelf_id']!r} has no slots to move {SKU_ID} into")
        slot = shelf["slots"][0]
        targets.append({
            "level": shelf["level"],
            "slot_id": slot["slot_id"],
            # The level SKU_ID already occupies is the baseline. Its patch is a
            # no-op move, which resolve() returns unchanged -- so the frame is a
            # real simulation of the shipped planogram, not a special case.
            "is_baseline": slot["sku_id"] == SKU_ID,
        })
    return targets


def simulate_states(base: Mapping[str, Any], *, n_synth: int = N_SYNTH,
                    seed: int = SEED) -> List[HeldState]:
    """Run the five what-ifs. Five real simulations, no shortcuts between them."""
    states: List[HeldState] = []

    for target in shelf_states(base):
        patches = [{"op": "move_sku", "sku_id": SKU_ID, "to_slot_id": target["slot_id"]}]
        variant = variant_for(base, patches)
        bundle = simcache.population(
            resolve(base, variant), variant["variant_id"], n_synth=n_synth, seed=seed,
        )
        slot_id = target["slot_id"]
        states.append(HeldState(
            level=target["level"],
            slot_id=slot_id,
            is_baseline=target["is_baseline"],
            patches=patches,
            variant_id=variant["variant_id"],
            sim_run_id=bundle.population["sim_run_id"],
            n_synth=n_synth,
            seed=seed,
            fixation_prob={
                persona_id: float(result["fixation_prob"][slot_id])
                for persona_id, result in bundle.per_persona.items()
            },
            population_fixation_prob=float(bundle.population["fixation_prob"][slot_id]),
        ))

    return states


# ---------------------------------------------------------------------------
# Drawing (conventions borrowed from scripts/eval.py's figure section)
# ---------------------------------------------------------------------------


def render_gif(states: Sequence[HeldState], path: Path, *,
               hold_seconds: float = HOLD_SECONDS) -> Dict[str, Any]:
    """Draw one frame per state and write them as an animated GIF.

    Exactly one encoded frame per simulated state. A GIF stores a delay per
    frame, and `PillowWriter` turns fps into that delay, so a frame rate of
    1/hold_seconds is what "hold this state for three seconds" means here.
    Writing it this way rather than grabbing the same canvas repeatedly is not
    only smaller -- it makes the no-interpolation rule structural. There is no
    slot in the file for a tweened frame to occupy, because the file has as
    many frames as there were simulations.

    Returns what was actually drawn -- bar heights read back off the artists,
    not the values handed in -- so a test can check the canvas rather than the
    intention.
    """
    if not states:
        raise ValueError("nothing to animate: simulate_states returned no states")
    if hold_seconds <= 0:
        raise ValueError(f"hold_seconds must be positive, got {hold_seconds}")

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # One y-scale for the whole animation. Rescaling per frame would make an
    # unchanged bar appear to move, which is exactly the illusion this figure
    # exists to avoid.
    ceiling = max(
        max(list(state.fixation_prob.values()) + [state.population_fixation_prob])
        for state in states
    )
    y_limit = ceiling * 1.28 if ceiling > 0 else 1.0

    levels = [state.level for state in states]
    persona_ids = sorted(states[0].fixation_prob)

    figure, (bars_axes, shelf_axes) = plt.subplots(
        1, 2, figsize=(9.6, 4.6), width_ratios=[3.1, 1.0],
    )

    # Created once and retargeted per state: figure-level text is not cleared
    # by axes.clear(), so re-adding it every frame would stack the captions.
    heading = figure.suptitle("", fontsize=13)
    footer = figure.text(0.5, 0.028, "", ha="center", fontsize=7, color="0.3")

    drawn_states: List[Dict[str, Any]] = []
    writer = animation.PillowWriter(fps=1.0 / hold_seconds)

    with writer.saving(figure, str(path), dpi=DPI):
        for state in states:
            drawn = _draw_state(bars_axes, shelf_axes, heading, footer, state,
                                persona_ids=persona_ids, levels=levels, y_limit=y_limit)
            drawn_states.append(drawn)
            figure.tight_layout(rect=(0, 0.06, 1, 0.93))
            writer.grab_frame()

    plt.close(figure)

    return {
        "path": path,
        "n_frames": len(states),
        "hold_seconds": float(hold_seconds),
        "duration_s": len(states) * float(hold_seconds),
        "states": drawn_states,
    }


def _draw_state(bars_axes, shelf_axes, heading, footer, state: HeldState, *,
                persona_ids: Sequence[str], levels: Sequence[str],
                y_limit: float) -> Dict[str, Any]:
    """One frame: attention per persona beside the shelf it was measured on."""
    bars_axes.clear()
    shelf_axes.clear()

    labels = list(persona_ids) + ["population"]
    heights = [state.fixation_prob[persona_id] for persona_id in persona_ids]
    heights.append(state.population_fixation_prob)
    positions = np.arange(len(labels))

    personas_bar = bars_axes.bar(positions[:-1], heights[:-1], 0.55, label="persona")
    population_bar = bars_axes.bar(positions[-1:], heights[-1:], 0.55,
                                   label="population (share-weighted)")
    for container in (personas_bar, population_bar):
        bars_axes.bar_label(container, fmt="%.4f", fontsize=8, padding=2)

    bars_axes.set_xticks(positions)
    bars_axes.set_xticklabels(labels, fontsize=9)
    bars_axes.set_ylim(0, y_limit)
    bars_axes.set_ylabel(f"share of fixations landing on {SKU_ID}")
    bars_axes.set_title(f"{SKU_ID} on slot {state.slot_id}"
                        + ("  (as shipped)" if state.is_baseline else ""), fontsize=10)
    bars_axes.legend(fontsize=8, loc="upper left")

    # The shelf beside the chart: five levels, the occupied one picked out, so
    # a reader can see what moved without reading the slot id.
    rows = np.arange(len(levels))
    active = list(levels).index(state.level)
    shelf_axes.barh(rows, np.ones(len(levels)), 0.72, color="0.88")
    shelf_axes.barh([rows[active]], [1.0], 0.72)
    shelf_axes.text(0.5, rows[active], SKU_ID, ha="center", va="center",
                    fontsize=9, color="white", fontweight="bold")
    shelf_axes.set_yticks(rows)
    shelf_axes.set_yticklabels([level.replace("_", " ") for level in levels], fontsize=9)
    shelf_axes.set_xticks([])
    shelf_axes.set_xlim(0, 1)
    shelf_axes.invert_yaxis()
    shelf_axes.set_title(f"bay {BAY_ID}", fontsize=10)
    for side in ("top", "right", "bottom", "left"):
        shelf_axes.spines[side].set_visible(False)

    heading.set_text(f"What-if: move {SKU_ID} to the {state.level.replace('_', ' ')} shelf")
    footer.set_text(state.caption)

    return {
        "level": state.level,
        "slot_id": state.slot_id,
        "sim_run_id": state.sim_run_id,
        "variant_id": state.variant_id,
        "n_synth": state.n_synth,
        "seed": state.seed,
        "caption": state.caption,
        # Read off the artists, not copied from the state: this is what the
        # canvas holds, which is the only thing a reader of the GIF sees.
        "drawn_values": {label: float(bar.get_height()) for label, bar
                         in zip(labels, list(personas_bar) + list(population_bar))},
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--planogram", default=str(PLANOGRAM_PATH))
    parser.add_argument("--out", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--n-synth", type=int, default=N_SYNTH)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--hold-seconds", type=float, default=HOLD_SECONDS,
                        help="how long each simulated state is held (never interpolated)")
    args = parser.parse_args(argv)

    base = json.loads(Path(args.planogram).read_text(encoding="utf-8"))
    states = simulate_states(base, n_synth=args.n_synth, seed=args.seed)
    summary = render_gif(states, Path(args.out), hold_seconds=args.hold_seconds)

    print(f"wrote {_relative(summary['path'])}  "
          f"{summary['n_frames']} frames, {summary['duration_s']:.1f}s, "
          f"one per simulated state, {SYNTHETIC_NOTICE}")
    for state in states:
        print(f"  {state.level:<10} {state.slot_id}  sim_run_id {state.sim_run_id}  "
              f"population {state.population_fixation_prob:.4f}")
    return 0


def _relative(path: Path) -> str:
    try:
        return Path(path).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return Path(path).as_posix()


if __name__ == "__main__":
    raise SystemExit(main())
