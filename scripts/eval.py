"""`make eval` -- regenerate RESULTS.md and docs/figures from committed evidence.

Run: `python scripts/eval.py` (exit 0 on success, non-zero on any integrity
failure).

This script is a build gate first and a report generator second. It loads
every anonymised session in `data/sessions/anon/` and every prediction lock in
`predictions/`, refuses to produce a single number if the evidence does not
hold together, and only then runs the analytics and writes the report.

The pre-registration check (CLAUDE.md, non-negotiable)
-----------------------------------------------------
The project's central claim is that each synthetic prediction was locked and
hashed BEFORE the real shopper started. `POST /sessions` enforces that
structurally at capture time -- the lock is written before the session row
exists, and the events endpoint refuses a session with no lock. This script
re-enforces it from the committed files, because a structural guarantee at
capture time says nothing about what happened to the files afterwards.

For every accepted session:

  * the lock exists, and its `session_id`, `variant_id` and `prediction_id`
    agree with the session document;
  * its `sha256` recomputes from its own stored fields, using
    `api/app/prediction.compute_sha256` -- the production recipe, called, not
    re-implemented, so a second slightly-different recipe cannot quietly
    bless a tampered file;
  * `created_at` strictly precedes the arrival of the session's first event.

That last one needs care. Events carry `t_ms`, an offset from the start of
the session, NOT a wall clock, so the first event's arrival is reconstructed
as `started_at + t_ms`. The naive check -- `created_at <= started_at` -- would
be wrong in the other direction and would fail the build on every honest
session: the browser stamps `started_at` and only then calls `POST /sessions`,
which simulates 10,000 shoppers per persona before it can write the lock, so
an honest `created_at` is always a little *later* than `started_at`. See the
"created_at, and what it can honestly be compared against" section of
`api/app/prediction.py`. What matters, and what SPEC 4.6 actually asks for
("`make eval` asserts `created_at < first event timestamp`"), is that no
behaviour was recorded before the commitment it is judged against.

Any violation prints a message naming the session and exits non-zero, and no
RESULTS.md is written -- a report built from evidence that just failed its own
integrity check would be worse than no report.

The empty panel
---------------
`data/sessions/anon/` is empty until the real panel is collected (S21), and
this script has to be honest about that rather than crash, invent, or print a
table of zeroes. So:

  * an empty panel is a successful run (exit 0), not a failure;
  * every real-panel quantity is None and renders as "not yet collected" --
    never `0.0`, and never an omitted row a reader would read as zero;
  * no `ExperimentMetrics` document is emitted, because every field that
    document requires is a real-vs-synthetic comparison and there is nothing
    to compare;
  * the synthetic side, which needs no real panel, is computed and reported
    in full, so an honest empty run still shows the half of the study that
    exists.

Nothing changes when the sessions land: the same code paths fill in.

Determinism (SPEC M8: "`make eval` reproduces RESULTS.md byte-identically")
--------------------------------------------------------------------------
Every seed is fixed and explicit, every iteration over a directory or a
mapping is sorted, every float is formatted at fixed precision by
`analytics/report.py`, and the document carries no wall clock -- not even a
"generated at" line, which is precisely the kind of thing that would break
this. `docs/figures/*.png` is gitignored, so only RESULTS.md needs to be
byte-stable; matplotlib still runs on the `Agg` backend so a headless machine
draws the same figures.

Formulas
--------
None live here. Fusion is `analytics/fusion.py`, the metrics are
`analytics/metrics.py`, the ceiling is `analytics/noise_ceiling.py`,
calibration is `analytics/calibration.py`, the lift is `analytics/lift.py`,
the known effect is `analytics/known_effect.py`, the simulation is
`api/app/simcache.py` and `resolve()` is `api/app/resolve.py`. This module
loads files, checks them, routes arrays between those modules, and writes the
output.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import matplotlib

matplotlib.use("Agg")  # before pyplot: eval runs headless, in CI and on the office machine

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from jsonschema import Draft7Validator  # noqa: E402

from analytics import known_effect as known_effect_mod  # noqa: E402
from analytics import report as report_mod  # noqa: E402
from analytics.calibration import calibrate, evaluate  # noqa: E402
from analytics.fusion import DEFAULT_MODE, fuse_session, fuse_synthetic, trimmed_mean  # noqa: E402
from analytics.lift import (  # noqa: E402
    POPULATION_KEY,
    ad_slots_showing,
    ad_to_purchase_lift,
    creative_brand,
    sku_brands,
    split_panel,
    synth_lift,
)
from analytics.metrics import (  # noqa: E402
    ad_slot_index_spearman,
    attention_spearman,
    decision_agreement,
    heatmap_kl,
    purchase_share_mae,
)
from analytics.noise_ceiling import MIN_SESSIONS, noise_ceiling, relative_agreement  # noqa: E402
from api.app import prediction, simcache  # noqa: E402
from api.app.resolve import resolve  # noqa: E402

# --- Paths -----------------------------------------------------------------

DEFAULT_SESSIONS_DIR = ROOT / "data" / "sessions" / "anon"
DEFAULT_PREDICTIONS_DIR = ROOT / "predictions"
DEFAULT_RESULTS_PATH = ROOT / "RESULTS.md"
DEFAULT_FIGURES_DIR = ROOT / "docs" / "figures"
PLANOGRAMS_DIR = ROOT / "data" / "planograms"
VARIANTS_DIR = ROOT / "data" / "variants"
SCHEMAS_DIR = ROOT / "schemas"

# --- The experiment --------------------------------------------------------

BASE_PLANOGRAM_ID = "demo_aisle"

# PLAN S17 / section 13: the persona shares are fitted on variant A and ONLY
# variant A. Everything else is holdout, and the two are always reported
# separately. This is a constant rather than a flag so a rushed Day 8 cannot
# quietly consume the holdout.
FIT_VARIANT = "A"

# The intervention with a known sign: data/variants/B.json moves this SKU from
# the bottom shelf to eye level.
FOCAL_SKU = "SKU_008"

# The creative the Ad-to-Purchase Lift is measured for.
FOCAL_CREATIVE = "AD_1"

# The KPI decision agreement is taken on. A purchase KPI, deliberately: an
# attention KPI is the thing attention vendors already sell, and picking the
# same *shelf* matters less than recommending the same *decision*.
KPI = "focal_sku_purchase_share"

# Locked predictions are simulated at 10,000 shoppers per persona, seed 42
# (api/app/prediction.py). Eval uses the same numbers so the figures it draws
# are the population the locks committed to.
N_SYNTH = prediction.N_SYNTH
SEED = prediction.SEED

# PLAN S17: 200 half-splits. SPEC M5: 1,000 bootstrap resamples, 95 % CI.
N_SPLITS = 200
N_BOOT = 1000
CI = 0.95
CI_PERCENT = 95

# schemas/session.schema.json's reject_reason may be null on a rejected
# session; the histogram needs a label for that bucket rather than a hole.
UNSPECIFIED_REASON = "unspecified"


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LoadedSession:
    """One anonymised session file: the SPEC 4.3 document plus its events."""

    path: Path
    session: dict
    events: list

    @property
    def session_id(self) -> str:
        return str(self.session["session_id"])

    @property
    def variant_id(self) -> str:
        return str(self.session["variant_id"])

    @property
    def mode(self) -> str:
        return str(self.session["mode"])

    @property
    def accepted(self) -> bool:
        return bool(self.session.get("accepted"))


@dataclass(frozen=True)
class EvalOutcome:
    """Everything a caller (or a test) needs to know about one run."""

    exit_code: int
    failures: tuple
    metrics: Optional[dict]
    report_input: Optional[dict]
    results_markdown: Optional[str]
    headline_source: Optional[str]


def _validator(name: str) -> Draft7Validator:
    return Draft7Validator(json.loads((SCHEMAS_DIR / name).read_text(encoding="utf-8")))


def _schema_errors(validator: Draft7Validator, document: Any) -> list:
    return [
        f"{'/'.join(str(part) for part in error.path)}: {error.message}"
        for error in sorted(validator.iter_errors(document), key=str)
    ]


def load_sessions(sessions_dir: Path) -> tuple[list, list]:
    """Every anonymised session in `sessions_dir`, in filename order.

    Two on-disk shapes are accepted, because `scripts/anonymise_sessions.py`
    (S21) has not been written yet and either is a reasonable thing for it to
    emit:

        {"session": {...SPEC 4.3 session...}, "events": [...]}
        {...SPEC 4.3 session..., "events": [...]}

    The session is validated against schemas/session.schema.json with `events`
    removed (the schema is `additionalProperties: false`) and every event
    against schemas/event.schema.json. A file that fails either is a build
    failure, not a skipped file: silently dropping a malformed session would
    change the panel size without telling anyone.
    """
    session_validator = _validator("session.schema.json")
    event_validator = _validator("event.schema.json")

    loaded: list = []
    failures: list = []

    for path in sorted(sessions_dir.glob("*.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            failures.append(f"{path.name}: could not be read as JSON: {exc}")
            continue

        if not isinstance(document, dict):
            failures.append(f"{path.name}: expected a JSON object, got {type(document).__name__}")
            continue

        if isinstance(document.get("session"), dict):
            session = dict(document["session"])
            events = document.get("events") or []
        else:
            session = {key: value for key, value in document.items() if key != "events"}
            events = document.get("events") or []

        errors = _schema_errors(session_validator, session)
        if errors:
            name = session.get("session_id", path.stem)
            failures.append(f"{name}: session does not match session.schema.json: {'; '.join(errors)}")
            continue

        if not isinstance(events, list):
            failures.append(f"{session['session_id']}: `events` must be a list")
            continue

        event_errors = []
        for index, event in enumerate(events):
            for message in _schema_errors(event_validator, event):
                event_errors.append(f"event {index}: {message}")
        if event_errors:
            failures.append(
                f"{session['session_id']}: events do not match event.schema.json: "
                f"{'; '.join(event_errors[:5])}"
            )
            continue

        loaded.append(LoadedSession(path=path, session=session, events=list(events)))

    return loaded, failures


def load_locks(predictions_dir: Path) -> tuple[dict, list]:
    """Every committed prediction lock, keyed by session id.

    A lock whose file name and `session_id` disagree is a failure: the events
    endpoint looks the lock up by file name, so the two disagreeing means the
    gate at capture time was checking a different document from the one this
    script is about to verify.
    """
    lock_validator = _validator("prediction.schema.json")
    locks: dict = {}
    failures: list = []

    for path in sorted(predictions_dir.glob("*.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            failures.append(f"{path.name}: lock could not be read as JSON: {exc}")
            continue

        errors = _schema_errors(lock_validator, document)
        if errors:
            failures.append(
                f"{path.stem}: lock does not match prediction.schema.json: {'; '.join(errors)}"
            )
            continue

        if document["session_id"] != path.stem:
            failures.append(
                f"{path.stem}: lock file name does not match its session_id "
                f"{document['session_id']!r}"
            )
            continue

        locks[document["session_id"]] = document

    return locks, failures


# ---------------------------------------------------------------------------
# Integrity: the pre-registration guarantee
# ---------------------------------------------------------------------------


def parse_iso(value: str) -> datetime:
    """A SPEC 4.6 / 4.3 timestamp as an aware UTC datetime.

    `created_at` is written with a trailing `Z`, which `fromisoformat` did not
    accept before 3.11 and which is spelled `+00:00` internally; a timestamp
    with no zone at all is read as UTC rather than as local time, because a
    machine's timezone must never change whether a build passes.
    """
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def first_event_arrival(session: Mapping, events: Sequence[Mapping]) -> Optional[datetime]:
    """Wall clock of the session's earliest event, or None if it has none.

    Events carry `t_ms`, an offset from the start of the session, so the
    arrival is reconstructed from `started_at`. The *smallest* `t_ms` is used
    rather than the first element: a file's event order is not part of the
    contract, and the check must be against the earliest behaviour recorded,
    not the earliest one listed.
    """
    if not events:
        return None
    earliest_ms = min(int(event["t_ms"]) for event in events)
    return parse_iso(session["started_at"]) + timedelta(milliseconds=earliest_ms)


def check_session_integrity(loaded: LoadedSession, lock: Optional[Mapping]) -> list:
    """Every reason this session's evidence cannot be trusted."""
    failures: list = []
    session = loaded.session
    session_id = loaded.session_id

    if lock is None:
        if loaded.accepted:
            failures.append(
                f"{session_id}: accepted session has no prediction lock in predictions/. "
                "A lock is written on POST /sessions before any event is accepted, so a "
                "session without one cannot be scored against a pre-registered prediction."
            )
        return failures

    if lock["variant_id"] != loaded.variant_id:
        failures.append(
            f"{session_id}: lock is for variant {lock['variant_id']!r} but the session "
            f"shopped variant {loaded.variant_id!r}"
        )

    session_prediction_id = session.get("prediction_id")
    if session_prediction_id is not None and session_prediction_id != lock["prediction_id"]:
        failures.append(
            f"{session_id}: session names prediction_id {session_prediction_id!r} but its "
            f"lock is {lock['prediction_id']!r}"
        )

    expected = prediction.compute_sha256(
        lock["population_fixation_prob"], lock["sim_run_id"], lock["created_at"]
    )
    if expected != lock["sha256"]:
        failures.append(
            f"{session_id}: lock sha256 does not match its contents "
            f"(stored {lock['sha256'][:12]}..., recomputed {expected[:12]}...). "
            "The prediction was changed after it was hashed."
        )

    failures.extend(_check_ordering(loaded, lock))
    return failures


