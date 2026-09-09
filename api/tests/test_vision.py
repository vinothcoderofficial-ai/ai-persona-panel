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


# ---------------------------------------------------------------------------
# The vocabulary an operator has to label a reading with
# ---------------------------------------------------------------------------
#
# `vision/planogram.py` writes every SKU as brand "unknown", category
# "unknown", price 0 - correctly, because a classical pipeline cannot read any
# of the four. The cost of that honesty is that the synthetic panel does
# nothing at all on a video store, and this was measured rather than assumed:
# running the four committed policies in `data/cache/policies/` against a
# nine-facing reading gives `path.stations_mean` 0.0 for loyalist, mission and
# switcher - they never take a single step, because `sim/simulator.py` keeps a
# shopper active only while `goals` is non-empty and every goal category is
# matched against the store's category list, which here holds the one string
# "unknown". Browser walks the bay (it is the one archetype allowed to shop
# without goals) and buys nothing, because a purchase needs `goal_match` too.
# After the same nine facings are given categories from those policies, all
# four walk and all four buy.
#
# So the fix is a person typing the eight rows they already have in their ERP,
# and for that the screen needs the vocabulary the personas actually shop. That
# vocabulary is a server-side fact - it is the union of `goal_categories` over
# the committed policy of every persona on disk - so it is reported here,
# beside the notes about what the camera could not see. It is the same
# sentence: this is what was not observed, and this is what a person would have
# to supply instead.


def test_the_reading_offers_the_categories_the_personas_actually_shop(
    client: TestClient, aisle_clip: bytes
) -> None:
    """A category outside this set is a category no persona will ever walk to."""
    offered = _post(client, aisle_clip).json()["shoppable_categories"]

    assert "chips" in offered
    assert "cola" in offered


def test_the_offered_categories_never_include_the_unknown_the_pipeline_wrote(
    client: TestClient, aisle_clip: bytes
) -> None:
    """"unknown" is what the camera failed to read, not something to choose."""
    assert "unknown" not in _post(client, aisle_clip).json()["shoppable_categories"]


