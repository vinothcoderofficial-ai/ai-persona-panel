"""HTTP-level tests for POST /experiments and GET /experiments/{id} (S5).

This router wires together three already-implemented, already-tested pieces
that must not be reimplemented here (CLAUDE.md): resolve() (api/app/resolve.py),
the vectorised simulator (sim/simulator.py) and the fusion/metrics maths
(analytics/fusion.py, analytics/metrics.py). These tests exercise that wiring
through the real HTTP layer against the real committed seed data (demo_aisle,
variants A/B/C, the four persona policies) -- no mocks -- matching test_api.py's
style, and using the `client` fixture from conftest.py so every test gets its
own isolated in-memory DB.

The correctness-anchor tests independently recompute the true synthetic
attention ranking using the public sim/resolve/fusion APIs (not the router's
own internals), so a passing test proves real events are genuinely wired to
the synthetic prediction rather than the router merely agreeing with itself.

That synthetic vector is `fusion.fuse_synthetic` of the population SimResult,
NOT its raw `fixation_prob`: the real side fuses looking and interaction, so
the synthetic side has to as well or the Spearman compares two different
quantities. The `synth_attention` response key is unchanged (the dashboard
reads it); what it holds is now the fused vector.
"""
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import pytest

from analytics.fusion import fuse_session, fuse_synthetic
from api.app.resolve import resolve
from sim.simulator import build_store, combine, run

ROOT = Path(__file__).resolve().parents[2]
PERSONA_IDS = ("mission", "browser", "loyalist", "switcher")
N_RUNS = 10_000
SEED = 42

RESPONSE_KEYS = {
    "experiment_id", "variant_id", "session_id", "n_synth", "seed", "slot_ids",
    "real_attention", "synth_attention", "attention_spearman", "purchase_share_mae",
    "real_purchase_share", "synth_purchase_share",
}


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def base_planogram() -> dict:
    return load_json(ROOT / "data" / "planograms" / "demo_aisle.json")


def variant(name: str) -> dict:
    return load_json(ROOT / "data" / "variants" / f"{name}.json")


def personas() -> Dict[str, dict]:
    return {p: load_json(ROOT / "data" / "personas" / f"{p}.json") for p in PERSONA_IDS}


def policies(planogram_id: str) -> Dict[str, dict]:
    return {
        p: load_json(ROOT / "data" / "cache" / "policies" / f"{p}_{planogram_id}.json")
        for p in PERSONA_IDS
    }


def occupied_slot_ids(planogram: dict) -> List[str]:
    return [
        slot["slot_id"]
        for bay in planogram["bays"]
        for shelf in bay["shelves"]
        for slot in shelf["slots"]
        if slot["sku_id"] is not None
    ]


def population_for(variant_name: str = "A"):
    """Independently reproduces the simulation POST /experiments must run for
    `variant_name`, using the public sim/resolve APIs directly, not the
    router's internals, so tests built on this check the real wiring.

    Returns `(population, resolved, slot_ids)`: the combined population
    SimResult, the resolved planogram it was produced over (which the
    synthetic fusion needs for its sku -> slot map) and the shared slot
    vocabulary.
    """
    resolved = resolve(base_planogram(), variant(variant_name))
    slot_ids = occupied_slot_ids(resolved)
    store = build_store(resolved)
    persona_docs = personas()
    policy_docs = policies(resolved["planogram_id"])
    results = [
        run(store, policy_docs[p], n_runs=N_RUNS, seed=SEED, variant_id=variant_name)
        for p in PERSONA_IDS
    ]
    shares = [persona_docs[p]["share_of_population"] for p in PERSONA_IDS]
    population = combine(results, shares)
    return population, resolved, slot_ids


def valid_session_body(variant_id: str = "A", mode: str = "cursor_only") -> dict:
    return {
        "session_id": str(uuid.uuid4()),
        "variant_id": variant_id,
        "consent": True,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "screen_w": 1440,
        "screen_h": 900,
        "mode": mode,
    }


def create_session(client, variant_id: str = "A", mode: str = "cursor_only") -> str:
    body = valid_session_body(variant_id, mode)
    resp = client.post("/sessions", json=body)
    assert resp.status_code == 201, resp.text
    return body["session_id"]