def _check_ordering(loaded: LoadedSession, lock: Mapping) -> list:
    """`created_at` must predate the session's first event (SPEC 4.6)."""
    failures: list = []
    session_id = loaded.session_id
    created_at = parse_iso(lock["created_at"])

    arrival = first_event_arrival(loaded.session, loaded.events)
    if arrival is not None and created_at >= arrival:
        failures.append(
            f"{session_id}: prediction lock created_at {lock['created_at']} does not "
            f"precede the session's first event, which arrived at "
            f"{arrival.isoformat()} (started_at {loaded.session['started_at']} plus the "
            "event's t_ms). The prediction was not pre-registered."
        )

    ended_at = loaded.session.get("ended_at")
    if ended_at and created_at > parse_iso(ended_at):
        failures.append(
            f"{session_id}: prediction lock created_at {lock['created_at']} is after the "
            f"session ended at {ended_at}. The prediction was written after the fact."
        )

    return failures


def ordering_checkable(loaded: LoadedSession) -> bool:
    """Is there anything to compare this session's `created_at` against?"""
    return bool(loaded.events) or bool(loaded.session.get("ended_at"))


# ---------------------------------------------------------------------------
# The real panel
# ---------------------------------------------------------------------------


def real_purchase_share(sessions: Sequence[LoadedSession], sku_ids: Sequence[str]) -> dict:
    """Observed purchase share per SKU, from `add_to_cart` events.

    Counted the way `analytics/lift.py` counts a basket: every `add_to_cart`
    is a purchase, repeats included, and `remove` is not netted out (PLAN
    S18). Normalised over the observed total so it is comparable, term for
    term, with a SimResult's `purchase_share`. A panel that bought nothing
    gives an all-zero vector rather than a division by zero.
    """
    counts: Counter = Counter()
    for loaded in sessions:
        for event in loaded.events:
            if event.get("type") != "add_to_cart":
                continue
            sku_id = (event.get("payload") or {}).get("sku_id")
            if sku_id is not None:
                counts[sku_id] += 1

    total = float(sum(counts[sku_id] for sku_id in sku_ids))
    if total <= 0.0:
        return {sku_id: 0.0 for sku_id in sku_ids}
    return {sku_id: counts[sku_id] / total for sku_id in sku_ids}