def test_the_categories_are_read_off_the_personas_rather_than_hardcoded(
    client: TestClient,
    aisle_clip: bytes,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Add a persona who shops something new and it becomes choosable.

    A list written into this router would agree with the personas on the day it
    was typed and silently disagree after the next `make seed`. The endpoint
    reads `data/personas/` for who exists and `data/cache/policies/` for what
    each of them is going after, so the screen can never offer a category the
    simulator would ignore, nor hide one it would honour.
    """
    personas = tmp_path / "personas"
    policies = tmp_path / "policies"
    personas.mkdir()
    policies.mkdir()
    (personas / "gardener.json").write_text(
        '{"persona_id": "gardener", "archetype": "browser"}', encoding="utf-8"
    )
    (policies / "gardener_demo_aisle.json").write_text(
        '{"persona_id": "gardener", "goal_categories": ["compost", "seeds"]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(vision_router, "PERSONAS_DIR", personas)
    monkeypatch.setattr(vision_router, "POLICIES_DIR", policies)

    assert _post(client, aisle_clip).json()["shoppable_categories"] == [
        "compost",
        "seeds",
    ]


def test_a_persona_with_no_committed_policy_does_not_break_the_reading(
    client: TestClient,
    aisle_clip: bytes,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fresh checkout that has not run the policy step still uploads a clip.

    The vocabulary is a convenience on top of the reading. Losing it must cost
    the operator a dropdown, not the whole feature - so an empty list comes
    back and the 200 stands.
    """
    personas = tmp_path / "personas"
    personas.mkdir()
    (personas / "gardener.json").write_text(
        '{"persona_id": "gardener", "archetype": "browser"}', encoding="utf-8"
    )
    monkeypatch.setattr(vision_router, "PERSONAS_DIR", personas)
    monkeypatch.setattr(vision_router, "POLICIES_DIR", tmp_path / "policies")

    response = _post(client, aisle_clip)

    assert response.status_code == 200
    assert response.json()["shoppable_categories"] == []


def test_the_reading_still_says_it_was_not_saved(
    client: TestClient, aisle_clip: bytes
) -> None:
    """Offering the labelling vocabulary is not the same as saving anything.

    The endpoint gained a field about what an operator could type; it did not
    gain a write. `saved` stays false and the document stays out of the
    database until a person deliberately POSTs it to /planograms.
    """
    assert _post(client, aisle_clip).json()["saved"] is False


# ---------------------------------------------------------------------------
# Keeping a reading: the round trip `#/vision` performs
# ---------------------------------------------------------------------------
#
# The screen holds the reading, applies the operator's labels to it, and posts
# the result to the endpoints that already exist for storing a store. This
# router takes no part in that and gains no write - but nothing else in the
# suite proves that what the screen assembles is a document `/planograms` will
# actually accept, and a shape error there would surface as a 422 in front of
# an operator with a labelled shelf they cannot keep. So the round trip is
# asserted here, in Python, against the real validator: relabel, save, wrap in
# a zero-patch variant, resolve.


def _label(planogram: dict, planogram_id: str) -> dict:
    """What the operator does on `#/vision`, in one function.

    Only the fields a person typed are overwritten. The second SKU is left
    exactly as the camera left it, because that asymmetry is the whole point:
    a reader has to be able to tell the two apart in the stored document.
    """
    document = dict(planogram)
    document["planogram_id"] = planogram_id
    document["name"] = (
        "Aisle read from video, 1 of N products labelled by an operator - positions, "
        "sizes and colours measured from the clip; brand, category, price and "
        "promotion typed by hand"
    )
    skus = [dict(sku) for sku in document["skus"]]
    skus[0].update(
        {
            "name": skus[0]["name"].replace("unidentified", "operator-labelled"),
            "brand": "Crunch",
            "category": "chips",
            "price": 2.49,
            "promo": True,
        }
    )
    document["skus"] = skus
    return document


def test_a_labelled_reading_is_a_document_planograms_accepts(
    client: TestClient, aisle_clip: bytes
) -> None:
    """The shape `#/vision` assembles has to survive planogram.schema.json.

    `additionalProperties: false` runs the length of that schema, so there is
    nowhere to hang an "operator supplied" flag and the provenance has to live
    in fields the schema already has - the document name, and each SKU's name.
    This asserts the assembled document validates, which is what stops the save
    button 422-ing in front of somebody holding a labelled shelf.
    """
    reading = _post(client, aisle_clip).json()["planogram"]

    response = client.post(
        "/planograms", json=_label(reading, "video_aisle_20260101120000_ab12")
    )

    assert response.status_code == 201, response.text


def test_a_kept_reading_is_shoppable_through_a_variant_that_changes_nothing(
    client: TestClient, aisle_clip: bytes
) -> None:
    """The store route resolves variants, never planograms.

    A zero-patch variant is the shortest honest bridge from "this is what was
    read and labelled" to "shop it": what gets shopped is exactly the document
    that was saved, with nothing moved on top of it.
    """
    reading = _post(client, aisle_clip).json()["planogram"]
    planogram_id = "video_aisle_20260101120000_ab12"
    client.post("/planograms", json=_label(reading, planogram_id))

    created = client.post(
        "/variants",
        json={
            "variant_id": f"{planogram_id}_asread",
            "base_planogram_id": planogram_id,
            "name": "As read from video - nothing moved",
            "patches": [],
        },
    )
    assert created.status_code == 201, created.text

    resolved = client.get(f"/variants/{planogram_id}_asread/resolved").json()
    assert resolved["source"] == "video"
    assert resolved["skus"][0]["category"] == "chips"
    # The facing nobody described still says so, in the stored document, beside
    # the one that was labelled.
    assert resolved["skus"][1]["category"] == "unknown"
    assert resolved["skus"][1]["name"].startswith("unidentified")


def test_a_kept_reading_does_not_displace_the_hand_authored_store(
    client: TestClient, aisle_clip: bytes
) -> None:
    """`POST /planograms` upserts on the id. A video reading must never land on
    one that somebody measured by hand, which is why the screen generates a
    `video_`-prefixed, run-stamped id rather than reusing the pipeline's
    `video_aisle`."""
    reading = _post(client, aisle_clip).json()["planogram"]
    client.post("/planograms", json=_label(reading, "video_aisle_20260101120000_ab12"))

    listed = client.get("/planograms").json()

    assert "video_aisle_20260101120000_ab12" in listed
    assert "video_aisle" not in listed
