"""What the camera did wrong, measured before the shelves are read (S30).

`vision/shelves.py` and `vision/track.py` each carry an unstated assumption
about the person holding the phone, and a clip filmed in a shop breaks both.
This module measures the two failures so the pipeline can correct one and
refuse the other.

**Roll, which is correctable.** `shelf_edge_rows` asks whether a single pixel
row is more than `MIN_SPAN` edge. A lip tilted by theta spreads its response
over `width * tan(theta)` rows, so the per-row fraction falls as the tilt
grows: on a 960-wide frame the strongest row of the repository's own fixture
measures 1.00 level, 0.70 at half a degree and 0.51 at one, which is under the
0.55 bar - and one degree is nothing. Worse, the range just below that does not
refuse, it under-counts, and shelf level is the largest term in
`sim/saliency.py`. The fix is not a looser threshold, which would let price
rails and shadows in; it is to measure the tilt and turn the frame back.

The search is brute force over a fixed range rather than a Hough transform,
for one reason: **the objective has to be the detector's own criterion.** An
angle that maximises agreement among Hough lines can still be an angle at which
`shelf_edge_rows` finds nothing, because Hough scores whole lines and the
detector scores rows. Rotating by quarter degrees and asking the detector's own
profile which angle it likes best cannot have that disagreement, and fifty
warps of one frame is milliseconds.

**Drift, which is not correctable here.** `vision/track.py` matches facings
across frames at IoU 0.5, which holds for a phone propped against a shelf and
fails for one being carried: the same pack lands somewhere new each time, never
overlaps itself, and is counted as a new product in every frame. A twenty
second walk past three bays reports over a hundred facings for two dozen packs
and reports them with confidence. Correcting it means registering the frames
into a mosaic and reading the shelf once across the whole strip, which is a
larger piece of work than this pipeline is. So drift is measured and handed to
`vision/pipeline.py`, which refuses - the rule the rest of this package already
follows, and the one that matters most here, because a fabricated planogram is
indistinguishable from a real one the moment it becomes JSON.
"""
from __future__ import annotations

from typing import List, Sequence

import cv2
import numpy as np

# How far off level this will look, in degrees either way, and it is set to the
# angle past which the correction stops working rather than to a round number.
#
# Turning a frame back does not only undo the tilt: it also swings the corners
# of the image out of frame and replicates the border into the wedges left
# behind. The further the turn, the more of the top and bottom shelf the frame
# loses. Measured on the committed fixture, which is five shelves and eight
# facings, the reading is exact through 7.00 degrees and drops to four bands at
# 7.25 - not because the angle was measured wrongly (it is measured to the
# quarter degree there) but because a shelf has left the picture.
#
# So the search range is the *correctness* limit, which makes saturation and
# failure the same event: a frame this can measure is a frame it can correct.
# That is what `roll_is_saturated` is for. 7 degrees each way at
# ROLL_SEARCH_STEP is 57 warps of one frame - milliseconds.
#
# The number is measured on a rendering of this repository's own planogram, so
# it is a property of that fixture as much as of the algorithm; a real shelf
# with less headroom above the top band would give up sooner. It is a stated
# measurement, not a guarantee about footage nobody has shot.
#
# The range is a **bracket, not a cap**. `estimate_roll` returns the best angle
# inside it, so a frame tilted further comes back clamped at the edge, and
# deskewing by a clamped value leaves a residual tilt. `shelf_bands` does not
# fail cleanly on a residual: it degrades, reading one band fewer than the bay
# has. Measured on the committed fixture at the old 6-degree range, 6.5 degrees
# estimated 6.00 and read four bands and seven facings where there are five and
# eight - and emitted a schema-valid planogram saying so. That is the silent
# under-count this module exists to remove, moved rather than removed.
#
# A saturated estimate cannot be told from a much larger one - a true 10 and a
# true 25 both come back at the edge - so saturation is the only signal
# available, and `roll_is_saturated` is what `vision/pipeline.py` refuses on.
ROLL_SEARCH_DEGREES = 7.0

# The search granularity, and therefore the accuracy `estimate_roll` claims. A
# quarter degree over the range above costs 57 warps of one frame; an eighth
# would cost 113 and buy nothing, because the bands it produces land on the
# same pixel rows.
#
# The estimate itself can land on a half step. `estimate_roll` returns the
# centre of the run of angles that tie for best, and an even-length run has its
# centre between two of them - which is a finer answer than the step, not a
# coarser one, and `deskew` takes any angle.
ROLL_SEARCH_STEP = 0.25

# How far the camera may travel between two sampled frames, as a fraction of
# the frame, before the IoU stage stops being able to tell one pack seen twice
# from two packs. Both committed fixtures jitter a pixel on purpose - about
# 0.15% at 640 wide - and a walk down an aisle moves about 5%, so the bar sits
# an order of magnitude clear of both.
MAX_DRIFT_FRACTION = 0.01


def _edge_profile_peak(frame: np.ndarray) -> float:
    """How much of the frame's strongest row is a horizontal edge.

    Imported from the detector rather than reimplemented: this is the number
    `shelf_edge_rows` thresholds against `MIN_SPAN`, and an objective that was
    merely correlated with it could pick an angle at which the detector still
    finds nothing.
    """
    from vision.shelves import _horizontal_edge_profile

    profile = _horizontal_edge_profile(frame)
    return float(profile.max()) if profile.size else 0.0