def real_ad_attention(sessions: Sequence[LoadedSession], ad_slot_ids: Sequence[str]) -> dict:
    """Per-ad-slot looking share, from fixations that named an ad slot.

    `analytics/fusion.py` deliberately never scores an ad slot -- ads are a
    different question with their own metric -- so this small aggregation
    lives here rather than there. `web/src/capture/FixationFilter.ts` writes
    an ad hit with `ad_slot_id` set (and copies it into `slot_id`), so that
    field is what identifies one.

    Normalised over the ad slots. A panel that never looked at an ad gives an
    all-zero vector, which the caller turns into "not measured" rather than
    into a Spearman of 0.
    """
    dwell: dict = {ad_slot_id: 0.0 for ad_slot_id in ad_slot_ids}
    for loaded in sessions:
        for event in loaded.events:
            if event.get("type") != "fixation":
                continue
            payload = event.get("payload") or {}
            ad_slot_id = payload.get("ad_slot_id")
            if ad_slot_id in dwell:
                dwell[ad_slot_id] += float(payload.get("dur_ms", 0) or 0)

    total = sum(dwell.values())
    if total <= 0.0:
        return dwell
    return {ad_slot_id: value / total for ad_slot_id, value in dwell.items()}


def dominant_mode(sessions: Sequence[LoadedSession]) -> str:
    """The capture mode the synthetic side is fused with.

    Each real session is fused with its OWN mode -- that is what the session
    document records and what its events actually contain. The synthetic
    vector is a single vector per variant, though, so it has to pick one, and
    it picks the panel's most common mode. Ties, and an empty panel, resolve
    to `fusion.DEFAULT_MODE`; the choice is named in RESULTS.md rather than
    left implicit, because fusing the two sides with different weights is
    exactly the mismatch `fuse_synthetic` exists to remove.
    """
    counts = Counter(loaded.mode for loaded in sessions)
    if not counts:
        return DEFAULT_MODE
    return max(sorted(counts), key=lambda mode: counts[mode])


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