def post_events(client, session_id: str, events: List[Dict[str, Any]]) -> None:
    resp = client.post(f"/sessions/{session_id}/events", json=events)
    assert resp.status_code == 200, resp.text


@pytest.fixture(scope="module")
def population_a():
    """The true synthetic prediction for variant A, as
    `(population, resolved, slot_ids)`. Computed once (it is a real
    ~40,000-shopper simulation) and shared by every test in this module that
    needs it, instead of recomputing it per test.
    """
    return population_for("A")


# ---------------------------------------------------------------------------
# Round trip and response shape
# ---------------------------------------------------------------------------


def test_experiment_round_trip_shape(client):
    session_id = create_session(client)
    post_events(client, session_id, [
        {"t_ms": 1000, "type": "cursor_dwell", "station_id": "B1",
         "payload": {"slot_id": "B1S1P1", "dur_ms": 900}},
        {"t_ms": 2000, "type": "hover", "station_id": "B1",
         "payload": {"slot_id": "B1S1P2", "sku_id": "SKU_002"}},
        {"t_ms": 3000, "type": "add_to_cart", "station_id": "B1",
         "payload": {"slot_id": "B1S1P1", "sku_id": "SKU_001"}},
    ])

    resp = client.post("/experiments", json={"variant_id": "A", "session_id": session_id})
    assert resp.status_code == 201, resp.text
    body = resp.json()

    assert set(body.keys()) == RESPONSE_KEYS
    assert body["experiment_id"].startswith("exp_")
    assert body["variant_id"] == "A"
    assert body["session_id"] == session_id
    assert body["n_synth"] == 10_000
    assert body["seed"] == 42
    assert len(body["slot_ids"]) == 24
    assert set(body["slot_ids"]) == set(occupied_slot_ids(resolve(base_planogram(), variant("A"))))
    assert set(body["real_attention"].keys()) == set(body["slot_ids"])
    assert set(body["synth_attention"].keys()) == set(body["slot_ids"])
    assert -1.0 <= body["attention_spearman"] <= 1.0
    assert body["purchase_share_mae"] >= 0.0
    assert body["real_purchase_share"] == {"SKU_001": 1.0}


def test_get_experiment_returns_same_document_as_post(client):
    session_id = create_session(client)
    resp = client.post("/experiments", json={"variant_id": "A", "session_id": session_id})
    assert resp.status_code == 201, resp.text
    created = resp.json()

    resp = client.get(f"/experiments/{created['experiment_id']}")
    assert resp.status_code == 200
    assert resp.json() == created


# ---------------------------------------------------------------------------
# Correctness anchor: real events genuinely drive the Spearman value
# ---------------------------------------------------------------------------


def test_attention_spearman_strongly_positive_when_events_match_synthetic_ranking(
    client, population_a,
):
    population, resolved, slot_ids = population_a
    synth_attention = fuse_synthetic(population, resolved, slot_ids)
    ranked = sorted(slot_ids, key=lambda s: synth_attention[s])  # low -> high

    session_id = create_session(client)
    post_events(client, session_id, [
        {"t_ms": 1000 * (i + 1), "type": "cursor_dwell", "station_id": None,
         "payload": {"slot_id": slot_id, "dur_ms": 1000.0 * (i + 1)}}
        for i, slot_id in enumerate(ranked)
    ])

    resp = client.post("/experiments", json={"variant_id": "A", "session_id": session_id})
    assert resp.status_code == 201, resp.text
    rho = resp.json()["attention_spearman"]
    print(f"\nsame-order attention_spearman = {rho:.4f}")
    assert rho > 0.9


