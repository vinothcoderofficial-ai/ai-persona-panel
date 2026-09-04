"""S19 -- `scripts/eval.py`, the script that gates the build.

Two things are being defended here.

**The pre-registration guarantee.** The project's central claim is that the
synthetic prediction was fixed before the human shopped. `POST /sessions`
enforces that structurally at capture time; this script re-enforces it from
the committed files, so a lock that was back-dated, re-timestamped or edited
after the fact fails the build instead of quietly becoming evidence. The
ordering test below builds a deliberately violating fixture and asserts a
non-zero exit that names the session.

**Honesty about an empty panel.** `data/sessions/anon/` is empty today -- the
real panel is outstanding human work (S21). `eval.py` must still run, must
emit the synthetic-only numbers it genuinely can compute, and must never
print `0.00` where it means "not measured". The empty-panel tests assert the
absence of that fake zero, not merely the presence of a caveat.

The wall clock and `t_ms`
-------------------------
Events carry `t_ms`, an offset from the start of the session, not a
timestamp, so "the lock predates the first event" is checked as

    lock.created_at  <  session.started_at + first_event.t_ms

`api/app/prediction.py` documents why the naive comparison against
`started_at` alone would be wrong: the browser stamps `started_at` and *then*
calls `POST /sessions`, which simulates 10,000 shoppers before it can write
the lock, so `created_at` is always a little later than `started_at` on an
honest session. `test_an_honest_lock_written_just_after_started_at_passes`
pins that, and the gating fixture violates the ordering under both readings.
"""

import importlib.util
import json
import random
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from jsonschema import Draft7Validator

from analytics.report import NOT_COLLECTED
from api.app import prediction
from api.app.resolve import resolve

ROOT = Path(__file__).resolve().parents[2]


def _load_eval_module():
    """Import scripts/eval.py, which is a script rather than a package member.

    Registered in `sys.modules` before it is executed: the module defines
    dataclasses, and `@dataclass` resolves its annotations through
    `sys.modules[cls.__module__]`, which does not exist yet for a module built
    straight from a spec.
    """
    name = "shoppertwin_eval"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "eval.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


evalmod = _load_eval_module()

VARIANTS = ("A", "B", "C")
BASE_TIME = datetime(2026, 9, 14, 10, 0, 0, tzinfo=timezone.utc)

# How long POST /sessions takes to simulate and write the lock, on an honest
# session: after `started_at`, well before the shopper's first event.
LOCK_DELAY = timedelta(milliseconds=400)
FIRST_EVENT_MS = 1200


def _iso(moment: datetime) -> str:
    """The `created_at` format api/app/prediction.py writes (SPEC 4.6)."""
    return f"{moment.strftime('%Y-%m-%dT%H:%M:%S')}.{moment.microsecond // 1000:03d}Z"


def _resolved(variant_id: str) -> dict:
    base = json.loads((ROOT / "data" / "planograms" / "demo_aisle.json").read_text(encoding="utf-8"))
    variant = json.loads((ROOT / "data" / "variants" / f"{variant_id}.json").read_text(encoding="utf-8"))
    return resolve(base, variant)


def _slot_sku_pairs(planogram: dict) -> list[tuple[str, str]]:
    return [
        (slot["slot_id"], slot["sku_id"])
        for bay in planogram["bays"]
        for shelf in bay["shelves"]
        for slot in shelf["slots"]
        if slot["sku_id"] is not None
    ]


def _ad_slot_ids(planogram: dict) -> list[str]:
    return [ad["ad_slot_id"] for bay in planogram["bays"] for ad in bay["ad_slots"]]


# ---------------------------------------------------------------------------
# Fixture panel
# ---------------------------------------------------------------------------