def run_eval(
    *,
    sessions_dir: Path = DEFAULT_SESSIONS_DIR,
    predictions_dir: Path = DEFAULT_PREDICTIONS_DIR,
    results_path: Path = DEFAULT_RESULTS_PATH,
    figures_dir: Path = DEFAULT_FIGURES_DIR,
    planograms_dir: Path = PLANOGRAMS_DIR,
    variants_dir: Path = VARIANTS_DIR,
    base_planogram_id: str = BASE_PLANOGRAM_ID,
    n_synth: int = N_SYNTH,
    seed: int = SEED,
    write_figures: bool = True,
    llm_client: Any = None,
    llm_headline: bool = False,
) -> EvalOutcome:
    """Load, verify, analyse, and write. Returns everything, raises nothing.

    On an integrity failure nothing is written and `exit_code` is 1; the
    failures are on the outcome and `main()` prints them.
    """
    sessions_dir = Path(sessions_dir)
    predictions_dir = Path(predictions_dir)

    sessions, failures = load_sessions(sessions_dir) if sessions_dir.is_dir() else ([], [])
    locks, lock_failures = load_locks(predictions_dir) if predictions_dir.is_dir() else ({}, [])
    failures = list(failures) + list(lock_failures)

    for loaded in sessions:
        failures.extend(check_session_integrity(loaded, locks.get(loaded.session_id)))

    if failures:
        return EvalOutcome(
            exit_code=1,
            failures=tuple(failures),
            metrics=None,
            report_input=None,
            results_markdown=None,
            headline_source=None,
        )

    report_input, metrics, attention = _analyse(
        sessions=sessions,
        locks=locks,
        sessions_dir=sessions_dir,
        planograms_dir=Path(planograms_dir),
        variants_dir=Path(variants_dir),
        base_planogram_id=base_planogram_id,
        n_synth=int(n_synth),
        seed=int(seed),
    )

    figures_dir = Path(figures_dir)
    if write_figures:
        written, skipped = _draw_figures(figures_dir, report_input, attention)
    else:
        written, skipped = [], [{"name": "all", "reason": "figures were not requested"}]
    report_input["figures"] = {"written": written, "skipped": skipped}

    if llm_headline:
        headline_text, headline_source = report_mod.headline(report_input, client=llm_client)
    else:
        # Deterministic by construction, not by luck. SPEC's acceptance line is
        # that `make eval` reproduces RESULTS.md byte-identically from committed
        # data, and the CI evidence job enforces it. Asking a model for the
        # headline sentence puts a non-reproducible string into a committed file
        # -- it happened to stay stable only while the grounding check kept
        # rejecting it, and while there was no API key at all. It also made every
        # `make eval` a paid network call. Opt in with --llm-headline when you
        # want one, and expect the report to stop being reproducible.
        headline_text, headline_source = (
            report_mod.template_headline(report_input),
            report_mod.SOURCE_TEMPLATE,
        )
    markdown = report_mod.render(report_input, headline_text=headline_text)

    results_path = Path(results_path)
    results_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.write_text(markdown, encoding="utf-8", newline="\n")

    return EvalOutcome(
        exit_code=0,
        failures=(),
        metrics=metrics,
        report_input=report_input,
        results_markdown=markdown,
        headline_source=headline_source,
    )


