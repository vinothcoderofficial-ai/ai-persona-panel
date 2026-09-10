"""One shelf band, segmented into product facings (S30).

`vision/shelves.py` says where a shelf is; this says how many products stand on
it and how wide each one is. That count is `facings` in the planogram, which is
the second term in `sim/saliency.py` after shelf level, so it feeds every
attention number a video-derived planogram ever produces.

**This finds facings, not products.** It measures where one block of colour
ends and the next begins, how wide each block is, and what colour it is. It has
no idea of brand, name or price, and `vision/planogram.py` says so in the
document it emits rather than filling those fields with something plausible.

The colour is not decoration. `color_lab` feeds the colour-contrast term of the
saliency model, so a shelf read off a video is genuinely simulatable even with
every product unidentified - which is what makes this pipeline worth having on
a CPU at all.

The method: a shelf front is a row of adjacent colour blocks. Reduce the band
to one mean colour per column, walk that profile, and cut where the colour
changes by more than a threshold. That gets right the case a plain edge
detector gets wrong - two identical packs side by side, separated only by a
seam - which is why `facings` counts 2 there rather than 1.

Two heuristics that are worth knowing are heuristics, because both are places
this can be wrong on a real clip:

* **A sliver is merged, not kept.** A run narrower than `MIN_FACING_PX` is a
  scuff, a joint or a price rail, and it is absorbed into whichever neighbour
  it resembles more - so the two sides of it become one facing again rather
  than two.
* **A band with the same colour at both ends has a background.** Products do
  not usually run to the exact edge of a shot, so matching ends are read as
  shelf backing and every run of that colour is dropped. When the two ends
  differ, no background is assumed and every run is a facing. This is the rule
  that separates "two packs with a gap between them" from "an empty shelf", and
  it is the first thing to look at if a real clip comes out wrong.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence, Tuple

import cv2
import numpy as np

from vision.shelves import Band

# A run narrower than this is a scuff, a joint or a shadow rather than a pack.
# Merged into a neighbour rather than dropped, so removing it does not leave
# its two sides counted as separate facings.
MIN_FACING_PX = 12

# How far apart two columns' colours must be, in Lab, to be different products.
# Lab is used precisely so this threshold means roughly the same thing at every
# brightness, which is not true in BGR.
COLOUR_BREAK = 12.0

# A block this far from its neighbours is as certain as this pipeline gets.
# Above it confidence saturates at 1.0 rather than growing without meaning.
CONFIDENT_DELTA = 45.0


@dataclass(frozen=True)
class Facing:
    """One product facing, as measured from one frame.

    `color_lab` is CIE L*a*b*, matching `schemas/planogram.schema.json`'s
    `sku.color_lab`, and is the mean over the facing's columns - a real
    measurement, and the one thing about an unidentified product the saliency
    model can still use.

    `confidence` is how distinct this block is from what is beside it, mapped
    to 0..1. It is not a probability and does not claim to be; it is a
    monotonic reading of the evidence, and it is what
    `schemas/planogram.schema.json`'s `slot.confidence` carries.
    """

    x0: int
    x1: int
    top: int
    bottom: int
    color_lab: Tuple[float, float, float]
    confidence: float

    @property
    def width(self) -> int:
        return self.x1 - self.x0

    @property
    def box(self) -> Tuple[int, int, int, int]:
        return (self.x0, self.top, self.x1, self.bottom)


def _column_lab(strip: np.ndarray) -> np.ndarray:
    """Mean L*a*b* per column of a band strip, shape (width, 3).

    OpenCV's 8-bit Lab is scaled (L in 0..255, a/b offset by 128); it is
    converted to CIE ranges here so `color_lab` in the emitted planogram means
    the same thing as `color_lab` in the seed one, which was authored in real
    Lab. A planogram whose colours were in a different space would quietly
    change every colour-contrast term in the saliency model.
    """
    lab = cv2.cvtColor(strip, cv2.COLOR_BGR2LAB).astype(np.float32)
    lab[:, :, 0] *= 100.0 / 255.0
    lab[:, :, 1] -= 128.0
    lab[:, :, 2] -= 128.0
    return lab.mean(axis=0)


def _raw_runs(profile: np.ndarray) -> List[Tuple[int, int]]:
    """Column ranges between colour breaks, as (start, end_exclusive).

    A break is a **local step**: the mean colour of the `MIN_FACING_PX` columns
    ending at a column, against the mean of the `MIN_FACING_PX` columns
    starting there. A step of `COLOUR_BREAK` between those two neighbourhoods
    is a boundary; anything gentler is not.

    Two failure modes bracket this rule, and both were measured rather than
    imagined.

    **Column against its neighbour cannot see a soft edge.** Nothing filmed has
    a one-pixel edge - depth of field, the shelf's own shadow, motion blur and
    any resampling spread it over a band of columns - and a boundary that
    arrives over twenty columns never shows a single step of twelve. Three
    packs with a 24-column ramp between them read as *zero* facings, because a
    band that is one run end to end is then suppressed as background.

    **Column against its run's mean sees edges that are not there.** Drift from
    the mean of the first n columns grows like slope*n/2, so a smooth
    illumination gradient crosses any fixed threshold eventually, however
    gentle - the break is a property of the band's width, not of anything on
    the shelf. An empty band spanning about 25 L* split into two runs, whose
    means then sat far enough apart that `_background` concluded there was no
    backing and returned both as products. A 30% radial falloff, ordinary for a
    phone lens, was enough. Nothing downstream would have caught it either: a
    lighting gradient is static, so the same phantom appears in every sampled
    frame and `vision/track.py` reads that as corroboration and *raises* its
    confidence.

    Comparing two neighbourhoods separates the two cases by the one thing that
    actually distinguishes them, which is spatial scale. A pack edge is a step
    over tens of columns; a lighting gradient is a ramp over hundreds. The soft
    edge still reads full strength here, because the window is narrower than
    the ramp is wide; the gradient contributes only its slope across one window,
    which is a fraction of a Lab unit.
    """
    width = profile.shape[0]
    if width == 0:
        return []

    window = MIN_FACING_PX

    # How big a colour step sits at each column, comparing the `window` columns
    # behind it against the `window` columns in front.
    steps = np.zeros(width, dtype=np.float32)
    for column in range(1, width):
        before = profile[max(0, column - window) : column]
        after = profile[column : min(width, column + window)]
        if before.shape[0] == 0 or after.shape[0] == 0:
            continue
        steps[column] = float(np.linalg.norm(after.mean(axis=0) - before.mean(axis=0)))

    # Where each column sits against the one before it. This is the old rule,
    # kept for what it is genuinely good at: saying precisely WHERE an edge is.
    # It cannot say whether there is one - it is blind to any edge that arrives
    # gradually - so the two are used for different halves of the question.
    adjacent = np.zeros(width, dtype=np.float32)
    for column in range(1, width):
        adjacent[column] = float(np.linalg.norm(profile[column] - profile[column - 1]))

    # One edge raises `steps` across the whole window either side of it, so a
    # cluster of columns is over the line for a single boundary. The window
    # decides that a boundary exists; the derivative decides which column it is.
    # Taking the window's own peak instead puts the break up to a window early -
    # measured, it turned a three-pixel scuff into a twelve-pixel facing, which
    # is exactly wide enough to survive `_absorb_slivers`.
    breaks: List[int] = [0]
    over = steps >= COLOUR_BREAK
    column = 1
    while column < width:
        if not over[column]:
            column += 1
            continue
        end = column
        while end + 1 < width and over[end + 1]:
            end += 1
        peak = int(column + int(np.argmax(adjacent[column : end + 1])))
        if peak > breaks[-1]:
            breaks.append(peak)
        column = end + 1

    breaks.append(width)
    return [(breaks[i], breaks[i + 1]) for i in range(len(breaks) - 1)]


def _mean_of(profile: np.ndarray, run: Tuple[int, int]) -> np.ndarray:
    return profile[run[0] : run[1]].mean(axis=0)


def _absorb_slivers(
    profile: np.ndarray, runs: List[Tuple[int, int]]
) -> List[Tuple[int, int]]:
    """Merge every run narrower than `MIN_FACING_PX` into a neighbour.

    Merged rather than dropped, and that distinction is the whole point: a
    three-pixel scratch across an otherwise uniform shelf splits it into two
    wide runs, and dropping the scratch alone would leave those two counted as
    two facings of an empty shelf.
    """
    if not runs:
        return []

    changed = True
    while changed and len(runs) > 1:
        changed = False
        for index, run in enumerate(runs):
            if run[1] - run[0] >= MIN_FACING_PX:
                continue

            left = index - 1 if index > 0 else None
            right = index + 1 if index + 1 < len(runs) else None
            mean = _mean_of(profile, run)

            def distance(other: int) -> float:
                return float(np.linalg.norm(mean - _mean_of(profile, runs[other])))

            if left is None:
                target = right
            elif right is None:
                target = left
            else:
                target = left if distance(left) <= distance(right) else right

            merged = (min(run[0], runs[target][0]), max(run[1], runs[target][1]))
            runs = [r for i, r in enumerate(runs) if i not in (index, target)]
            runs.append(merged)
            runs.sort()
            changed = True
            break

    return runs


def _background(profile: np.ndarray, runs: Sequence[Tuple[int, int]]) -> np.ndarray | None:
    """The band's backing colour, if the two ends agree on one.

    Products rarely run to the exact edge of a shot, so a band whose leftmost
    and rightmost columns are the same colour is showing shelf backing at both
    ends. When the ends differ, nothing is assumed: the band is full of
    products edge to edge, and dropping either end would lose a real facing.

    A heuristic, and named as one. It is what separates "two packs with a gap"
    from "an empty shelf", and the first thing to check if a real clip reads
    wrong.
    """
    if len(runs) < 2:
        return None
    first = _mean_of(profile, runs[0])
    last = _mean_of(profile, runs[-1])
    if float(np.linalg.norm(first - last)) < COLOUR_BREAK:
        return (first + last) / 2.0
    return None


def _distinctness(profile: np.ndarray, runs: Sequence[Tuple[int, int]], index: int) -> float:
    """How far this run's colour sits from its neighbours', in Lab.

    The larger of the two gaps, because a pack at the end of a shelf has only
    one neighbour and is no less certain for it.
    """
    mean = _mean_of(profile, runs[index])
    gaps = []
    if index > 0:
        gaps.append(float(np.linalg.norm(mean - _mean_of(profile, runs[index - 1]))))
    if index + 1 < len(runs):
        gaps.append(float(np.linalg.norm(mean - _mean_of(profile, runs[index + 1]))))
    return max(gaps) if gaps else 0.0


def facing_boxes(frame: np.ndarray, band: Band) -> List[Facing]:
    """Product facings standing in `band`, left to right.

    Empty for a uniform band. An empty shelf is a real planogram state -
    `sku_id: null`, `facings: 0`, which is what makes "move a SKU to eye level"
    expressible (CLAUDE.md) - and not a detection failure to be papered over
    with a guess.

    A band outside the frame returns nothing rather than raising: bands are
    measured on one frame and applied across a clip, and a video that changes
    resolution part-way through must not take the pipeline down.
    """
    height, width = frame.shape[:2]
    top = max(0, min(int(band.top), height))
    bottom = max(0, min(int(band.bottom), height))
    if bottom - top < 2 or width < MIN_FACING_PX:
        return []

    strip = frame[top:bottom, :]
    profile = _column_lab(strip)

    runs = _absorb_slivers(profile, _raw_runs(profile))
    # One run left means the band is one colour: an empty shelf.
    if len(runs) < 2:
        return []

    backing = _background(profile, runs)

    facings: List[Facing] = []
    for index, (start, end) in enumerate(runs):
        if end - start < MIN_FACING_PX:
            continue
        mean = _mean_of(profile, (start, end))
        if backing is not None and float(np.linalg.norm(mean - backing)) < COLOUR_BREAK:
            continue

        distinct = _distinctness(profile, runs, index)
        facings.append(
            Facing(
                x0=int(start),
                x1=int(end),
                top=top,
                bottom=bottom,
                color_lab=(float(mean[0]), float(mean[1]), float(mean[2])),
                confidence=float(min(1.0, distinct / CONFIDENT_DELTA)),
            )
        )

    return facings