def _events(rng: random.Random, planogram: dict, first_event_ms: int) -> list[dict]:
    """A plausible cursor-only session: dwells, one ad look, two purchases.

    Deterministic given `rng`, and shaped by schemas/event.schema.json.
    """
    pairs = _slot_sku_pairs(planogram)
    looked = rng.sample(pairs, 8)
    events: list[dict] = [
        {"t_ms": first_event_ms, "type": "station_enter", "station_id": "B1", "payload": {}}
    ]
    t = first_event_ms
    for slot_id, _sku_id in looked:
        t += rng.randint(400, 1600)
        events.append(
            {
                "t_ms": t,
                "type": "cursor_dwell",
                "station_id": slot_id[:2],
                "payload": {"slot_id": slot_id, "dur_ms": rng.randint(300, 2500)},
            }
        )

    ad_slot_id = rng.choice(_ad_slot_ids(planogram))
    t += 900
    events.append(
        {
            "t_ms": t,
            "type": "fixation",
            "station_id": ad_slot_id[:2],
            "payload": {
                "x": 700,
                "y": 300,
                "dur_ms": rng.randint(200, 900),
                "slot_id": ad_slot_id,
                "ad_slot_id": ad_slot_id,
                "shelf_id": None,
            },
        }
    )

    for slot_id, sku_id in rng.sample(looked, 2):
        t += rng.randint(500, 1500)
        events.append(
            {
                "t_ms": t,
                "type": "hover",
                "station_id": slot_id[:2],
                "payload": {"sku_id": sku_id, "slot_id": slot_id},
            }
        )
        t += 400
        events.append(
            {
                "t_ms": t,
                "type": "add_to_cart",
                "station_id": slot_id[:2],
                "payload": {"sku_id": sku_id, "slot_id": slot_id},
            }
        )

    events.append({"t_ms": t + 1000, "type": "checkout", "station_id": None, "payload": {}})
    return events


def _session_document(
    session_id: str,
    variant_id: str,
    started_at: datetime,
    *,
    prediction_id: str,
    accepted: bool = True,
    reject_reason: str | None = None,
    mode: str = "cursor_only",
    archetype: str = "mission",
    duration_s: float = 96.0,
) -> dict:
    return {
        "session_id": session_id,
        "variant_id": variant_id,
        "consent": True,
        "started_at": _iso(started_at),
        "ended_at": _iso(started_at + timedelta(seconds=duration_s)),
        "screen_w": 1440,
        "screen_h": 900,
        "mode": mode,
        "calibration_error_px": None,
        "intake": {"has_list": True, "same_brand": False, "hurry": True},
        "archetype_label": archetype,
        "prediction_id": prediction_id,
        "accepted": accepted,
        "reject_reason": reject_reason,
        "quality": {"fixation_coverage": 0.71, "stations_visited": 3, "duration_s": duration_s},
    }


def _lock_document(
    session_id: str, variant_id: str, created_at: datetime, *, prediction_id: str
) -> dict:
    """A SPEC 4.6 lock whose `sha256` is computed with the production recipe.

    `api/app/prediction.compute_sha256` is called rather than re-implemented,
    so the fixture and the verifier cannot drift apart -- if they did, this
    file would be testing its own arithmetic instead of eval.py's.
    """
    population = {
        slot_id: round(0.02 + 0.001 * index, 6)
        for index, slot_id in enumerate(prediction.occupied_slot_ids(_resolved(variant_id)))
    }
    sim_run_id = f"simrun-{variant_id}"
    created = _iso(created_at)
    return {
        "prediction_id": prediction_id,
        "session_id": session_id,
        "variant_id": variant_id,
        "sim_run_id": sim_run_id,
        "created_at": created,
        "population_fixation_prob": population,
        "sha256": prediction.compute_sha256(population, sim_run_id, created),
        "git_commit": "abc1234",
    }