def _analyse(
    *,
    sessions: Sequence[LoadedSession],
    locks: Mapping[str, Mapping],
    sessions_dir: Path,
    planograms_dir: Path,
    variants_dir: Path,
    base_planogram_id: str,
    n_synth: int,
    seed: int,
) -> tuple[dict, Optional[dict], dict]:
    """Everything downstream of the integrity gate.

    Returns `(report_input, metrics_document_or_None, attention_vectors)`. The
    attention vectors are handed to the figure writer separately rather than
    hung off the report input, because the report input is serialised into the
    headline prompt: putting several hundred per-slot floats in there would
    both bloat the prompt and make almost any number in a sentence
    "grounded", which would gut the check in analytics/report.py.
    """
    base = json.loads(
        (planograms_dir / f"{base_planogram_id}.json").read_text(encoding="utf-8")
    )
    variants = {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(variants_dir.glob("*.json"))
    }
    variant_ids = sorted(variants)

    accepted = [loaded for loaded in sessions if loaded.accepted]
    rejected = [loaded for loaded in sessions if not loaded.accepted]
    fusion_mode = dominant_mode(accepted)
    unavailable: list = []

    # --- the synthetic side, which needs no real panel ----------------------
    resolved: dict = {}
    slot_ids: dict = {}
    ad_slot_ids: dict = {}
    bundles: dict = {}
    synth_attention: dict = {}
    for variant_id in variant_ids:
        planogram = resolve(base, variants[variant_id])
        resolved[variant_id] = planogram
        slot_ids[variant_id] = prediction.occupied_slot_ids(planogram)
        ad_slot_ids[variant_id] = [
            ad["ad_slot_id"] for bay in planogram["bays"] for ad in bay["ad_slots"]
        ]
        bundles[variant_id] = simcache.population(
            planogram, variant_id, n_synth=n_synth, seed=seed
        )
        synth_attention[variant_id] = fuse_synthetic(
            bundles[variant_id].population, planogram, slot_ids[variant_id], mode=fusion_mode
        )

    sku_ids = sorted(sku["sku_id"] for sku in base["skus"])
    focal_category = next(
        (sku["category"] for sku in base["skus"] if sku["sku_id"] == FOCAL_SKU), None
    )
    focal_category_skus = sorted(
        sku["sku_id"] for sku in base["skus"] if sku["category"] == focal_category
    )
    focal_brand = creative_brand(resolved[variant_ids[0]], FOCAL_CREATIVE)

    # --- the real side, per variant -----------------------------------------
    by_variant: dict = {
        variant_id: [loaded for loaded in accepted if loaded.variant_id == variant_id]
        for variant_id in variant_ids
    }
    real_attention: dict = {}
    real_purchase: dict = {}
    per_variant_rows: list = []
    per_variant_metrics: dict = {}

    for variant_id in variant_ids:
        panel = by_variant[variant_id]
        row: dict = {
            "variant_id": variant_id,
            "name": variants[variant_id].get("name", variant_id),
            "n_real": len(panel),
            "attention_spearman": None,
            "heatmap_kl": None,
            "purchase_share_mae": None,
            "purchase_share_mae_focal_category": None,
            "ad_slot_index_spearman": None,
            "real_focal_attention": None,
            "real_focal_purchase_share": None,
            "focal_slot": known_effect_mod.focal_slot(resolved[variant_id], FOCAL_SKU),
        }
        population = bundles[variant_id].population
        row["synth_focal_attention"] = (
            synth_attention[variant_id].get(row["focal_slot"]) if row["focal_slot"] else None
        )
        row["synth_focal_purchase_share"] = float(
            population["purchase_share"].get(FOCAL_SKU, 0.0)
        )
        row["synth_lift"] = synth_lift(
            population, brand_of_sku=sku_brands(resolved[variant_id]), brand=focal_brand
        )

        if panel:
            fused = [
                fuse_session(loaded.events, slot_ids[variant_id], mode=loaded.mode)
                for loaded in sorted(panel, key=lambda item: item.session_id)
            ]
            attention = trimmed_mean(fused, slot_ids[variant_id])
            purchases = real_purchase_share(panel, sku_ids)
            real_attention[variant_id] = attention
            real_purchase[variant_id] = purchases

            row["attention_spearman"] = attention_spearman(
                attention, synth_attention[variant_id], slot_ids[variant_id]
            )
            row["heatmap_kl"] = heatmap_kl(
                attention, synth_attention[variant_id], slot_ids[variant_id]
            )
            row["purchase_share_mae"] = purchase_share_mae(
                purchases, population["purchase_share"], sku_ids
            )
            row["purchase_share_mae_focal_category"] = purchase_share_mae(
                purchases, population["purchase_share"], focal_category_skus
            )
            row["real_focal_attention"] = (
                attention.get(row["focal_slot"]) if row["focal_slot"] else None
            )
            row["real_focal_purchase_share"] = purchases.get(FOCAL_SKU, 0.0)

            ad_attention = real_ad_attention(panel, ad_slot_ids[variant_id])
            if any(value > 0.0 for value in ad_attention.values()):
                row["ad_slot_index_spearman"] = ad_slot_index_spearman(
                    ad_attention, population["ad_slot_attention"], ad_slot_ids[variant_id]
                )
            else:
                unavailable.append(
                    f"variant `{variant_id}`: no accepted session looked at an ad slot, so the "
                    "Ad Slot Index has no real side (this is not an index of 0)"
                )

            entry = {
                "attention_spearman": row["attention_spearman"],
                "heatmap_kl": row["heatmap_kl"],
                "purchase_share_mae": row["purchase_share_mae"],
            }
            if row["ad_slot_index_spearman"] is not None:
                entry["ad_slot_index_spearman"] = row["ad_slot_index_spearman"]
            per_variant_metrics[variant_id] = entry
        elif accepted:
            unavailable.append(
                f"variant `{variant_id}` has no accepted sessions, so it has no real-vs-"
                "synthetic comparison"
            )

        per_variant_rows.append(row)

    if not accepted:
        unavailable.append(
            f"no accepted real sessions were found in `{_relative(sessions_dir)}` — the panel "
            "has not been collected yet (PLAN S21), so every real-panel number below is "
            "missing rather than zero"
        )

    # --- noise ceiling and relative agreement -------------------------------
    ceiling = None
    relative = None
    fit_panel = by_variant.get(FIT_VARIANT, [])
    if len(fit_panel) >= MIN_SESSIONS:
        fused_fit = [
            fuse_session(loaded.events, slot_ids[FIT_VARIANT], mode=loaded.mode)
            for loaded in sorted(fit_panel, key=lambda item: item.session_id)
        ]
        block = noise_ceiling(fused_fit, slot_ids[FIT_VARIANT], n_splits=N_SPLITS, seed=seed)
        ceiling = {"variant_id": FIT_VARIANT, **block}
        fit_row = next(row for row in per_variant_rows if row["variant_id"] == FIT_VARIANT)
        relative = relative_agreement(fit_row["attention_spearman"], block["spearman_mean"])
    elif accepted:
        unavailable.append(
            f"the noise ceiling needs at least {MIN_SESSIONS} accepted sessions on variant "
            f"`{FIT_VARIANT}` to split; there are {len(fit_panel)}"
        )

    # --- calibration: fit on A, report the holdouts separately --------------
    calibration = None
    if FIT_VARIANT in real_attention:
        fit = calibrate(
            real_attention[FIT_VARIANT],
            real_purchase[FIT_VARIANT],
            bundles[FIT_VARIANT].per_persona,
            planogram=resolved[FIT_VARIANT],
            slot_ids=slot_ids[FIT_VARIANT],
            mode=fusion_mode,
        )
        holdout = [
            evaluate(
                fit["shares"],
                real_attention[variant_id],
                real_purchase[variant_id],
                bundles[variant_id].per_persona,
                planogram=resolved[variant_id],
                slot_ids=slot_ids[variant_id],
                mode=fusion_mode,
            )
            for variant_id in variant_ids
            if variant_id != FIT_VARIANT and variant_id in real_attention
        ]
        calibration = {"fit": fit, "holdout": holdout}
    elif accepted:
        unavailable.append(
            f"calibration fits on variant `{FIT_VARIANT}` only, and it has no accepted sessions"
        )

    # --- the known effect ---------------------------------------------------
    known = _known_effect_block(
        resolved, real_attention, synth_attention, variant_ids, unavailable
    )

    # --- Ad-to-Purchase Lift on the fit variant -----------------------------
    lift_block = _lift_block(
        resolved=resolved,
        bundles=bundles,
        panel=by_variant.get(FIT_VARIANT, []),
        brand=focal_brand,
        seed=seed,
        unavailable=unavailable,
    )

    # --- decision agreement -------------------------------------------------
    real_kpi = {
        variant_id: real_purchase[variant_id].get(FOCAL_SKU, 0.0)
        for variant_id in variant_ids
        if variant_id in real_purchase
    }
    synth_kpi = {
        variant_id: float(bundles[variant_id].population["purchase_share"].get(FOCAL_SKU, 0.0))
        for variant_id in variant_ids
    }
    decision = None
    if len(real_kpi) >= 2:
        decision = decision_agreement(real_kpi, synth_kpi, KPI)
    elif accepted:
        unavailable.append(
            "decision agreement needs accepted sessions on at least two variants; there are "
            f"{len(real_kpi)}"
        )

    experiment_id = _experiment_id(sessions, bundles, variant_ids, n_synth, seed)
    holdout_variants = [
        variant_id for variant_id in variant_ids if variant_id != FIT_VARIANT
    ]

    report_input = {
        "experiment": {
            "experiment_id": experiment_id,
            "fit_variant": FIT_VARIANT,
            "holdout_variants": holdout_variants,
            "focal_sku": FOCAL_SKU,
            "focal_category": focal_category,
            "focal_creative": FOCAL_CREATIVE,
            "focal_brand": focal_brand,
            "kpi": KPI,
            "n_synth": n_synth,
            "seed": seed,
            "n_splits": N_SPLITS,
            "n_boot": N_BOOT,
            "ci_percent": CI_PERCENT,
        },
        "panel": {
            "n_real_accepted": len(accepted),
            "n_real_rejected": len(rejected),
            "n_synth": n_synth,
            "mode_split": dict(sorted(Counter(loaded.mode for loaded in accepted).items())),
            "reject_reasons": [
                {"reason": reason, "n": count}
                for reason, count in sorted(
                    Counter(
                        loaded.session.get("reject_reason") or UNSPECIFIED_REASON
                        for loaded in rejected
                    ).items()
                )
            ],
            "fusion_mode": fusion_mode,
            "has_real_panel": bool(accepted),
        },
        "pre_registration": _pre_registration_block(sessions, locks),
        "per_variant": per_variant_rows,
        "noise_ceiling": ceiling,
        "relative_agreement": relative,
        "calibration": calibration,
        "known_effect": known,
        "ad_to_purchase_lift": lift_block,
        "decision_agreement": decision,
        "figures": {"written": [], "skipped": []},
        "unavailable": unavailable,
    }

    # `_metrics_document` may append to `unavailable` -- it is the same list
    # object the report input holds, so its reason for not emitting a document
    # reaches the reader rather than only the caller.
    metrics = _metrics_document(
        report_input=report_input,
        per_variant_metrics=per_variant_metrics,
        ceiling=ceiling,
        relative=relative,
        calibration=calibration,
        known=known,
        decision=decision,
        lift_block=lift_block,
        unavailable=unavailable,
    )

    attention = {
        variant_id: {
            "slot_ids": slot_ids[variant_id],
            "synth": synth_attention[variant_id],
            "real": real_attention.get(variant_id),
        }
        for variant_id in variant_ids
    }
    return report_input, metrics, attention