def test_attention_spearman_strongly_negative_when_events_reverse_synthetic_ranking(
    client, population_a,
):
    population, resolved, slot_ids = population_a
    synth_attention = fuse_synthetic(population, resolved, slot_ids)
    ranked = sorted(slot_ids, key=lambda s: synth_attention[s])  # low -> high
    reverse_ranked = list(reversed(ranked))

    session_id = create_session(client)
    post_events(client, session_id, [
        {"t_ms": 1000 * (i + 1), "type": "cursor_dwell", "station_id": None,
         "payload": {"slot_id": slot_id, "dur_ms": 1000.0 * (i + 1)}}
        for i, slot_id in enumerate(reverse_ranked)
    ])

    resp = client.post("/experiments", json={"variant_id": "A", "session_id": session_id})
    assert resp.status_code == 201, resp.text
    rho = resp.json()["attention_spearman"]
    print(f"\nreverse-order attention_spearman = {rho:.4f}")
    assert rho < -0.9


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_session_with_no_events_yields_all_zero_attention_and_zero_spearman(client):
    session_id = create_session(client)

    resp = client.post("/experiments", json={"variant_id": "A", "session_id": session_id})
    assert resp.status_code == 201, resp.text
    body = resp.json()

    assert body["real_attention"] == {slot_id: 0.0 for slot_id in body["slot_ids"]}
    assert body["attention_spearman"] == 0.0


def test_unknown_variant_id_404(client):
    session_id = create_session(client)
    resp = client.post("/experiments", json={"variant_id": "NOPE", "session_id": session_id})
    assert resp.status_code == 404


def test_unknown_session_id_404(client):
    resp = client.post("/experiments", json={"variant_id": "A", "session_id": "does-not-exist"})
    assert resp.status_code == 404


def test_real_purchase_share_reflects_add_to_cart_events(client):
    session_id = create_session(client)
    post_events(client, session_id, [
        {"t_ms": 1000, "type": "hover", "station_id": "B1",
         "payload": {"slot_id": "B1S2P1", "sku_id": "SKU_003"}},
        {"t_ms": 2000, "type": "add_to_cart", "station_id": "B1",
         "payload": {"slot_id": "B1S1P1", "sku_id": "SKU_001"}},
        {"t_ms": 3000, "type": "add_to_cart", "station_id": "B1",
         "payload": {"slot_id": "B1S1P1", "sku_id": "SKU_001"}},
        {"t_ms": 4000, "type": "add_to_cart", "station_id": "B1",
         "payload": {"slot_id": "B1S1P2", "sku_id": "SKU_002"}},
    ])

    resp = client.post("/experiments", json={"variant_id": "A", "session_id": session_id})
    assert resp.status_code == 201, resp.text
    body = resp.json()

    assert body["real_purchase_share"] == pytest.approx({"SKU_001": 2 / 3, "SKU_002": 1 / 3})


# ---------------------------------------------------------------------------
# both sides of the comparison are fused the same way
# ---------------------------------------------------------------------------


def test_synth_attention_is_the_fused_synthetic_vector_not_raw_fixation_prob(
    client, population_a,
):
    """`synth_attention` keeps its response key (the dashboard reads it) but
    now holds `fuse_synthetic` of the population result, so it is comparable
    term for term with the fused real vector instead of being looking-only.

    Both halves are asserted: it MATCHES the fused vector, and it DIFFERS from
    the raw `fixation_prob` the endpoint used to return -- without the second
    half this test would still pass if fusion silently did nothing.
    """
    population, resolved, slot_ids = population_a
    fused = fuse_synthetic(population, resolved, slot_ids)
    raw = {slot_id: population["fixation_prob"].get(slot_id, 0.0) for slot_id in slot_ids}

    session_id = create_session(client)
    resp = client.post("/experiments", json={"variant_id": "A", "session_id": session_id})
    assert resp.status_code == 201, resp.text
    synth_attention = resp.json()["synth_attention"]

    assert synth_attention == pytest.approx(fused)
    assert synth_attention != pytest.approx(raw)
    # Every slot the shelf carries is present, and the purchases moved real
    # weight around rather than being a rounding-level nudge.
    assert set(synth_attention) == set(slot_ids)
    assert max(abs(fused[s] - raw[s]) for s in slot_ids) > 0.01


