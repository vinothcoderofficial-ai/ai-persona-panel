"""Reading frames out of a video (S30).

PLAN S20 specified `extract_frames` via ffmpeg at 2 fps. ffmpeg is not on this
machine's PATH and OpenCV decodes video directly, so the dependency is dropped
rather than added: one fewer thing to install for a step that is a loop over
`VideoCapture`.

Sampling rather than decoding everything is the point. A 20-second clip at 30
fps is 600 frames of a shelf that is not moving; 2 fps is 40, which is plenty
of corroboration for `vision/track.py` and forty times less work. The sampling
is by frame index rather than by timestamp seek, because seeking is
unreliable across containers and a wrong seek silently returns the wrong frame.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import cv2
import numpy as np

DEFAULT_FPS = 2.0

# A clip long enough to need more than this is not a shelf shot; the cap stops
# a mis-pointed pipeline from spending minutes on an hour of footage.
DEFAULT_MAX_FRAMES = 120


class VideoUnreadable(Exception):
    """The file is not there, or is not a video this build of OpenCV decodes."""


@dataclass(frozen=True)
class Frame:
    """One sampled frame: its index in the source, its time, and its pixels."""

    index: int
    time_s: float
    image: np.ndarray


def extract_frames(
    path: str | Path,
    *,
    fps: float = DEFAULT_FPS,
    max_frames: Optional[int] = DEFAULT_MAX_FRAMES,
) -> List[Frame]:
    """Sample `path` at roughly `fps` frames per second.

    Raises `VideoUnreadable` for a missing file or a container this build
    cannot open - distinguished from "a video with no frames in it", which
    returns an empty list, because the fixes are different and the caller has
    to be able to say which happened.
    """
    source = Path(path)
    if not source.exists():
        raise VideoUnreadable(f"no such video: {source}")

    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise VideoUnreadable(
            f"OpenCV could not open {source.name}. It may be a container this "
            "build does not decode; try re-encoding to H.264 in an .mp4."
        )

    try:
        source_fps = capture.get(cv2.CAP_PROP_FPS)
        # A container that does not report its rate reports 0 or NaN. Treating
        # that as 1 fps would silently take every frame of a long clip.
        if not source_fps or source_fps != source_fps or source_fps <= 0:
            source_fps = 30.0

        step = max(1, int(round(source_fps / max(fps, 1e-6))))

        frames: List[Frame] = []
        index = 0
        while True:
            ok, image = capture.read()
            if not ok:
                break
            if index % step == 0:
                frames.append(
                    Frame(index=index, time_s=index / source_fps, image=image)
                )
                if max_frames is not None and len(frames) >= max_frames:
                    break
            index += 1
        return frames
    finally:
        capture.release()