def _known_effect_block(
    resolved: Mapping[str, Mapping],
    real_attention: Mapping[str, Mapping],
    synth_attention: Mapping[str, Mapping],
    variant_ids: Sequence[str],
    unavailable: list,
) -> dict:
    """The A -> B focal-SKU uplift for both panels.

    The focal slot is read out of each variant's own resolved planogram --
    variant B moves the SKU, so one slot id would measure an empty shelf
    position on one side of the subtraction (see analytics/known_effect.py).
    """
    treatment = "B"
    if FIT_VARIANT not in variant_ids or treatment not in variant_ids:
        unavailable.append(
            f"the known effect needs variants `{FIT_VARIANT}` and `{treatment}`; the committed "
            f"variants are {', '.join(f'`{v}`' for v in variant_ids)}"
        )
        return {
            "focal_slot_a": None,
            "focal_slot_b": None,
            "real_att_a": None,
            "real_att_b": None,
            "synth_att_a": None,
            "synth_att_b": None,
            "real_uplift": None,
            "synth_uplift": None,
            "same_direction": None,
        }

    focal_a = known_effect_mod.focal_slot(resolved[FIT_VARIANT], FOCAL_SKU)
    focal_b = known_effect_mod.focal_slot(resolved[treatment], FOCAL_SKU)

    att_a: dict = {known_effect_mod.SYNTH: synth_attention[FIT_VARIANT]}
    att_b: dict = {known_effect_mod.SYNTH: synth_attention[treatment]}
    if FIT_VARIANT in real_attention and treatment in real_attention:
        att_a[known_effect_mod.REAL] = real_attention[FIT_VARIANT]
        att_b[known_effect_mod.REAL] = real_attention[treatment]

    result = known_effect_mod.known_effect(att_a, att_b, focal_a, focal_b)
    return {
        "focal_slot_a": focal_a,
        "focal_slot_b": focal_b,
        "real_att_a": _lookup(att_a.get(known_effect_mod.REAL), focal_a),
        "real_att_b": _lookup(att_b.get(known_effect_mod.REAL), focal_b),
        "synth_att_a": _lookup(att_a[known_effect_mod.SYNTH], focal_a),
        "synth_att_b": _lookup(att_b[known_effect_mod.SYNTH], focal_b),
        **result,
    }


def _lift_block(
    *,
    resolved: Mapping[str, Mapping],
    bundles: Mapping[str, Any],
    panel: Sequence[LoadedSession],
    brand: str,
    seed: int,
    unavailable: list,
) -> dict:
    """The `ad_to_purchase_lift` block, measured on the fit variant.

    Rows are the four personas plus the population. The real side is split by
    `archetype_label`, which takes the same four values as `persona_id`
    (SPEC 4.3's intake rule), and a session whose label is null still counts
    in the population row -- it is a shopper, just not a classified one.
    """
    planogram = resolved[FIT_VARIANT]
    bundle = bundles[FIT_VARIANT]
    brand_of_sku = sku_brands(planogram)
    exposure_slots = ad_slots_showing(planogram, FOCAL_CREATIVE)

    synth_rows = dict(bundle.per_persona)
    synth_rows[POPULATION_KEY] = bundle.population

    real_rows: dict = {}
    if panel:
        ordered = sorted(panel, key=lambda item: item.session_id)
        real_rows[POPULATION_KEY] = split_panel(
            [loaded.events for loaded in ordered], ad_slot_ids=exposure_slots
        )
        by_label: dict = {}
        for loaded in ordered:
            label = loaded.session.get("archetype_label")
            if label in synth_rows:
                by_label.setdefault(label, []).append(loaded.events)
        for label in sorted(by_label):
            real_rows[label] = split_panel(by_label[label], ad_slot_ids=exposure_slots)
        if not exposure_slots:
            unavailable.append(
                f"variant `{FIT_VARIANT}` shows no `{FOCAL_CREATIVE}` creative, so no real "
                "shopper counts as ad-exposed and the real lift is undefined"
            )

    block = ad_to_purchase_lift(
        synth_rows,
        brand_of_sku=brand_of_sku,
        brand=brand,
        real=real_rows or None,
        seed=seed,
        n_boot=N_BOOT,
        ci=CI,
    )
    return {
        "variant_id": FIT_VARIANT,
        "rows": [{"row": key, **block[key]} for key in sorted(block)],
    }


def _pre_registration_block(sessions: Sequence[LoadedSession], locks: Mapping) -> dict:
    """What was verified, counted -- the evidence the honesty claim rests on."""
    notes: list = []
    with_lock = [loaded for loaded in sessions if loaded.session_id in locks]
    checkable = [loaded for loaded in with_lock if ordering_checkable(loaded)]

    unlockable = [
        loaded.session_id
        for loaded in sessions
        if loaded.session_id not in locks and not loaded.accepted
    ]
    if unlockable:
        notes.append(
            f"{len(unlockable)} rejected session(s) have no committed lock and were counted "
            "but not verified: " + ", ".join(f"`{name}`" for name in sorted(unlockable))
        )

    unverifiable = [
        loaded.session_id for loaded in with_lock if not ordering_checkable(loaded)
    ]
    if unverifiable:
        notes.append(
            f"{len(unverifiable)} session(s) carry neither an event nor an `ended_at`, so "
            "their lock ordering could not be checked against anything: "
            + ", ".join(f"`{name}`" for name in sorted(unverifiable))
        )

    return {
        "n_locks_found": len(locks),
        "n_locks_verified": len(locks),
        "n_ordering_checked": len(checkable),
        "notes": notes,
    }


