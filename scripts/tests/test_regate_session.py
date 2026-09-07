"""Tests for scripts/regate_session.py.

This script rewrites a verdict on a session that has already been recorded,
which is the most dangerous thing in this repository short of editing a
prediction lock. The tests are mostly about what it **refuses** to do.

The context, because it is the whole reason for the care: the session gate used
to reject on `duration_s >= 45`. That floor was replaced (see METHODOLOGY.md
2.3) because it preferentially discarded the `mission` archetype - a shopper
with a list finishes in half a minute - and `mission` is one of the four
personas the panel exists to validate. The session that exposed the flaw is
also the session the new rule would accept, so re-admitting it is applying a
rule to rescue the very datapoint that motivated the rule. That can be
defensible, but only if it is impossible to do quietly. Hence: it runs at a
terminal and never through the API, it refuses anything that is not the
specific historical correction it was written for, and it stamps a `regated`
block into the session so the disclosure travels with the evidence rather than
living only in a document somebody may not read.
"""
import json
import pathlib
import sys

import pytest
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from api.app.db import EventRecord, SessionRecord  # noqa: E402
from scripts import regate_session  # noqa: E402


def session_doc(session_id="s-001", **over):
    """A session rejected by the old duration floor, as one is really stored."""
    doc = {
        "session_id": session_id,
        "variant_id": "A",
        "consent": True,
        "started_at": "2026-09-05T10:32:41.000Z",
        "ended_at": "2026-09-05T10:33:10.000Z",
        "screen_w": 1920,
        "screen_h": 1080,
        "mode": "cursor_only",
        "calibration_error_px": None,
        "intake": {"has_list": True, "same_brand": True, "hurry": True},
        "archetype_label": "mission",
        "prediction_id": "pred-001",
        "accepted": False,
        "reject_reason": "too_short",
        "quality": {"fixation_coverage": 0.0, "stations_visited": 3, "duration_s": 28.9},
    }
    doc.update(over)
    return doc


def dwell(t_ms, slot_id, station="B1", dur_ms=400):
    return {
        "t_ms": t_ms,
        "type": "cursor_dwell",
        "station_id": station,
        "payload": {"slot_id": slot_id, "dur_ms": dur_ms},
    }


def fixation(t_ms, slot_id, station="B1", dur_ms=400):
    return {
        "t_ms": t_ms,
        "type": "fixation",
        "station_id": station,
        "payload": {"x": 1, "y": 2, "dur_ms": dur_ms, "slot_id": slot_id, "shelf_id": "B1S1"},
    }


def interaction(t_ms, slot_id, station="B1"):
    return {
        "t_ms": t_ms,
        "type": "pickup",
        "station_id": station,
        "payload": {"slot_id": slot_id, "sku_id": "SKU_001"},
    }


SIX_SLOTS = [
    dwell(600, "B1S3P1"),
    dwell(1_100, "B1S4P2"),
    dwell(1_700, "B2S2P2", station="B2"),
    dwell(2_400, "B2S4P2", station="B2"),
    dwell(3_000, "B3S4P2", station="B3"),
    dwell(3_600, "B3S5P1", station="B3"),
    interaction(4_000, "B1S3P1"),
]


@pytest.fixture(name="engine")
def engine_fixture():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    return engine


def store(engine, session, events=()):
    with Session(engine) as db:
        db.add(SessionRecord(session_id=session["session_id"], data=json.dumps(session)))
        for event in events:
            db.add(EventRecord(session_id=session["session_id"], data=json.dumps(event)))
        db.commit()


def read(engine, session_id="s-001"):
    with Session(engine) as db:
        row = db.exec(
            select(SessionRecord).where(SessionRecord.session_id == session_id)
        ).one()
        return json.loads(row.data)


# --- counting the looking channel -----------------------------------------


def test_counts_distinct_slots_dwelt_on_in_a_cursor_session() -> None:
    assert regate_session.slots_observed(SIX_SLOTS, "cursor_only") == 6


def test_counts_a_slot_once_however_often_it_was_looked_at() -> None:
    repeated = [dwell(100, "B1S1P1"), dwell(600, "B1S1P1"), dwell(1_200, "B1S1P1")]
    assert regate_session.slots_observed(repeated, "cursor_only") == 1


def test_skips_a_look_at_bare_shelf() -> None:
    # fusion.py: a fixation with slot_id null belongs to no slot and is
    # skipped. A slot the formula will never credit is not evidence.
    assert regate_session.slots_observed([fixation(100, None)], "webcam") == 0


def test_ignores_fixations_in_a_cursor_only_session() -> None:
    # fusion.py's _MODE_WEIGHTS weights fixation 0 in cursor_only. This mirrors
    # web/src/capture/SessionGate.ts, and the two must agree - see the module
    # docstring in the script about that duplication.
    mixed = [dwell(100, "B1S1P1"), fixation(600, "B1S2P1"), fixation(900, "B1S3P1")]
    assert regate_session.slots_observed(mixed, "cursor_only") == 1
    assert regate_session.slots_observed(mixed, "webcam") == 3


def test_does_not_count_interactions_as_looking() -> None:
    # A pickup is a thing done, not a thing looked at, and it already satisfies
    # its own rule. Counting it here would let one action clear two criteria.
    assert regate_session.slots_observed([interaction(100, "B1S1P1")], "cursor_only") == 0


