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

from typing import Sequence

import cv2
import numpy as np

# How far off level this will look, in degrees either way. Six is generous for
# a hand-held shot and cheap; past it the shot is not front-on in the sense the
# rest of the pipeline needs, and rotating it would not rescue the perspective
# convergence that comes with a camera that far off axis.
ROLL_SEARCH_DEGREES = 6.0

# The search granularity, and therefore the accuracy `estimate_roll` claims. A
# quarter degree costs 49 warps of one frame; an eighth would cost 97 and buy
# nothing, because the bands it produces land on the same pixel rows.
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
    """
    steps = int(round(ROLL_SEARCH_DEGREES / ROLL_SEARCH_STEP))
    angles = [index * ROLL_SEARCH_STEP for index in range(-steps, steps + 1)]

    best_angle = 0.0
    best_score = 0.0
    for angle in angles:
        score = _edge_profile_peak(_rotate(frame, -angle))
        # Strictly greater, and the list runs from negative to positive through
        # zero, so a tie between two equally good angles keeps the smaller
        # rotation rather than the first one tried.
        if score > best_score:
            best_score = score
            best_angle = angle

    return best_angle if best_score > 0.0 else 0.0


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