def _metrics_document(
    *,
    report_input: Mapping,
    per_variant_metrics: Mapping,
    ceiling: Optional[Mapping],
    relative: Optional[float],
    calibration: Optional[Mapping],
    known: Mapping,
    decision: Optional[Mapping],
    lift_block: Mapping,
    unavailable: list,
) -> Optional[dict]:
    """The `ExperimentMetrics` document, or None when it cannot be honest.

    schemas/metrics.schema.json REQUIRES `noise_ceiling` and
    `decision_agreement`, and `decision_agreement.winner_real` is typed as a
    plain string with `agree` a plain boolean. There is no honest way to fill
    those in without a real panel: a `winner_real` of "none" is a fabricated
    variant id, and an `agree` of false says the panels disagreed when in fact
    nobody was asked. So the document is emitted only when both exist, and
    RESULTS.md says plainly why it does not otherwise.

    This is deliberately not the same thing as the report: RESULTS.md is
    always written, and always reports what the synthetic panel alone can say.
    """
    if ceiling is None or decision is None:
        unavailable.append(
            "no `ExperimentMetrics` document was produced: every field "
            "schemas/metrics.schema.json requires is a real-vs-synthetic comparison, and one "
            "cannot be built without a real panel"
        )
        return None

    experiment = report_input["experiment"]
    panel = report_input["panel"]

    document: dict = {
        "experiment_id": experiment["experiment_id"],
        "fit_variant": experiment["fit_variant"],
        "holdout_variants": list(experiment["holdout_variants"]),
        "per_variant": dict(per_variant_metrics),
        "decision_agreement": dict(decision),
        # `variant_id` is ours, not the schema's -- the block is
        # additionalProperties: false, so it is dropped here and kept in the
        # report, where naming the variant the ceiling was measured on matters.
        "noise_ceiling": {
            key: value for key, value in ceiling.items() if key != "variant_id"
        },
        "n_real_accepted": panel["n_real_accepted"],
        "n_real_rejected": panel["n_real_rejected"],
        "n_synth": panel["n_synth"],
    }
    if relative is not None:
        document["relative_agreement"] = relative

    known_block = known_effect_mod.to_metrics_block(
        {
            "real_uplift": known["real_uplift"],
            "synth_uplift": known["synth_uplift"],
            "same_direction": known["same_direction"],
        }
    )
    if known_block:
        document["known_effect"] = known_block

    lift_rows = {
        row["row"]: {key: value for key, value in row.items() if key != "row"}
        for row in lift_block["rows"]
    }
    if lift_rows:
        document["ad_to_purchase_lift"] = lift_rows

    if calibration is not None:
        document["calibrated_shares"] = dict(calibration["fit"]["shares"])

    return document


def _experiment_id(
    sessions: Sequence[LoadedSession],
    bundles: Mapping[str, Any],
    variant_ids: Sequence[str],
    n_synth: int,
    seed: int,
) -> str:
    """A stable id for this exact set of inputs.

    Content-addressed rather than random or time-based: SPEC M8 asks for a
    byte-identical RESULTS.md from the same committed data, so the id has to
    be a function of the data. Two runs over the same sessions and the same
    simulation produce the same id; adding a session changes it.
    """
    digest = simcache.document_hash(
        {
            "sessions": sorted(loaded.session_id for loaded in sessions),
            "variants": list(variant_ids),
            "sim_run_ids": {
                variant_id: bundles[variant_id].population["sim_run_id"]
                for variant_id in variant_ids
            },
            "n_synth": int(n_synth),
            "seed": int(seed),
        }
    )
    return f"eval-{digest[:12]}"


def _lookup(vector: Optional[Mapping[str, float]], slot_id: Optional[str]) -> Optional[float]:
    if vector is None or slot_id is None:
        return None
    return float(vector.get(slot_id, 0.0))


def _relative(path: Path) -> str:
    try:
        return Path(path).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return Path(path).as_posix()


# ---------------------------------------------------------------------------
# Figures (SPEC M8)
# ---------------------------------------------------------------------------


def _draw_figures(figures_dir: Path, data: Mapping, attention: Mapping) -> tuple[list, list]:
    """Write every figure the data supports; name the ones it does not.

    A figure that needs the real panel is NOT drawn as an empty chart when
    there is none -- an axis of zero-height bars reads as a measured zero,
    which is the exact failure this whole script is written against. It is
    listed in RESULTS.md as not drawn, with the reason.
    """
    figures_dir.mkdir(parents=True, exist_ok=True)
    written: list = []
    skipped: list = []

    for row in data["per_variant"]:
        name = f"heatmap_{row['variant_id']}.png"
        _draw_attention(figures_dir / name, row, attention[row["variant_id"]])
        written.append(name)

    if data["noise_ceiling"] is not None:
        _draw_agreement(figures_dir / "agreement_vs_ceiling.png", data)
        written.append("agreement_vs_ceiling.png")
    else:
        skipped.append(
            {
                "name": "agreement_vs_ceiling.png",
                "reason": "there is no real panel to measure a noise ceiling on",
            }
        )

    if data["calibration"] is not None:
        _draw_calibration(figures_dir / "calibration_fit_vs_holdout.png", data)
        written.append("calibration_fit_vs_holdout.png")
    else:
        skipped.append(
            {
                "name": "calibration_fit_vs_holdout.png",
                "reason": "calibration needs a real panel on the fit variant",
            }
        )

    if data["panel"]["reject_reasons"]:
        _draw_reject_reasons(figures_dir / "reject_reasons.png", data)
        written.append("reject_reasons.png")
    else:
        skipped.append(
            {"name": "reject_reasons.png", "reason": "no rejected sessions were committed"}
        )

    return sorted(written), skipped


