"""HTTP-level tests for POST /whatif (S15).

Uses the `client` fixture from conftest.py, which points the whole app at an
isolated in-memory SQLite database (startup seeding included) so the real
shoppertwin.db is never touched. The app's lifespan also runs the what-if
warm-up, so these tests exercise the same warm path production uses.
"""
import json
import math
import statistics
import time
from pathlib import Path

from jsonschema import Draft7Validator

from api.app.routers import whatif as whatif_module

ROOT = Path(__file__).resolve().parents[2]

PERSONA_IDS = {"browser", "loyalist", "mission", "switcher"}

# The known effect the whole project is benchmarked on: the focal SKU sits on
# the bottom shelf in the base planogram and moves to eye level in variant B.
FOCAL_SKU = "SKU_008"
EYE_LEVEL_SLOT = "B1S3P2"
VARIANT_B_PATCH = {"op": "move_sku", "sku_id": FOCAL_SKU, "to_slot_id": EYE_LEVEL_SLOT}


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def base_planogram() -> dict:
    return load_json(ROOT / "data" / "planograms" / "demo_aisle.json")


def simresult_validator() -> Draft7Validator:
    return Draft7Validator(load_json(ROOT / "schemas" / "simresult.schema.json"))


def whatif_body(patches, **extra) -> dict:
    return {"base_planogram_id": "demo_aisle", "patches": patches, **extra}


def post_whatif(client, patches, **extra):
    resp = client.post("/whatif", json=whatif_body(patches, **extra))
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Acceptance: p95 < 1,000 ms over 20 calls (docs/PLAN.md S15, SPEC M9)
# ---------------------------------------------------------------------------


def test_whatif_p95_under_1000ms(client, capsys):
    # One un-timed call first: production warms the policies and the baseline
    # at startup (the `client` fixture runs the lifespan, so that has already
    # happened), and this additionally pays any first-call import/JIT cost so
    # the 20 measured calls reflect the steady state the UI actually sees.
    post_whatif(client, [VARIANT_B_PATCH])

    elapsed_ms = []
    for _ in range(20):
        start = time.perf_counter()
        body = post_whatif(client, [VARIANT_B_PATCH])
        elapsed_ms.append((time.perf_counter() - start) * 1000.0)
        assert body["elapsed_ms"] >= 0

    ordered = sorted(elapsed_ms)
    p50 = statistics.median(ordered)
    p95 = ordered[math.ceil(0.95 * len(ordered)) - 1]  # nearest-rank
    with capsys.disabled():
        print(f"\n[whatif] 20 calls: p50={p50:.1f} ms  p95={p95:.1f} ms  max={ordered[-1]:.1f} ms")

    assert p95 < 1000.0, f"p95 {p95:.1f} ms exceeds the 1,000 ms budget"


# ---------------------------------------------------------------------------
# The no-op case must be exactly neutral
# ---------------------------------------------------------------------------


def test_empty_patches_equals_cached_baseline(client):
    body = post_whatif(client, [], focal_sku_id=FOCAL_SKU)

    baseline = whatif_module.get_baseline(
        base_planogram(), whatif_module.DEFAULT_N_SYNTH, whatif_module.DEFAULT_SEED
    )

    assert body["population_fixation_prob"] == baseline.population["fixation_prob"]
    assert body["sim_run_id"] == baseline.population["sim_run_id"]
    assert body["ad_slot_attention"] == baseline.population["ad_slot_attention"]
    # A focal SKU was named, so both keys are present and exactly zero.
    assert body["lift_vs_baseline"] == {
        "focal_sku_attention": 0.0,
        "focal_sku_purchase_share": 0.0,
    }


def test_baseline_is_cached_not_recomputed(client):
    first = whatif_module.get_baseline(base_planogram(), whatif_module.DEFAULT_N_SYNTH,
                                       whatif_module.DEFAULT_SEED)
    second = whatif_module.get_baseline(base_planogram(), whatif_module.DEFAULT_N_SYNTH,
                                        whatif_module.DEFAULT_SEED)
    assert first is second


# ---------------------------------------------------------------------------
# The known effect: bottom shelf -> eye level lifts the focal SKU's attention
# ---------------------------------------------------------------------------


def test_move_to_eye_level_lifts_focal_attention(client, capsys):
    body = post_whatif(client, [VARIANT_B_PATCH])

    lift = body["lift_vs_baseline"]["focal_sku_attention"]
    purchase_lift = body["lift_vs_baseline"]["focal_sku_purchase_share"]
    with capsys.disabled():
        print(f"\n[whatif] {FOCAL_SKU} bottom shelf -> eye level: "
              f"attention lift={lift:+.4f}  purchase-share lift={purchase_lift:+.4f}")

    assert lift is not None
    assert lift > 0.0, f"moving {FOCAL_SKU} to eye level lowered attention (lift={lift})"

    # The focal SKU now sits at eye level, so its new slot is the one that
    # carries the attention; its old bottom-shelf slot is empty and gone.
    assert EYE_LEVEL_SLOT in body["population_fixation_prob"]


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_same_seed_and_patches_are_identical(client):
    first = post_whatif(client, [VARIANT_B_PATCH], seed=7)
    second = post_whatif(client, [VARIANT_B_PATCH], seed=7)

    assert first["sim_run_id"] == second["sim_run_id"]
    assert first["population_fixation_prob"] == second["population_fixation_prob"]


def test_different_seed_gives_a_different_result(client):
    first = post_whatif(client, [VARIANT_B_PATCH], seed=7)
    second = post_whatif(client, [VARIANT_B_PATCH], seed=8)

    assert first["sim_run_id"] != second["sim_run_id"]
    assert first["population_fixation_prob"] != second["population_fixation_prob"]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


def test_unknown_planogram_returns_404(client):
    resp = client.post("/whatif", json={"base_planogram_id": "nope", "patches": []})
    assert resp.status_code == 404


def test_patch_naming_an_unknown_slot_returns_400(client):
    resp = client.post("/whatif", json=whatif_body(
        [{"op": "move_sku", "sku_id": FOCAL_SKU, "to_slot_id": "NO_SUCH_SLOT"}]
    ))
    assert resp.status_code == 400
    assert "NO_SUCH_SLOT" in resp.json()["detail"]


def test_patch_failing_the_variant_schema_returns_422(client):
    resp = client.post("/whatif", json=whatif_body([{"op": "move_sku", "sku_id": FOCAL_SKU}]))
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Shape of the response
# ---------------------------------------------------------------------------


def test_ad_only_patch_omits_the_focal_keys(client):
    body = post_whatif(client, [
        {"op": "set_ad_creative", "ad_slot_id": "B3_ENDCAP", "creative_id": "AD_2"}
    ])

    # No SKU is implied by an ad-creative swap, so there is nothing honest to
    # report for these two: they are absent, not null and not zero.
    assert body["lift_vs_baseline"] == {}
    assert "B3_ENDCAP" in body["ad_slot_attention"]


def test_per_persona_holds_all_four_valid_simresults(client):
    body = post_whatif(client, [VARIANT_B_PATCH])

    assert set(body["per_persona"]) == PERSONA_IDS

    validator = simresult_validator()
    for persona_id, result in body["per_persona"].items():
        errors = sorted(validator.iter_errors(result), key=str)
        assert not errors, f"{persona_id}: {[e.message for e in errors]}"
        assert result["persona_id"] == persona_id
        assert result["n_runs"] == whatif_module.DEFAULT_N_SYNTH