def _write_panel(
    tmp_path: Path,
    *,
    per_variant: dict[str, int] | None = None,
    n_rejected: int = 2,
) -> dict:
    """Write a whole committed-evidence directory pair and return its paths.

    Sessions are spread over the three variants so calibration has a fit
    variant and two holdouts and `decision_agreement` has more than one real
    winner to choose between.
    """
    per_variant = per_variant or {"A": 6, "B": 5, "C": 5}
    sessions_dir = tmp_path / "sessions" / "anon"
    predictions_dir = tmp_path / "predictions"
    sessions_dir.mkdir(parents=True)
    predictions_dir.mkdir(parents=True)

    rng = random.Random(20260914)
    index = 0
    for variant_id in sorted(per_variant):
        planogram = _resolved(variant_id)
        for _ in range(per_variant[variant_id]):
            index += 1
            _write_session(
                sessions_dir,
                predictions_dir,
                session_id=f"sess-{index:03d}",
                variant_id=variant_id,
                started_at=BASE_TIME + timedelta(minutes=10 * index),
                events=_events(rng, planogram, FIRST_EVENT_MS),
                archetype=("mission", "browser", "loyalist", "switcher")[index % 4],
                mode="webcam" if index % 3 else "cursor_only",
            )

    for reject in range(n_rejected):
        index += 1
        _write_session(
            sessions_dir,
            predictions_dir,
            session_id=f"sess-{index:03d}",
            variant_id="A",
            started_at=BASE_TIME + timedelta(minutes=10 * index),
            events=[{"t_ms": FIRST_EVENT_MS, "type": "station_enter", "station_id": "B1",
                     "payload": {}}],
            accepted=False,
            reject_reason="too_short",
        )

    return {"sessions_dir": sessions_dir, "predictions_dir": predictions_dir}