def _draw_attention(path: Path, row: Mapping, vectors: Mapping) -> None:
    """Synthetic attention per slot, with the real panel beside it when it exists."""
    variant_id = row["variant_id"]
    synthetic = vectors["synth"]
    real = vectors["real"]
    slots = vectors["slot_ids"]

    figure, axes = plt.subplots(figsize=(11, 4.5))
    positions = np.arange(len(slots))
    width = 0.4 if real is not None else 0.7

    axes.bar(positions - (width / 2 if real is not None else 0),
             [synthetic.get(slot, 0.0) for slot in slots], width, label="synthetic")
    if real is not None:
        axes.bar(positions + width / 2, [real.get(slot, 0.0) for slot in slots], width,
                 label="real")
        axes.set_title(f"Variant {variant_id}: real vs synthetic attention")
    else:
        axes.set_title(
            f"Variant {variant_id}: synthetic attention "
            f"(real panel {report_mod.NOT_COLLECTED})"
        )

    axes.set_xticks(positions)
    axes.set_xticklabels(slots, rotation=90, fontsize=7)
    axes.set_ylabel("fused attention")
    axes.legend()
    figure.tight_layout()
    figure.savefig(path, dpi=110)
    plt.close(figure)


def _draw_agreement(path: Path, data: Mapping) -> None:
    ceiling = data["noise_ceiling"]
    rows = [row for row in data["per_variant"] if row["attention_spearman"] is not None]

    figure, axes = plt.subplots(figsize=(7, 4.5))
    positions = np.arange(len(rows))
    axes.bar(positions, [row["attention_spearman"] for row in rows], 0.55,
             label="synthetic vs real")
    axes.axhline(ceiling["spearman_mean"], linestyle="--",
                 label=f"real panel vs itself (variant {ceiling['variant_id']})")
    axes.fill_between([-0.5, len(rows) - 0.5], ceiling["ci95"][0], ceiling["ci95"][1],
                      alpha=0.15, label=f"{CI_PERCENT}% interval")
    axes.set_xlim(-0.5, len(rows) - 0.5)
    axes.set_xticks(positions)
    axes.set_xticklabels([row["variant_id"] for row in rows])
    axes.set_ylabel("Spearman")
    axes.set_title("Agreement against the panel's own repeatability")
    axes.legend(fontsize=8)
    figure.tight_layout()
    figure.savefig(path, dpi=110)
    plt.close(figure)


def _draw_calibration(path: Path, data: Mapping) -> None:
    calibration = data["calibration"]
    rows = [("fit", calibration["fit"])] + [
        ("holdout", entry) for entry in calibration["holdout"]
    ]

    figure, axes = plt.subplots(figsize=(7, 4.5))
    positions = np.arange(len(rows))
    axes.bar(positions - 0.2, [entry["attention_spearman"] for _role, entry in rows], 0.4,
             label="attention Spearman")
    axes.bar(positions + 0.2, [entry["purchase_share_mae"] for _role, entry in rows], 0.4,
             label="purchase-share MAE")
    axes.set_xticks(positions)
    axes.set_xticklabels([f"{entry['variant_id']}\n{role}" for role, entry in rows])
    axes.set_title("Calibration: fit on one variant, held out on the rest")
    axes.legend(fontsize=8)
    figure.tight_layout()
    figure.savefig(path, dpi=110)
    plt.close(figure)


def _draw_reject_reasons(path: Path, data: Mapping) -> None:
    reasons = data["panel"]["reject_reasons"]

    figure, axes = plt.subplots(figsize=(7, 4.0))
    axes.bar([row["reason"] for row in reasons], [row["n"] for row in reasons], 0.55)
    axes.set_ylabel("sessions")
    axes.set_title(
        f"Rejected sessions by reason "
        f"({data['panel']['n_real_rejected']} of "
        f"{data['panel']['n_real_rejected'] + data['panel']['n_real_accepted']})"
    )
    figure.tight_layout()
    figure.savefig(path, dpi=110)
    plt.close(figure)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sessions-dir", default=str(DEFAULT_SESSIONS_DIR))
    parser.add_argument("--predictions-dir", default=str(DEFAULT_PREDICTIONS_DIR))
    parser.add_argument("--results", default=str(DEFAULT_RESULTS_PATH))
    parser.add_argument("--figures-dir", default=str(DEFAULT_FIGURES_DIR))
    parser.add_argument("--n-synth", type=int, default=N_SYNTH)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument(
        "--no-figures", action="store_true", help="skip docs/figures/*.png"
    )
    parser.add_argument(
        "--llm-headline", action="store_true",
        help="ask a model for the headline sentence. Off by default: it makes "
             "RESULTS.md non-reproducible and every run a live call.",
    )
    args = parser.parse_args(argv)

    outcome = run_eval(
        sessions_dir=Path(args.sessions_dir),
        predictions_dir=Path(args.predictions_dir),
        results_path=Path(args.results),
        figures_dir=Path(args.figures_dir),
        n_synth=args.n_synth,
        seed=args.seed,
        write_figures=not args.no_figures,
        llm_headline=args.llm_headline,
    )

    if outcome.exit_code != 0:
        print("eval FAILED: the committed evidence did not pass its integrity check.")
        print("")
        for failure in outcome.failures:
            print(f"  - {failure}")
        print("")
        print(f"{len(outcome.failures)} problem(s). RESULTS.md was not written.")
        return outcome.exit_code

    data = outcome.report_input
    print(f"experiment {data['experiment']['experiment_id']}")
    print(
        f"real panel: {data['panel']['n_real_accepted']} accepted, "
        f"{data['panel']['n_real_rejected']} rejected"
    )
    print(
        f"synthetic panel: {data['panel']['n_synth']} shoppers per variant "
        f"({len(data['per_variant'])} variants, seed {data['experiment']['seed']})"
    )
    print(
        f"prediction locks: {data['pre_registration']['n_locks_found']} found, "
        f"{data['pre_registration']['n_locks_verified']} hashes verified, "
        f"{data['pre_registration']['n_ordering_checked']} ordering checks passed"
    )
    print(f"headline: {outcome.headline_source}")
    print(
        f"metrics document: "
        f"{'built and schema-valid' if outcome.metrics is not None else 'not built'}"
    )
    for reason in data["unavailable"]:
        print(f"  not measured: {reason}")
    for name in data["figures"]["written"]:
        print(f"  wrote docs/figures/{name}")
    for skipped in data["figures"]["skipped"]:
        print(f"  skipped {skipped['name']}: {skipped['reason']}")
    print(f"wrote {_relative(Path(args.results))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