def test_a_webcam_session_is_fused_with_webcam_weights_on_both_sides(client, population_a):
    """A webcam session's real attention weights fixations 0.5 / cursor 0.3 /
    interaction 0.2, so its synthetic counterpart must use the matching
    0.8 looking / 0.2 interaction split -- comparing a webcam-fused panel
    against a cursor_only-fused synthetic vector would put the mismatch back.

    The session's `mode` is what selects both, so this also pins down that the
    router reads it from the session document rather than defaulting.
    """
    population, resolved, slot_ids = population_a
    events = [
        {"t_ms": 1000, "type": "fixation", "station_id": "B1",
         "payload": {"x": 0.4, "y": 0.6, "dur_ms": 800, "slot_id": slot_ids[0],
                     "shelf_id": None}},
        {"t_ms": 2000, "type": "fixation", "station_id": "B1",
         "payload": {"x": 0.5, "y": 0.5, "dur_ms": 400, "slot_id": slot_ids[1],
                     "shelf_id": None}},
        {"t_ms": 3000, "type": "cursor_dwell", "station_id": "B1",
         "payload": {"slot_id": slot_ids[2], "dur_ms": 900}},
        {"t_ms": 4000, "type": "add_to_cart", "station_id": "B1",
         "payload": {"slot_id": slot_ids[0], "sku_id": "SKU_001"}},
    ]

    session_id = create_session(client, mode="webcam")
    post_events(client, session_id, events)
    resp = client.post("/experiments", json={"variant_id": "A", "session_id": session_id})
    assert resp.status_code == 201, resp.text
    body = resp.json()

    assert body["real_attention"] == pytest.approx(
        fuse_session(events, slot_ids, mode="webcam"))
    assert body["synth_attention"] == pytest.approx(
        fuse_synthetic(population, resolved, slot_ids, mode="webcam"))

    # ...and webcam really is a different fusion from the cursor_only default
    # on both sides, so the assertions above are not vacuous.
    assert body["real_attention"] != pytest.approx(fuse_session(events, slot_ids))
    assert body["synth_attention"] != pytest.approx(
        fuse_synthetic(population, resolved, slot_ids))


def test_a_cursor_only_session_still_uses_cursor_only_weights(client, population_a):
    """The default mode is unchanged: a cursor_only session fuses both sides
    with 0.7 looking / 0.3 interaction and ignores its fixation events."""
    population, resolved, slot_ids = population_a
    events = [
        {"t_ms": 1000, "type": "fixation", "station_id": "B1",
         "payload": {"x": 0.4, "y": 0.6, "dur_ms": 800, "slot_id": slot_ids[0],
                     "shelf_id": None}},
        {"t_ms": 2000, "type": "cursor_dwell", "station_id": "B1",
         "payload": {"slot_id": slot_ids[2], "dur_ms": 900}},
    ]

    session_id = create_session(client, mode="cursor_only")
    post_events(client, session_id, events)
    resp = client.post("/experiments", json={"variant_id": "A", "session_id": session_id})
    assert resp.status_code == 201, resp.text
    body = resp.json()

    assert body["real_attention"] == pytest.approx(fuse_session(events, slot_ids))
    assert body["synth_attention"] == pytest.approx(
        fuse_synthetic(population, resolved, slot_ids, mode="cursor_only"))
    assert body["real_attention"][slot_ids[0]] == 0.0  # the fixation carries no weight


def test_synth_attention_follows_a_moved_sku_to_its_new_slot(client):
    """Variant B moves SKU_008 from the bottom shelf to eye level. The
    synthetic interaction channel is keyed by sku, so the endpoint must credit
    that sku's purchases to B1S3P2 under variant B -- taken from the resolved
    planogram, not from a slot map cached off variant A.
    """
    population_b, resolved_b, slot_ids_b = population_for("B")
    expected = fuse_synthetic(population_b, resolved_b, slot_ids_b)

    session_id = create_session(client, variant_id="B")
    resp = client.post("/experiments", json={"variant_id": "B", "session_id": session_id})
    assert resp.status_code == 201, resp.text
    body = resp.json()

    assert body["synth_attention"] == pytest.approx(expected)
    assert "B1S3P2" in body["slot_ids"]
    assert "B1S5P1" not in body["slot_ids"]  # it is the empty slot under B
    # SKU_008's purchases reach eye level, where nothing but its own
    # fixation share would otherwise put them.
    assert body["synth_attention"]["B1S3P2"] > 0.0