def _write_session(
    sessions_dir: Path,
    predictions_dir: Path,
    *,
    session_id: str,
    variant_id: str,
    started_at: datetime,
    events: list[dict],
    lock_created_at: datetime | None = None,
    accepted: bool = True,
    reject_reason: str | None = None,
    mode: str = "cursor_only",
    archetype: str = "mission",
    write_lock: bool = True,
    tamper_sha: bool = False,
    lock_variant_id: str | None = None,
) -> dict:
    prediction_id = f"pred-{session_id}"
    lock = _lock_document(
        session_id,
        lock_variant_id or variant_id,
        lock_created_at if lock_created_at is not None else started_at + LOCK_DELAY,
        prediction_id=prediction_id,
    )
    if tamper_sha:
        lock["population_fixation_prob"]["B1S1P1"] = 0.99
    if write_lock:
        (predictions_dir / f"{session_id}.json").write_text(
            json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    document = {
        "session": _session_document(
            session_id,
            variant_id,
            started_at,
            prediction_id=prediction_id,
            accepted=accepted,
            reject_reason=reject_reason,
            mode=mode,
            archetype=archetype,
        ),
        "events": events,
    }
    (sessions_dir / f"{session_id}.json").write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return document


def _empty_panel(tmp_path: Path) -> dict:
    sessions_dir = tmp_path / "sessions" / "anon"
    predictions_dir = tmp_path / "predictions"
    sessions_dir.mkdir(parents=True)
    predictions_dir.mkdir(parents=True)
    (sessions_dir / ".gitkeep").write_text("", encoding="utf-8")
    (predictions_dir / ".gitkeep").write_text("", encoding="utf-8")
    return {"sessions_dir": sessions_dir, "predictions_dir": predictions_dir}


def _run(tmp_path: Path, panel: dict, **kwargs):
    return evalmod.run_eval(
        sessions_dir=panel["sessions_dir"],
        predictions_dir=panel["predictions_dir"],
        results_path=tmp_path / "RESULTS.md",
        figures_dir=tmp_path / "figures",
        **kwargs,
    )


# ---------------------------------------------------------------------------
# THE GATING TEST: pre-registration ordering
# ---------------------------------------------------------------------------


def test_a_lock_created_after_its_sessions_first_event_fails_the_build(tmp_path):
    """The pre-registration guarantee. A silent pass here would void the
    project's central claim, so this is an exit code, not a warning."""
    panel = _write_panel(tmp_path)
    started_at = BASE_TIME + timedelta(hours=5)
    _write_session(
        panel["sessions_dir"],
        panel["predictions_dir"],
        session_id="sess-backdated",
        variant_id="A",
        started_at=started_at,
        events=_events(random.Random(7), _resolved("A"), FIRST_EVENT_MS),
        # The shopper's first event arrives 1.2 s in; the lock is written 5 s
        # in -- after the behaviour it claims to have predicted.
        lock_created_at=started_at + timedelta(seconds=5),
    )

    outcome = _run(tmp_path, panel)

    assert outcome.exit_code != 0
    assert any("sess-backdated" in failure for failure in outcome.failures)
    assert any("created_at" in failure for failure in outcome.failures)
    # The build gate must not leave a report behind that was built from
    # evidence it just rejected.
    assert not (tmp_path / "RESULTS.md").exists()


def test_main_exits_non_zero_on_an_ordering_violation(tmp_path, capsys):
    """The same failure, through the actual command-line entry point."""
    panel = _write_panel(tmp_path, per_variant={"A": 4, "B": 4, "C": 4}, n_rejected=0)
    started_at = BASE_TIME + timedelta(hours=9)
    _write_session(
        panel["sessions_dir"],
        panel["predictions_dir"],
        session_id="sess-backdated",
        variant_id="B",
        started_at=started_at,
        # 30 s in: after the shopper's first event at 1.2 s, but still before
        # the session ends at 96 s, so only the first-event rule can catch it.
        events=_events(random.Random(11), _resolved("B"), FIRST_EVENT_MS),
        lock_created_at=started_at + timedelta(seconds=30),
    )

    exit_code = evalmod.main(
        [
            "--sessions-dir", str(panel["sessions_dir"]),
            "--predictions-dir", str(panel["predictions_dir"]),
            "--results", str(tmp_path / "RESULTS.md"),
            "--figures-dir", str(tmp_path / "figures"),
        ]
    )

    assert exit_code != 0
    assert "sess-backdated" in capsys.readouterr().out


def test_an_honest_lock_written_just_after_started_at_passes(tmp_path):
    """`created_at` is later than `started_at` on every honest session.

    The browser stamps `started_at` and then calls `POST /sessions`, which
    simulates before it can write the lock (api/app/prediction.py). A check
    of `created_at <= started_at` would therefore fail the build on real data
    the day it arrives; the check is against the first event's arrival.
    """
    panel = _write_panel(tmp_path, per_variant={"A": 4, "B": 4, "C": 4}, n_rejected=0)
    started_at = BASE_TIME + timedelta(hours=7)
    _write_session(
        panel["sessions_dir"],
        panel["predictions_dir"],
        session_id="sess-honest",
        variant_id="A",
        started_at=started_at,
        events=_events(random.Random(3), _resolved("A"), FIRST_EVENT_MS),
        lock_created_at=started_at + timedelta(milliseconds=900),
    )

    outcome = _run(tmp_path, panel)

    assert outcome.exit_code == 0, outcome.failures


def test_a_lock_created_after_the_session_ended_fails(tmp_path):
    panel = _write_panel(tmp_path, per_variant={"A": 4, "B": 4, "C": 4}, n_rejected=0)
    started_at = BASE_TIME + timedelta(hours=11)
    _write_session(
        panel["sessions_dir"],
        panel["predictions_dir"],
        session_id="sess-after-the-fact",
        variant_id="C",
        started_at=started_at,
        events=[],
        lock_created_at=started_at + timedelta(days=1),
    )

    outcome = _run(tmp_path, panel)

    assert outcome.exit_code != 0
    assert any("sess-after-the-fact" in failure for failure in outcome.failures)


# ---------------------------------------------------------------------------
# Lock integrity
# ---------------------------------------------------------------------------


def test_a_tampered_lock_is_rejected(tmp_path):
    """The prediction was edited after it was hashed. The digest no longer
    matches its own payload, so the lock is not evidence of anything."""
    panel = _write_panel(tmp_path, per_variant={"A": 4, "B": 4, "C": 4}, n_rejected=0)
    _write_session(
        panel["sessions_dir"],
        panel["predictions_dir"],
        session_id="sess-tampered",
        variant_id="A",
        started_at=BASE_TIME + timedelta(hours=13),
        events=_events(random.Random(5), _resolved("A"), FIRST_EVENT_MS),
        tamper_sha=True,
    )

    outcome = _run(tmp_path, panel)

    assert outcome.exit_code != 0
    assert any("sess-tampered" in failure and "sha256" in failure for failure in outcome.failures)


def test_an_accepted_session_with_no_lock_fails(tmp_path):
    panel = _write_panel(tmp_path, per_variant={"A": 4, "B": 4, "C": 4}, n_rejected=0)
    _write_session(
        panel["sessions_dir"],
        panel["predictions_dir"],
        session_id="sess-unlocked",
        variant_id="A",
        started_at=BASE_TIME + timedelta(hours=15),
        events=_events(random.Random(9), _resolved("A"), FIRST_EVENT_MS),
        write_lock=False,
    )

    outcome = _run(tmp_path, panel)

    assert outcome.exit_code != 0
    assert any("sess-unlocked" in failure for failure in outcome.failures)


def test_a_lock_naming_a_different_variant_fails(tmp_path):
    """The prediction was computed for the wrong shelf. Scoring the session
    against it would compare a shopper to a store they never saw."""
    panel = _write_panel(tmp_path, per_variant={"A": 4, "B": 4, "C": 4}, n_rejected=0)
    _write_session(
        panel["sessions_dir"],
        panel["predictions_dir"],
        session_id="sess-crossed",
        variant_id="A",
        started_at=BASE_TIME + timedelta(hours=17),
        events=_events(random.Random(13), _resolved("A"), FIRST_EVENT_MS),
        lock_variant_id="C",
    )

    outcome = _run(tmp_path, panel)

    assert outcome.exit_code != 0
    assert any("sess-crossed" in failure and "variant" in failure for failure in outcome.failures)


def test_a_session_that_fails_its_schema_fails_the_build(tmp_path):
    panel = _write_panel(tmp_path, per_variant={"A": 4, "B": 4, "C": 4}, n_rejected=0)
    (panel["sessions_dir"] / "sess-broken.json").write_text(
        json.dumps({"session": {"session_id": "sess-broken"}, "events": []}), encoding="utf-8"
    )

    outcome = _run(tmp_path, panel)

    assert outcome.exit_code != 0
    assert any("sess-broken" in failure for failure in outcome.failures)


# ---------------------------------------------------------------------------
# A populated panel
# ---------------------------------------------------------------------------


EXPECTED_SECTIONS = (
    "# Results",
    "## Panel",
    "## Pre-registration",
    "## Real vs synthetic, per variant",
    "## Noise ceiling and relative agreement",
    "## Calibration — fit and holdout",
    "## Known effect — the focal SKU at eye level",
    "## Ad-to-Purchase Lift",
    "## Decision agreement",
    "## Synthetic panel on its own",
    "## Figures",
)


def test_a_populated_panel_produces_every_section(tmp_path):
    panel = _write_panel(tmp_path)

    outcome = _run(tmp_path, panel)

    assert outcome.exit_code == 0, outcome.failures
    markdown = (tmp_path / "RESULTS.md").read_text(encoding="utf-8")
    for section in EXPECTED_SECTIONS:
        assert section in markdown, section
    assert "Do not edit by hand" in markdown
    assert "n = 16 accepted" in markdown
    assert "2 rejected" in markdown
    assert "too_short 2" in markdown


def test_the_metrics_document_validates_against_its_schema(tmp_path):
    panel = _write_panel(tmp_path)

    outcome = _run(tmp_path, panel)

    schema = json.loads(
        (ROOT / "schemas" / "metrics.schema.json").read_text(encoding="utf-8")
    )
    errors = sorted(Draft7Validator(schema).iter_errors(outcome.metrics), key=str)
    assert errors == [], [error.message for error in errors]
    assert outcome.metrics["n_real_accepted"] == 16
    assert outcome.metrics["n_real_rejected"] == 2
    assert outcome.metrics["fit_variant"] == "A"
    assert outcome.metrics["holdout_variants"] == ["B", "C"]
    assert sorted(outcome.metrics["per_variant"]) == ["A", "B", "C"]


def test_calibration_is_fitted_on_a_and_the_holdouts_are_reported_separately(tmp_path):
    panel = _write_panel(tmp_path)

    outcome = _run(tmp_path, panel)
    calibration = outcome.report_input["calibration"]

    assert calibration["fit"]["variant_id"] == "A"
    assert [row["variant_id"] for row in calibration["holdout"]] == ["B", "C"]
    markdown = (tmp_path / "RESULTS.md").read_text(encoding="utf-8")
    assert "| A | fit |" in markdown
    assert "| B | holdout |" in markdown
    assert "| C | holdout |" in markdown


def test_the_known_effect_uses_each_variants_own_focal_slot(tmp_path):
    panel = _write_panel(tmp_path)

    outcome = _run(tmp_path, panel)
    known = outcome.report_input["known_effect"]

    assert known["focal_slot_a"] == "B1S5P1"
    assert known["focal_slot_b"] == "B1S3P2"


def test_figures_are_written(tmp_path):
    panel = _write_panel(tmp_path)

    outcome = _run(tmp_path, panel)

    written = sorted(path.name for path in (tmp_path / "figures").glob("*.png"))
    assert written, "no figures were written"
    assert set(outcome.report_input["figures"]["written"]) <= set(written)


# ---------------------------------------------------------------------------
# Determinism (SPEC M8: byte-identical regeneration)
# ---------------------------------------------------------------------------


def test_results_md_is_byte_identical_on_a_second_run(tmp_path):
    panel = _write_panel(tmp_path)

    _run(tmp_path, panel)
    first = (tmp_path / "RESULTS.md").read_bytes()
    _run(tmp_path, panel)
    second = (tmp_path / "RESULTS.md").read_bytes()

    assert first == second


def test_the_empty_panel_is_byte_identical_on_a_second_run(tmp_path):
    panel = _empty_panel(tmp_path)

    _run(tmp_path, panel)
    first = (tmp_path / "RESULTS.md").read_bytes()
    _run(tmp_path, panel)
    second = (tmp_path / "RESULTS.md").read_bytes()

    assert first == second


def test_no_wall_clock_leaks_into_the_report(tmp_path):
    """A generation timestamp would make byte-identical regeneration
    impossible, so the document carries no date at all -- not the run's, and
    not the sessions'."""
    panel = _write_panel(tmp_path)

    _run(tmp_path, panel)
    markdown = (tmp_path / "RESULTS.md").read_text(encoding="utf-8")

    assert re.search(r"\d{4}-\d{2}-\d{2}", markdown) is None
    assert str(datetime.now(timezone.utc).year) not in markdown


# ---------------------------------------------------------------------------
# The empty panel: honest, not zero
# ---------------------------------------------------------------------------


def test_the_empty_panel_exits_zero_and_says_n_is_zero(tmp_path):
    panel = _empty_panel(tmp_path)

    outcome = _run(tmp_path, panel)

    assert outcome.exit_code == 0, outcome.failures
    markdown = (tmp_path / "RESULTS.md").read_text(encoding="utf-8")
    assert "n = 0 accepted" in markdown
    assert NOT_COLLECTED in markdown


def test_the_empty_panel_prints_no_fabricated_real_number(tmp_path):
    """The failure mode this guards: a table whose real column reads `0.00`,
    which a reader takes for a measured agreement of zero."""
    panel = _empty_panel(tmp_path)

    _run(tmp_path, panel)
    markdown = (tmp_path / "RESULTS.md").read_text(encoding="utf-8")

    row = next(line for line in markdown.splitlines() if line.startswith("| A — "))
    assert NOT_COLLECTED in row
    assert "0.00" not in row

    ceiling = markdown.split("## Noise ceiling", 1)[1].split("##", 1)[0]
    assert NOT_COLLECTED in ceiling
    assert "0.00" not in ceiling


def test_the_empty_panel_states_what_is_missing(tmp_path):
    panel = _empty_panel(tmp_path)

    outcome = _run(tmp_path, panel)
    markdown = (tmp_path / "RESULTS.md").read_text(encoding="utf-8")

    assert outcome.metrics is None
    assert outcome.report_input["unavailable"]
    assert "## Not yet measured" in markdown
    assert any("no accepted" in reason for reason in outcome.report_input["unavailable"])


def test_the_empty_panel_still_reports_the_synthetic_panel(tmp_path):
    """The half of the study that does not need a real panel exists today and
    must be shown, or an empty run looks like a broken run."""
    panel = _empty_panel(tmp_path)

    outcome = _run(tmp_path, panel)
    markdown = (tmp_path / "RESULTS.md").read_text(encoding="utf-8")

    assert "## Synthetic panel on its own" in markdown
    rows = {row["variant_id"]: row for row in outcome.report_input["per_variant"]}
    assert sorted(rows) == ["A", "B", "C"]
    for variant_id in VARIANTS:
        assert rows[variant_id]["synth_focal_attention"] is not None
        assert rows[variant_id]["attention_spearman"] is None
    assert rows["A"]["focal_slot"] == "B1S5P1"
    assert rows["B"]["focal_slot"] == "B1S3P2"
    assert outcome.report_input["known_effect"]["synth_uplift"] is not None
    assert outcome.report_input["known_effect"]["real_uplift"] is None


def test_a_panel_too_small_for_a_noise_ceiling_says_so_rather_than_faking_one(tmp_path):
    """Three sessions cannot support a split-half ceiling
    (`noise_ceiling.MIN_SESSIONS`), so nothing is reported for it -- and no
    metrics document is emitted, because every field of it is a comparison."""
    panel = _write_panel(tmp_path, per_variant={"A": 3}, n_rejected=0)

    outcome = _run(tmp_path, panel)

    assert outcome.exit_code == 0, outcome.failures
    assert outcome.metrics is None
    assert outcome.report_input["noise_ceiling"] is None
    assert outcome.report_input["relative_agreement"] is None
    markdown = (tmp_path / "RESULTS.md").read_text(encoding="utf-8")
    assert NOT_COLLECTED in markdown.split("## Noise ceiling", 1)[1].split("##", 1)[0]


def test_locks_without_sessions_are_not_an_error(tmp_path):
    """A registered session whose anonymised file has not landed yet leaves a
    lock behind. That is the normal state during collection, not a failure."""
    panel = _empty_panel(tmp_path)
    (panel["predictions_dir"] / "sess-orphan.json").write_text(
        json.dumps(
            _lock_document("sess-orphan", "A", BASE_TIME, prediction_id="pred-sess-orphan"),
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    outcome = _run(tmp_path, panel)

    assert outcome.exit_code == 0, outcome.failures
    assert outcome.report_input["pre_registration"]["n_locks_found"] == 1
    assert outcome.report_input["pre_registration"]["n_locks_verified"] == 1


def test_the_real_repository_runs_clean(tmp_path):
    """`make eval` on the committed repository, today: no sessions, exit 0."""
    outcome = evalmod.run_eval(
        results_path=tmp_path / "RESULTS.md",
        figures_dir=tmp_path / "figures",
    )

    assert outcome.exit_code == 0, outcome.failures
    assert "n = 0 accepted" in (tmp_path / "RESULTS.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("variant_id", VARIANTS)
def test_every_committed_variant_is_reported(tmp_path, variant_id):
    outcome = evalmod.run_eval(
        results_path=tmp_path / "RESULTS.md",
        figures_dir=tmp_path / "figures",
    )
    assert any(row["variant_id"] == variant_id for row in outcome.report_input["per_variant"])
