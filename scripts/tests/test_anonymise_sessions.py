"""Tests for scripts/anonymise_sessions.py (S21).

The binding contract is not a shape this module invents: it is that
`scripts/eval.py:load_sessions` reads what this writes with zero failures.
eval.py treats a malformed session file as a build failure rather than a
skipped file, so anything this emits that eval cannot read stops the build.

The privacy rule is the other half. A session whose shopper did not consent
must never reach `data/sessions/anon/`, and no field outside
schemas/session.schema.json may be carried over from the live database.
"""
import json
import pathlib
import sys

import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from api.app.db import EventRecord, SessionRecord  # noqa: E402
from scripts import anonymise_sessions  # noqa: E402
from scripts import eval as eval_script  # noqa: E402


def session_doc(session_id="s-001", consent=True, **over):
    doc = {
        "session_id": session_id,
        "variant_id": "A",
        "consent": consent,
        "started_at": "2026-09-05T10:32:41.000Z",
        "ended_at": "2026-09-05T10:33:58.000Z",
        "screen_w": 1920,
        "screen_h": 1080,
        "mode": "cursor_only",
        "calibration_error_px": None,
        "intake": {"has_list": True, "same_brand": False, "hurry": False},
        "archetype_label": "mission",
        "prediction_id": "pred-001",
        "accepted": True,
        "reject_reason": None,
        "quality": {"fixation_coverage": 0.0, "stations_visited": 3, "duration_s": 77.0},
    }
    doc.update(over)
    return doc


def event_doc(t_ms=100, type_="cursor_dwell", station="B1"):
    payloads = {
        "cursor_dwell": {"slot_id": "B1S3P1", "dur_ms": 400},
        "pickup": {"slot_id": "B1S3P1", "sku_id": "SKU_001"},
    }
    return {"t_ms": t_ms, "type": type_, "station_id": station,
            "payload": dict(payloads[type_])}


@pytest.fixture(name="engine")
def engine_fixture():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    return engine


def store(engine, session, events=()):
    with Session(engine) as db:
        db.add(SessionRecord(session_id=session["session_id"], data=json.dumps(session)))
        for event in events:
            db.add(EventRecord(session_id=session["session_id"], data=json.dumps(event)))
        db.commit()


# --- the contract with eval.py -------------------------------------------

def test_output_is_readable_by_eval_load_sessions(engine, tmp_path):
    store(engine, session_doc(), [event_doc(100), event_doc(900, "pickup")])
    anonymise_sessions.export(engine, tmp_path)

    loaded, failures = eval_script.load_sessions(tmp_path)
    assert failures == []
    assert len(loaded) == 1
    assert loaded[0].session_id == "s-001"
    assert len(loaded[0].events) == 2


def test_one_file_per_session_named_by_session_id(engine, tmp_path):
    store(engine, session_doc("s-001"))
    store(engine, session_doc("s-002"))
    anonymise_sessions.export(engine, tmp_path)
    assert sorted(p.name for p in tmp_path.glob("*.json")) == ["s-001.json", "s-002.json"]


def test_events_are_written_in_t_ms_order(engine, tmp_path):
    store(engine, session_doc(), [event_doc(900, "pickup"), event_doc(100)])
    anonymise_sessions.export(engine, tmp_path)
    loaded, _ = eval_script.load_sessions(tmp_path)
    assert [e["t_ms"] for e in loaded[0].events] == [100, 900]


# --- privacy --------------------------------------------------------------

def test_a_session_without_consent_is_never_exported(engine, tmp_path):
    store(engine, session_doc("s-yes", consent=True))
    store(engine, session_doc("s-no", consent=False, accepted=False,
                              reject_reason="no_consent"))
    report = anonymise_sessions.export(engine, tmp_path)

    assert [p.name for p in tmp_path.glob("*.json")] == ["s-yes.json"]
    assert report.withheld_no_consent == 1


def test_fields_outside_the_schema_are_dropped(engine, tmp_path):
    """The live row is raw JSON; the anon corpus is schema-shaped, nothing more."""
    doc = session_doc()
    doc["operator_note"] = "Priya from the 3rd floor, sat at the window desk"
    doc["ip"] = "10.1.2.3"
    store(engine, doc)
    anonymise_sessions.export(engine, tmp_path)

    written = json.loads((tmp_path / "s-001.json").read_text(encoding="utf-8"))
    assert "operator_note" not in written["session"]
    assert "ip" not in written["session"]
    assert "Priya" not in (tmp_path / "s-001.json").read_text(encoding="utf-8")


def test_a_session_id_that_is_not_opaque_is_refused(engine, tmp_path):
    """A session id is a filename and a lock key; a typed-in name would leak."""
    store(engine, session_doc("priya.sharma@example.com"))
    with pytest.raises(anonymise_sessions.NotAnonymous):
        anonymise_sessions.export(engine, tmp_path)


# --- integrity ------------------------------------------------------------

def test_rejected_sessions_are_exported_too(engine, tmp_path):
    """SPEC keeps accepted AND rejected: the reject histogram needs them."""
    store(engine, session_doc("s-rej", accepted=False, reject_reason="too_short"))
    anonymise_sessions.export(engine, tmp_path)
    loaded, failures = eval_script.load_sessions(tmp_path)
    assert failures == []
    assert loaded[0].session["reject_reason"] == "too_short"


def test_export_is_idempotent_byte_for_byte(engine, tmp_path):
    store(engine, session_doc(), [event_doc(100)])
    anonymise_sessions.export(engine, tmp_path)
    first = (tmp_path / "s-001.json").read_bytes()
    anonymise_sessions.export(engine, tmp_path)
    assert (tmp_path / "s-001.json").read_bytes() == first


def test_a_session_that_fails_its_schema_is_fatal_not_skipped(engine, tmp_path):
    bad = session_doc()
    del bad["variant_id"]
    store(engine, bad)
    with pytest.raises(anonymise_sessions.InvalidSession):
        anonymise_sessions.export(engine, tmp_path)
    assert list(tmp_path.glob("*.json")) == []


def test_report_counts_what_it_wrote(engine, tmp_path):
    store(engine, session_doc("s-1"), [event_doc(100)])
    store(engine, session_doc("s-2", accepted=False, reject_reason="one_station"))
    store(engine, session_doc("s-3", consent=False))
    report = anonymise_sessions.export(engine, tmp_path)
    assert report.exported == 2
    assert report.accepted == 1
    assert report.rejected == 1
    assert report.withheld_no_consent == 1
    assert report.events == 1
