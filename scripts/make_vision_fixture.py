"""Render a short aisle clip from the seed planogram, for trying the vision pipeline.

    python scripts/make_vision_fixture.py                    # -> data/vision/demo_aisle.mp4
    python scripts/make_vision_fixture.py --out /tmp/x.mp4 --seconds 6

There is no aisle footage in this repository and none can be invented, so
without this there is nothing to point `#/vision` or `python -m vision.pipeline`
at on a machine with no camera. This renders a bay from
`data/planograms/demo_aisle.json` - its real shelves, its real slots, and each
product's real `color_lab` - and writes it as a video.

**This is a rendering, not footage, and the difference matters.** A pipeline
demonstrated only on a picture the same repository drew is a much weaker claim
than one demonstrated on a photograph of a shelf: the render has even lighting,
no perspective, no occlusion, no motion blur and no shopper's arm in the way,
and every one of those is something a real clip has and this pipeline may
struggle with. What it does show is that the stages compose - decode, shelf
edges, facings, IoU agreement, a schema-valid document - end to end on a real
file.

The output is gitignored (`data/vision/*.mp4`). It is regenerable in a second
and committing a video to prove a pipeline works on its own drawing would be
the wrong kind of evidence to keep.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any, Dict, List, Tuple

import cv2
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vision.frames import DEFAULT_FPS, DEFAULT_MAX_FRAMES

PLANOGRAM = ROOT / "data" / "planograms" / "demo_aisle.json"
DEFAULT_OUT = ROOT / "data" / "vision" / "demo_aisle.mp4"

WIDTH = 960
HEIGHT = 720
FPS = 10
BACKING = (168, 168, 168)
SHELF_LIP = (38, 38, 38)
LIP_PX = 6


def lab_to_bgr(lab: List[float]) -> Tuple[int, int, int]:
    """One CIE Lab triple to a BGR pixel, through OpenCV's own conversion.

    Round-tripped rather than hand-rolled so the colour the video shows is the
    colour the planogram records: the pipeline reads it back out in Lab, and a
    bespoke conversion here would make the fixture disagree with its own source
    for reasons that had nothing to do with the pipeline.
    """
    scaled = np.zeros((1, 1, 3), dtype=np.float32)
    scaled[0, 0, 0] = lab[0] * 255.0 / 100.0
    scaled[0, 0, 1] = lab[1] + 128.0
    scaled[0, 0, 2] = lab[2] + 128.0
    bgr = cv2.cvtColor(scaled.astype(np.uint8), cv2.COLOR_LAB2BGR)
    return tuple(int(v) for v in bgr[0, 0])


def render_bay(planogram: Dict[str, Any], bay_index: int, jitter: int) -> np.ndarray:
    """One frame of one bay: shelf lips, and a coloured pack in each full slot."""
    frame = np.full((HEIGHT, WIDTH, 3), BACKING, dtype=np.uint8)
    bay = planogram["bays"][bay_index]
    colours = {sku["sku_id"]: lab_to_bgr(sku["color_lab"]) for sku in planogram["skus"]}

    shelves = bay["shelves"]
    band = HEIGHT // len(shelves)

    for index, shelf in enumerate(shelves):
        top = index * band
        bottom = top + band

        for slot in shelf["slots"]:
            if slot["sku_id"] is None:
                # An empty slot is drawn as backing, which is what the pipeline
                # has to read as an empty shelf position rather than a product.
                continue
            x0 = int(slot["x_m"] / bay["width_m"] * WIDTH) + jitter
            width = max(20, int(slot["width_m"] / bay["width_m"] * WIDTH) - 10)
            frame[top + 10 : bottom - LIP_PX - 6, x0 : x0 + width] = colours[slot["sku_id"]]

        # The shelf's front lip: the long horizontal edge the pipeline finds.
        frame[bottom - LIP_PX : bottom, :, :] = SHELF_LIP

    return frame


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--bay", type=int, default=0, help="index into planogram.bays")
    parser.add_argument("--seconds", type=float, default=4.0)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    planogram = json.loads(PLANOGRAM.read_text(encoding="utf-8"))

    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    writer = cv2.VideoWriter(
        str(out), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (WIDTH, HEIGHT)
    )
    if not writer.isOpened():
        print(f"could not open a video writer for {out}", file=sys.stderr)
        return 1

    frames = int(FPS * args.seconds)
    try:
        for n in range(frames):
            # A pixel of handheld drift, so the IoU agreement stage is actually
            # exercised rather than handed identical boxes every frame.
            writer.write(render_bay(planogram, args.bay, jitter=n % 3 - 1))
    finally:
        writer.release()

    print(f"wrote {out} ({frames} frames, {WIDTH}x{HEIGHT}, {FPS} fps)")

    # How much of it the pipeline will actually look at. Worth saying, because
    # "I made it longer" is the obvious thing to try when a reading looks thin,
    # and past the cap it changes nothing: a 60-second clip and a five-minute
    # one are sampled identically. Derived from the pipeline's own constants
    # rather than restated, so this cannot drift away from the truth.
    step = max(1, int(round(FPS / max(DEFAULT_FPS, 1e-6))))
    sampled = min(DEFAULT_MAX_FRAMES, len(range(0, frames, step)))
    print(
        f"The pipeline will sample {sampled} of them at {DEFAULT_FPS:g} fps "
        f"(cap {DEFAULT_MAX_FRAMES}), taking a few seconds per hundred frames."
    )
    print("This is a rendering of the seed planogram, not footage of a real shelf.")
    print(f"Try it:  python -m vision.pipeline --video {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
