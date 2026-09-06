"""POST /vision/planogram -- upload a clip, get a shelf (S30).

`vision/pipeline.py` reads a video into a planogram at a terminal. This is the
same pipeline behind an upload, so "input video, get a 3D store" is something a
person can do rather than something the repository merely contains.

What the endpoint has to preserve, and what these tests are for: **everything
the pipeline refuses to claim.** It reads geometry and colour and nothing else,
so the response carries the pipeline's own notes about what was not observed,
and a clip with no shelves in it comes back as a refusal rather than as an
empty store. A vision endpoint that returned a plausible planogram for a video
of a wall would be the worst failure available here - once it is JSON, nothing
downstream can tell it from a real shelf.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from api.app.routers import vision as vision_router

WIDTH = 640
HEIGHT = 480
SHELF_EDGES = (150, 280, 410)
PRODUCTS = [
    [(200, 70, 50), (60, 60, 210), (70, 190, 80)],
    [(40, 200, 220), (190, 80, 190), (90, 90, 90)],
    [(30, 120, 220), (210, 190, 60), (120, 40, 160)],
]


def _aisle_frame(jitter: int = 0) -> np.ndarray:
    frame = np.full((HEIGHT, WIDTH, 3), 175, dtype=np.uint8)
    band_tops = [20, SHELF_EDGES[0], SHELF_EDGES[1]]
    for row, bottom, colours in zip(band_tops, SHELF_EDGES, PRODUCTS):
        for index, colour in enumerate(colours):
            x0 = 30 + index * 200 + jitter
            frame[row + 8 : bottom - 4, x0 : x0 + 170] = colour
    for edge in SHELF_EDGES:
        frame[edge : edge + 5, :, :] = 35
    return frame


def _write_clip(path: Path, frame_of) -> Path:
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10.0, (WIDTH, HEIGHT)
    )
    assert writer.isOpened()
    try:
        for n in range(30):
            writer.write(frame_of(n))
    finally:
        writer.release()
    return path


@pytest.fixture(name="aisle_clip")
def aisle_clip_fixture(tmp_path: Path) -> bytes:
    return _write_clip(tmp_path / "aisle.mp4", lambda n: _aisle_frame(n % 2)).read_bytes()


@pytest.fixture(name="blank_clip")
def blank_clip_fixture(tmp_path: Path) -> bytes:
    blank = np.full((HEIGHT, WIDTH, 3), 175, dtype=np.uint8)
    return _write_clip(tmp_path / "blank.mp4", lambda _n: blank).read_bytes()


def _post(client: TestClient, payload: bytes, filename: str = "aisle.mp4"):
    return client.post(
        "/vision/planogram",
        files={"video": (filename, payload, "video/mp4")},
    )


def test_reads_a_clip_into_a_planogram(client: TestClient, aisle_clip: bytes) -> None:
    response = _post(client, aisle_clip)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["planogram"]["source"] == "video"
    assert len(body["planogram"]["bays"][0]["shelves"]) == len(PRODUCTS)


def test_reports_how_many_frames_it_read(client: TestClient, aisle_clip: bytes) -> None:
    body = _post(client, aisle_clip).json()

    assert body["frames_sampled"] > 0


def test_carries_the_pipeline_s_own_notes(client: TestClient, aisle_clip: bytes) -> None:
    """Including what it could not observe. The notes are the honest half of
    this feature and must not be dropped at the HTTP boundary."""
    notes = " ".join(_post(client, aisle_clip).json()["notes"]).lower()

    assert "not identified" in notes
    assert "ad slots" in notes or "promotional signs" in notes


def test_every_slot_carries_the_confidence_it_earned(
    client: TestClient, aisle_clip: bytes
) -> None:
    body = _post(client, aisle_clip).json()

    for shelf in body["planogram"]["bays"][0]["shelves"]:
        for slot in shelf["slots"]:
            assert 0.0 < slot["confidence"] <= 1.0


def test_the_planogram_is_not_stored_by_reading_it(
    client: TestClient, aisle_clip: bytes
) -> None:
    """Reading a video is not committing a store. The document comes back for a
    person to look at; saving it is a separate, deliberate act - otherwise an
    upload would quietly add a planogram the simulator could then be run
    against."""
    _post(client, aisle_clip)

    listed = client.get("/planograms").json()
    assert all(entry != "video_aisle" for entry in listed)


def test_a_clip_with_no_shelves_is_refused(client: TestClient, blank_clip: bytes) -> None:
    """The most important refusal in the feature. A wall must not become a
    store."""
    response = _post(client, blank_clip)

    assert response.status_code == 422
    assert "shelf" in response.json()["detail"].lower()


def test_the_refusal_says_what_kind_of_shot_is_needed(
    client: TestClient, blank_clip: bytes
) -> None:
    detail = _post(client, blank_clip).json()["detail"]

    assert "front-on" in detail


def test_a_file_that_is_not_a_video_is_refused(client: TestClient) -> None:
    response = _post(client, b"this is not an mp4", filename="notes.txt")

    assert response.status_code == 422
    assert response.json()["detail"]


def test_an_empty_upload_is_refused(client: TestClient) -> None:
    response = _post(client, b"")

    assert response.status_code == 422


def test_reading_the_same_clip_twice_gives_the_same_planogram(
    client: TestClient, aisle_clip: bytes
) -> None:
    first = _post(client, aisle_clip).json()["planogram"]
    second = _post(client, aisle_clip).json()["planogram"]

    assert first == second


def test_reading_a_long_clip_does_not_freeze_the_rest_of_the_api(
    client: TestClient, aisle_clip: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A minute of video takes ~13 seconds to read. The server must stay up.

    The four-second fixture hid this: eight sampled frames come back fast
    enough that nobody notices what the handler is doing. A sixty-second clip
    samples the full 120-frame cap, and OpenCV decoding plus per-band colour
    segmentation is CPU-bound, synchronous work. Run directly inside an
    `async def`, it holds the event loop for its whole duration, which means
    that for those thirteen seconds this API serves nobody: not a shopper
    loading the store, not a live session flushing gaze over the websocket,
    not the spectator view. One person trying the vision demo would stall a
    measurement session in the next room, and the gaze data lost while the
    loop is blocked is not recoverable.

    The pipeline is deliberately left slow here and blocked on an event
    instead, so this asserts the handler's concurrency and not the speed of
    the CV code - which is allowed to get slower.
    """
    started = threading.Event()
    release = threading.Event()
    real_run = vision_router.run

    def slow_run(path):
        started.set()
        release.wait(timeout=20)
        return real_run(path)

    monkeypatch.setattr(vision_router, "run", slow_run)

    with ThreadPoolExecutor(max_workers=2) as pool:
        upload = pool.submit(_post, client, aisle_clip)
        assert started.wait(timeout=10), "the upload never reached the pipeline"

        # Mid-read, a shopper asks for the store. This is the request that a
        # blocked event loop never gets to.
        store = pool.submit(client.get, "/planograms")
        try:
            response = store.result(timeout=5)
        except FuturesTimeout:
            release.set()
            upload.result(timeout=30)
            pytest.fail(
                "GET /planograms was not served while a clip was being read: "
                "the pipeline is holding the event loop"
            )

        release.set()
        assert response.status_code == 200
        assert upload.result(timeout=30).status_code == 200