def estimate_roll(frame: np.ndarray) -> float:
    """How far off level `frame` is, in degrees, within the search range.

    Positive in the same sense as `cv2.getRotationMatrix2D`, so `deskew` undoes
    it by rotating back. Zero for a frame with no horizontal structure at all:
    a blank wall gives a flat objective, and returning its argmax would turn a
    frame by an arbitrary angle on no evidence and hand back something that
    looked corrected.

    **The peak is a plateau, and the answer is its centre.** The objective is
    the fraction of the strongest row that is edge, so it stops at exactly 1.0
    the moment a full-width lip lands on one row and every angle that still
    manages that scores the same. Only a drawn one-pixel edge puts a single
    angle there; a real lens, motion blur or an mp4 round trip smears the lip
    over several rows and widens the top into a run of tied angles, symmetric
    about the true tilt because the smearing is symmetric. Measured on this
    repository's fixture blurred at k=31, a true 0.0 ties from -0.50 to +0.50
    and a true +3.0 from +2.50 to +3.50.

    Picking any other member of that run is a *systematic* error, not a noisy
    one: taking the first tried returned exactly -0.50 degrees low at every
    tilt measured, twice the quarter-degree accuracy `ROLL_SEARCH_STEP` claims,
    and it does not average out - `vision/pipeline.py` reads one angle off the
    sharpest frame and deskews the whole clip by it.

    Tied is tested with `==` and that is deliberate rather than sloppy: the
    score is an integer count of strong pixels over the frame width, so equal
    counts give bitwise equal floats and a tolerance would only start merging
    genuinely different angles.
    """
    steps = int(round(ROLL_SEARCH_DEGREES / ROLL_SEARCH_STEP))
    angles = [index * ROLL_SEARCH_STEP for index in range(-steps, steps + 1)]
    scores = [_edge_profile_peak(_rotate(frame, -angle)) for angle in angles]

    best_score = max(scores)
    if best_score <= 0.0:
        return 0.0

    first, last = _widest_plateau(scores, best_score)

    # A plateau that runs off the end of the bracket was never bracketed, so
    # its centre is not something this measured - the objective may well go on
    # scoring 1.0 past the last angle tried. Reporting the edge is the honest
    # answer and the one `roll_is_saturated` reads, so the clip is refused
    # rather than corrected by a number pulled inward by the truncation.
    if first == 0:
        return angles[0]
    if last == len(angles) - 1:
        return angles[-1]

    return (angles[first] + angles[last]) / 2.0


def _widest_plateau(scores: Sequence[float], best_score: float) -> tuple[int, int]:
    """First and last index of the longest run of angles scoring `best_score`.

    Longest rather than first, and the middle one on a tie between equally long
    runs. Both cases are degenerate - a frame with two separate equally good
    tilts has no single answer to give - but they have to resolve the same way
    every time, because a committed planogram that changed between runs would
    make every number derived from it unreproducible.
    """
    runs: List[tuple[int, int]] = []
    index = 0
    while index < len(scores):
        if scores[index] != best_score:
            index += 1
            continue
        end = index
        while end + 1 < len(scores) and scores[end + 1] == best_score:
            end += 1
        runs.append((index, end))
        index = end + 1

    # `sum(run)` is twice the run's centre index and `len(scores) - 1` is twice
    # the index of zero degrees, so the second key is distance from level in
    # half-steps - kept negative so `max` prefers the nearer one.
    middle = len(scores) - 1
    return max(runs, key=lambda run: (run[1] - run[0], -abs(sum(run) - middle)))


def roll_is_saturated(roll_degrees: float) -> bool:
    """Did the search run out of range rather than find a peak?

    True when the estimate sits at the edge of what `estimate_roll` looked at,
    which means the real tilt is at least that and may be far more. Correcting
    by the clamped value would leave a residual tilt and read a shelf short, so
    the pipeline refuses instead - see the note on `ROLL_SEARCH_DEGREES`.
    """
    return abs(roll_degrees) >= ROLL_SEARCH_DEGREES


def deskew(frame: np.ndarray, roll_degrees: float) -> np.ndarray:
    """`frame` turned back by `roll_degrees`, so its shelf edges run flat.

    Returned unchanged at exactly zero. Rotating by nothing still resamples
    every pixel, and a clip that was already level would come back very
    slightly blurred for no reason.

    The border is replicated rather than filled with a constant. A constant
    would draw four straight edges of its own across the corners, and this
    module exists to find straight edges.
    """
    if roll_degrees == 0.0:
        return frame
    return _rotate(frame, -roll_degrees)


def _rotate(frame: np.ndarray, degrees: float) -> np.ndarray:
    height, width = frame.shape[:2]
    matrix = cv2.getRotationMatrix2D((width / 2.0, height / 2.0), degrees, 1.0)
    return cv2.warpAffine(
        frame,
        matrix,
        (width, height),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE,
    )


def camera_drift(frames: Sequence[np.ndarray]) -> float:
    """How far the camera moves between sampled frames, as a fraction of frame.

    The median of the per-pair displacements rather than the mean or the
    maximum: one person walking through shot, or one dropped frame, moves a
    single pair a long way and would condemn a clip that was otherwise filmed
    from a fixed point. A camera that is actually travelling moves *every*
    pair, which is what the median reads.

    Zero for a sequence with nothing to compare - drift is a property of a
    pair, and one frame cannot have moved.
    """
    if len(frames) < 2:
        return 0.0

    displacements = []
    for before, after in zip(frames, frames[1:]):
        height, width = before.shape[:2]
        (dx, dy), _response = cv2.phaseCorrelate(
            _grey(before), _grey(after), cv2.createHanningWindow((width, height), cv2.CV_32F)
        )
        # Each axis against its own extent before they are combined, so a
        # vertical drift on a wide frame is not quietly discounted.
        displacements.append(float(np.hypot(dx / width, dy / height)))

    return float(np.median(displacements))


def _grey(frame: np.ndarray) -> np.ndarray:
    grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    return grey.astype(np.float32)
