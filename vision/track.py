"""Agreeing across frames (S30).

PLAN S20 asks for "IoU 0.5 dedupe". One frame is a guess; the same facing found
in the same place across several frames is evidence, and the gap between those
two is what `confidence` in the emitted planogram has to mean if it is to mean
anything.

So this does two things that look like one:

* **dedupe** - a pack seen in twelve frames is one facing, not twelve;
* **corroborate** - how many frames agreed becomes part of that facing's
  confidence.

The second is why the module exists. Without it a single frame's misfire enters
the planogram with exactly the authority of a shelf that was visible
throughout, and nothing downstream - not the simulator, not the dashboard, not
a person reading `data/planograms/video_aisle.json` in six months - can tell
the two apart.

**Corroboration multiplies the per-frame evidence rather than replacing it.**
Fifty frames agreeing that something is barely there is still fifty frames of
barely, and a track that reached 1.0 purely by repetition would launder a weak
detection into a confident one.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence, Tuple

from vision.facings import Facing

# PLAN S20's threshold. Two boxes overlapping by at least this are the same
# facing seen twice.
IOU_MATCH = 0.5

# How many corroborating frames it takes for repetition to stop adding much.
# Three is deliberately low: at 2 fps that is a second and a half of the shelf
# staying where it is, which is all the confirmation a static shelf can offer.
CORROBORATION_SATURATES_AT = 3.0

Box = Tuple[float, float, float, float]


def iou(a: Box, b: Box) -> float:
    """Intersection over union of two (x0, top, x1, bottom) boxes.

    Boxes that merely touch return 0.0: two packs side by side share an edge
    and not an area, and treating that as overlap would collapse every facing
    on a shelf into one. A zero-area box overlaps nothing, so there is never a
    division by zero and never a NaN reaching a comparison.
    """
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b

    inter_w = min(ax1, bx1) - max(ax0, bx0)
    inter_h = min(ay1, by1) - max(ay0, by0)
    if inter_w <= 0 or inter_h <= 0:
        return 0.0

    intersection = inter_w * inter_h
    area_a = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    area_b = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    union = area_a + area_b - intersection
    if union <= 0:
        return 0.0
    return float(intersection / union)


@dataclass(frozen=True)
class Track:
    """One facing, as agreed across the frames it appeared in.

    `seen_in` is the count of frames that contained it, and it is reported in
    its own right rather than folded away into `confidence`: a reader of the
    planogram is entitled to know whether a slot rests on one frame or twenty.
    """

    x0: float
    x1: float
    top: float
    bottom: float
    color_lab: Tuple[float, float, float]
    confidence: float
    seen_in: int

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def box(self) -> Box:
        return (self.x0, self.top, self.x1, self.bottom)


class _Accumulator:
    """A track being built: the running mean of every facing matched into it."""

    def __init__(self, first: Facing) -> None:
        self.members: List[Facing] = [first]

    @property
    def box(self) -> Box:
        return (self.x0, self.top, self.x1, self.bottom)

    def _mean(self, attribute: str) -> float:
        return sum(getattr(m, attribute) for m in self.members) / len(self.members)

    @property
    def x0(self) -> float:
        return self._mean("x0")

    @property
    def x1(self) -> float:
        return self._mean("x1")

    @property
    def top(self) -> float:
        return self._mean("top")

    @property
    def bottom(self) -> float:
        return self._mean("bottom")

    def finish(self) -> Track:
        seen = len(self.members)
        per_frame = sum(m.confidence for m in self.members) / seen
        # Multiplied, not replaced: fifty frames of "barely there" stays barely
        # there. Corroboration can only close the gap to the per-frame reading,
        # never exceed it.
        corroboration = min(1.0, seen / CORROBORATION_SATURATES_AT)
        confidence = per_frame * (0.5 + 0.5 * corroboration)

        lab = [
            sum(m.color_lab[i] for m in self.members) / seen for i in range(3)
        ]
        return Track(
            x0=self.x0,
            x1=self.x1,
            top=self.top,
            bottom=self.bottom,
            color_lab=(lab[0], lab[1], lab[2]),
            confidence=float(max(0.0, min(1.0, confidence))),
            seen_in=seen,
        )


def merge_across_frames(frames: Sequence[Sequence[Facing]]) -> List[Track]:
    """Facings from many frames, deduped into tracks, left to right.

    Greedy nearest-match on IoU, in frame order. A facing that matches no
    existing track starts one - **dropped evidence is worse than weak
    evidence**, so a pack glimpsed in a single frame still reaches the
    planogram, with `seen_in: 1` and a confidence that says so.

    Deterministic: frames are walked in order and, within a frame, facings in
    the order they were found, which `vision/facings.py` guarantees is left to
    right. A committed planogram that changed between runs of the same clip
    would make every downstream number unreproducible.
    """
    accumulators: List[_Accumulator] = []

    for frame in frames:
        for facing in frame:
            best_index = -1
            best_score = IOU_MATCH
            for index, accumulator in enumerate(accumulators):
                score = iou(facing.box, accumulator.box)
                if score >= best_score:
                    best_score = score
                    best_index = index

            if best_index >= 0:
                accumulators[best_index].members.append(facing)
            else:
                accumulators.append(_Accumulator(facing))

    return sorted((a.finish() for a in accumulators), key=lambda t: (t.x0, t.x1))