# --- what it refuses ------------------------------------------------------


def test_refuses_a_session_that_was_accepted(engine) -> None:
    store(engine, session_doc(accepted=True, reject_reason=None), SIX_SLOTS)

    with pytest.raises(regate_session.RefuseToRegate) as excinfo:
        regate_session.regate(engine, "s-001")

    assert "already accepted" in str(excinfo.value)


def test_refuses_a_session_rejected_for_any_reason_but_the_duration_floor(engine) -> None:
    """The only correction this script exists to make.

    A session rejected as `one_station` or `no_consent` was rejected by a rule
    that still stands, and re-running the gate over it would be re-litigating a
    verdict rather than correcting one that a since-removed rule produced.
    """
    store(engine, session_doc(reject_reason="one_station"), SIX_SLOTS)

    with pytest.raises(regate_session.RefuseToRegate) as excinfo:
        regate_session.regate(engine, "s-001")

    assert "one_station" in str(excinfo.value)
    assert "too_short" in str(excinfo.value)


def test_refuses_a_session_that_the_new_rule_also_rejects(engine) -> None:
    """Removing the floor does not make every short session good.

    If the new rule rejects it too, the honest outcome is that it stays
    rejected - and the script will not quietly rewrite the reason, because the
    stored verdict is the one the browser actually reached at checkout.
    """
    store(engine, session_doc(), [dwell(100, "B1S1P1"), dwell(600, "B1S2P1")])

    with pytest.raises(regate_session.RefuseToRegate) as excinfo:
        regate_session.regate(engine, "s-001")

    assert "2" in str(excinfo.value)
    assert read(engine)["accepted"] is False
    assert read(engine)["reject_reason"] == "too_short"


def test_refuses_a_session_that_was_already_regated(engine) -> None:
    store(engine, session_doc(), SIX_SLOTS)
    regate_session.regate(engine, "s-001")

    with pytest.raises(regate_session.RefuseToRegate) as excinfo:
        regate_session.regate(engine, "s-001")

    assert "already" in str(excinfo.value)


def test_refuses_an_unknown_session(engine) -> None:
    with pytest.raises(regate_session.RefuseToRegate):
        regate_session.regate(engine, "nobody")


# --- what it does ---------------------------------------------------------


def test_accepts_the_session_and_clears_the_reason(engine) -> None:
    store(engine, session_doc(), SIX_SLOTS)

    regate_session.regate(engine, "s-001")

    stored = read(engine)
    assert stored["accepted"] is True
    assert stored["reject_reason"] is None


def test_backfills_slots_observed_into_the_quality_block(engine) -> None:
    # The number the new rule decided on. Sessions finished before the rule
    # existed have no such field, and a quality block that omits the deciding
    # number makes the verdict undiagnosable.
    store(engine, session_doc(), SIX_SLOTS)

    regate_session.regate(engine, "s-001")

    assert read(engine)["quality"]["slots_observed"] == 6


def test_leaves_every_other_quality_number_alone(engine) -> None:
    store(engine, session_doc(), SIX_SLOTS)

    regate_session.regate(engine, "s-001")

    quality = read(engine)["quality"]
    assert quality["duration_s"] == 28.9
    assert quality["stations_visited"] == 3
    assert quality["fixation_coverage"] == 0.0


def test_never_touches_the_events(engine) -> None:
    """Re-gating changes a verdict, never the record it was reached from."""
    store(engine, session_doc(), SIX_SLOTS)

    regate_session.regate(engine, "s-001")

    with Session(engine) as db:
        rows = db.exec(
            select(EventRecord).where(EventRecord.session_id == "s-001")
        ).all()
    assert [json.loads(row.data) for row in rows] == SIX_SLOTS


def test_stamps_a_disclosure_the_evidence_carries(engine) -> None:
    """The point of the whole script.

    METHODOLOGY.md records why the rule changed, but a reader inspecting
    data/sessions/anon/ sees session files, and an accepted session with no
    trace of having been re-admitted is a session that looks like it passed the
    gate on the day it was collected.
    """
    store(engine, session_doc(), SIX_SLOTS)

    regate_session.regate(engine, "s-001", rule_commit="1d97717")

    regated = read(engine)["regated"]
    assert regated["from_accepted"] is False
    assert regated["from_reject_reason"] == "too_short"
    assert regated["rule_commit"] == "1d97717"
    assert regated["at"].endswith("Z")
    assert "duration" in regated["note"].lower()


def test_the_result_still_validates_against_the_session_schema(engine) -> None:
    from api.app.db import get_validator

    store(engine, session_doc(), SIX_SLOTS)
    regate_session.regate(engine, "s-001")

    errors = sorted(get_validator("session.schema.json").iter_errors(read(engine)), key=str)
    assert errors == []


def test_a_dry_run_reports_without_writing(engine) -> None:
    store(engine, session_doc(), SIX_SLOTS)

    outcome = regate_session.regate(engine, "s-001", dry_run=True)

    assert outcome.slots_observed == 6
    assert read(engine)["accepted"] is False
    assert "regated" not in read(engine)
