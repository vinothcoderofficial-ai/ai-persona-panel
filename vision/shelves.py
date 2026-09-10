"""Where the shelves are in a frame (S30).

The first half of video -> planogram. A facing can only be placed once the
shelf it stands on is known, and the shelf *level* - top, eye, bottom - is the
strongest single term in `sim/saliency.py`, so an error here propagates into
every attention number the video ever produces.

**Classical CV, not a detection model.** PLAN S20 specified Grounding DINO on a
GPU; this machine has no CUDA build of torch and no GPU budget, so the pipeline
was rebuilt around what a shelf edge actually *is* in an image: a long,
horizontal, high-contrast discontinuity spanning most of the frame. A vertical
Sobel followed by a column sum finds exactly that, in milliseconds, on a CPU.

It is genuinely less general than a detector - it wants a roughly front-on
shot, and it will not read a shelf edge that is heavily occluded - and the
tests are written to pin where it stops rather than to flatter it. In
particular a frame with nothing in it returns nothing: a detector that always
emits its best guess turns "there was nothing to see" into a planogram, and
nothing downstream can tell the difference afterwards.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence

import cv2
import numpy as np

# An edge has to span this fraction of the frame width to be a shelf. A price
# label, a shadow or a sticker is horizontal too; a shelf goes all the way
# across. 0.55 rather than 0.9 because a bay upright or a shopper's arm
# routinely interrupts a real shelf edge.
MIN_SPAN = 0.55

# Two edges closer than this are the top and bottom of one shelf lip. Merging
# them is what stops a five-shelf bay being reported as ten.
MERGE_WITHIN_PX = 18

# A gap narrower than this cannot hold a product, so it is not a band. Without
# it, a merged lip's leftovers become a shelf of zero-height slots.
MIN_BAND_PX = 20

# There is deliberately no second, *relative* bar here - no "a row must also be
# some fraction of the frame's strongest row". One was carried until it was
# measured and found to be inert: the profile is a fraction of the frame width,
# so it is at most 1.0; candidates have already cleared `MIN_SPAN` at 0.55; and
# 0.35 of at-most-1.0 cannot exclude a row that has cleared 0.55. It would have
# taken a profile maximum above 1.571 to drop even one, which a fraction cannot
# reach. Its stated job - that exposure and contrast must not change the shelf
# count - is real and is done a step earlier, by the per-frame gradient
# threshold inside `_horizontal_edge_profile`.


@dataclass(frozen=True)
class Band:
    """The space between two shelf edges, where products stand.

    `top` and `bottom` are pixel rows in the frame this was measured from.
    """

    top: int
    bottom: int

    @property
    def height(self) -> int:
        return self.bottom - self.top


def _horizontal_edge_profile(frame: np.ndarray) -> np.ndarray:
    """Per-row strength of horizontal edges, as a fraction of the frame width.

    A vertical Sobel responds to horizontal discontinuities. Thresholding it
    before the row sum is what makes the number mean "how much of this row is
    an edge" rather than "how much gradient is in this row" - a few very strong
    pixels then cannot outvote a long faint line, which is the wrong way round
    for finding shelves.
    """
    grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    grey = cv2.GaussianBlur(grey, (5, 5), 0)

    sobel_y = cv2.Sobel(grey, cv2.CV_32F, 0, 1, ksize=3)
    magnitude = np.abs(sobel_y)
    if magnitude.max() <= 0:
        return np.zeros(grey.shape[0], dtype=np.float32)

    # A pixel is "on an edge" if it is in the top of this frame's own gradient
    # range. Per-frame, so exposure does not decide the shelf count.
    strong = magnitude > (0.4 * magnitude.max())
    return strong.sum(axis=1).astype(np.float32) / grey.shape[1]


def shelf_edge_rows(frame: np.ndarray) -> List[int]:
    """Pixel rows holding a shelf edge, top to bottom.

    Empty when the frame has none. That is the important case: a blank wall, a
    frame of somebody's coat, a shot of the floor. Returning a best guess there
    would put a shelf in a planogram that nothing was ever filmed on.

    Span is the only test, and it is worth being plain about what that does not
    cover: a faint horizontal texture running the full width of the frame -
    brickwork, a slatted wall, blinds - reads as shelves, because
    `_horizontal_edge_profile` thresholds against the frame's own gradient range
    and a full-width scratch is then a full-width edge. Measured: 183-grey rows
    every 24 rows on 190-grey backing - a seven-value texture - return 20 shelf
    edges on a 480-row frame, 24 being just above `MERGE_WITHIN_PX`. Contrast
    cannot be the discriminator here without also losing a real lip
    photographed flatly, which is why it is not one; `pipeline.py` writes the band
    count into its notes so a reading like that is visible rather than implied.
    """
    profile = _horizontal_edge_profile(frame)
    if profile.size == 0:
        return []

    # Long enough to be a shelf rather than a label.
    candidates = np.flatnonzero(profile >= MIN_SPAN)
    if candidates.size == 0:
        return []

    return _merge_runs(candidates.tolist(), profile)


def _merge_runs(rows: Sequence[int], profile: np.ndarray) -> List[int]:
    """Collapse each cluster of adjacent rows to its strongest row.

    A shelf lip is several pixels thick and has a top and a bottom edge, so one
    shelf produces a run of responding rows and often two runs a few pixels
    apart. Both collapse to one shelf here; reporting each edge would double
    the shelf count and halve every band.
    """
    merged: List[int] = []
    run: List[int] = [rows[0]]

    for row in rows[1:]:
        if row - run[-1] <= MERGE_WITHIN_PX:
            run.append(row)
        else:
            merged.append(max(run, key=lambda r: profile[r]))
            run = [row]
    merged.append(max(run, key=lambda r: profile[r]))
    return merged


def shelf_bands(frame: np.ndarray) -> List[Band]:
    """The product-holding spaces in a frame, top to bottom.

    One band above the topmost shelf edge (the products standing on the highest
    shelf are *above* its front lip in the image) and one between each
    consecutive pair. The space below the lowest edge is deliberately not a
    band: in a front-on aisle shot it is floor.
    """
    edges = shelf_edge_rows(frame)
    if not edges:
        return []

    edges = sorted(edges)
    boundaries = [0] + edges
    bands = [
        Band(top=int(boundaries[i]), bottom=int(boundaries[i + 1]))
        for i in range(len(boundaries) - 1)
    ]
    return [band for band in bands if band.height >= MIN_BAND_PX]


def cluster_1d(values: Sequence[float], k: int) -> List[float]:
    """1-D k-means on `values`, returning cluster centres in ascending order.

    PLAN S20 asks for this by name, to collapse the same shelf seen at slightly
    different rows across frames into one shelf.

    Deterministic: initialised from evenly spaced quantiles of the sorted input
    rather than at random, so the same clip always produces the same planogram.
    A committed planogram that changed between runs would make every downstream
    number unreproducible.

    **Never invents a cluster to reach `k`.** With fewer distinct values than
    `k`, the values themselves are returned. An empty cluster has no centre,
    and a fabricated one would become a shelf nobody filmed.
    """
    if k <= 0:
        raise ValueError(f"k must be at least 1, got {k}")

    points = sorted(float(value) for value in values)
    if not points:
        return []
    if len(points) <= k:
        return points

    # Evenly spaced order statistics: stable, and already sorted.
    centres = [points[round(i * (len(points) - 1) / (k - 1))] for i in range(k)]

    for _ in range(50):
        buckets: List[List[float]] = [[] for _ in centres]
        for point in points:
            nearest = min(range(len(centres)), key=lambda i: abs(point - centres[i]))
            buckets[nearest].append(point)

        moved = [sum(bucket) / len(bucket) for bucket in buckets if bucket]
        moved.sort()
        if moved == centres:
            break
        centres = moved

    return centres
