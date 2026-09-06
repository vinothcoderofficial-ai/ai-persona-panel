"""GET /ai/status, GET|POST /ai/personas/{id}/policy, GET /ai/personas/{id}/trace (S26).

The AI layer existed in `sim/` from S12 and was reachable from nothing: no module
under `api/` or `web/` imported `sim/llm_client.py`, `sim/policy.py` or
`sim/slow_agent.py`, so at runtime the "AI persona panel" was a numpy simulator
reading four pre-baked JSON files. These endpoints are the seam that makes the
language model both **visible** (what it was asked, what it answered, which model
answered) and **triggerable** (ask it again, now, from a screen).

Two invariants this suite exists to protect:

1. **POST never overwrites a committed policy.** `data/cache/policies/` holds the
   pre-registered inputs to every simulation, and `predictions/` locks are hashed
   against the behaviour they produce. A button that silently rewrote them would
   invalidate the evidence this whole project is built to produce, so a re-ask
   writes to a *preview* directory and returns a comparison instead.
2. **An unavailable model is reported, never faked.** Offline, no key, a model
   that will not produce schema-valid JSON, a policy naming a brand the store does
   not stock - each has its own status code and its own message. None of them
   returns a plausible-looking policy.
"""
import json
from pathlib import Path
from typing import Any, Dict, Iterator, List

import pytest
from fastapi.testclient import TestClient

from api.app.main import app
from api.app.routers import ai

PLANOGRAM_ID = "demo_aisle"

# A complete, schema-valid policy for `mission` against the seed planogram. Every
# brand named here is stocked by `data/planograms/demo_aisle.json`; the semantic
# check in `sim/policy.py` rejects any that is not, which is what test
# `test_post_policy_naming_unstocked_brand_is_422` exercises.
FRESH_POLICY: Dict[str, Any] = {
    "persona_id": "mission",
    "goal_categories": ["chips", "cola"],
    "time_budget_s": {"mean": 40.0, "sd": 8.0},
    "exploration": 0.09,
    "brand_affinity": {
        "_default": 0.5,
        "Crunch": 0.7,
        "Nimbus": 0.5,
        "Orchid": 0.45,
        "Zapp": 0.5,
    },
    "price_sensitivity": 0.4,
    "promo_sensitivity": 0.2,
    "ad_receptivity": 0.15,
    "purchase_threshold": 0.25,
    "dwell_ms": {"mu": 6.0, "sigma": 0.45},
    "fixations_per_station": {"lam": 2.5},
}


class FakeResponse:
    def __init__(self, body: Dict[str, Any]) -> None:
        self._body = body

    def json(self) -> Dict[str, Any]:
        return self._body

    def raise_for_status(self) -> None:
        return None


class FakeTransport:
    """The `client` seam `sim/llm_client.complete_json` already accepts.

    Anthropic wire shape, because that is the provider default and these tests
    are about the router rather than about which provider is configured.
    """

    def __init__(self, *payloads: Dict[str, Any]) -> None:
        self.payloads = list(payloads)
        self.calls: List[Dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        payload = self.payloads[min(len(self.calls) - 1, len(self.payloads) - 1)]
        return FakeResponse({"content": [{"type": "text", "text": json.dumps(payload)}]})


@pytest.fixture(name="preview_dir", autouse=True)
def preview_dir_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the re-ask output directory, so no test writes into the repo.

    `ai.py` reads this module global fresh on every call - the pattern
    `api/app/prediction.py` already uses for `PREDICTIONS_DIR` - so patching it
    here covers the router however it was imported.
    """
    directory = tmp_path / "preview"
    monkeypatch.setattr(ai, "POLICY_PREVIEW_DIR", directory)
    return directory


@pytest.fixture(name="ask_model")
def ask_model_fixture() -> Iterator[Any]:
    """Install a fake transport for POST, and hand the test the object back."""
    installed: Dict[str, Any] = {}

    def install(*payloads: Dict[str, Any]) -> FakeTransport:
        transport = FakeTransport(*payloads)
        app.dependency_overrides[ai.get_llm_client] = lambda: transport
        installed["transport"] = transport
        return transport

    yield install
    app.dependency_overrides.pop(ai.get_llm_client, None)


# ---------------------------------------------------------------------------
# GET /ai/status - what is wired up, and what it has already produced
# ---------------------------------------------------------------------------

def test_status_reports_the_configured_provider_and_model(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LLM_MODEL", "llama3.1:8b")

    body = client.get("/ai/status").json()

    assert body["provider"] == "ollama"
    assert body["model"] == "llama3.1:8b"


def test_status_defaults_to_anthropic_when_nothing_is_configured(
    client: TestClient,
) -> None:
    body = client.get("/ai/status").json()

    assert body["provider"] == "anthropic"
    assert body["model"] == "claude-haiku-4-5-20251001"


def test_status_says_it_cannot_call_when_offline(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_OFFLINE", "1")

    body = client.get("/ai/status").json()

    assert body["offline"] is True
    assert body["can_call"] is False
    assert "LLM_OFFLINE" in body["reason"]


def test_status_says_it_cannot_call_anthropic_without_a_key(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    body = client.get("/ai/status").json()

    assert body["api_key_set"] is False
    assert body["can_call"] is False
    assert "LLM_API_KEY" in body["reason"]


def test_status_can_call_ollama_with_no_key(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A local daemon needs no credential, and the panel must not claim it does."""
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    body = client.get("/ai/status").json()

    assert body["can_call"] is True
    assert body["reason"] is None


def test_status_never_echoes_the_api_key(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_API_KEY", "sk-do-not-leak-me")

    raw = client.get("/ai/status").text

    assert "sk-do-not-leak-me" not in raw


def test_status_lists_every_persona_with_its_cache_state(client: TestClient) -> None:
    body = client.get("/ai/status").json()

    by_id = {p["persona_id"]: p for p in body["personas"]}
    assert set(by_id) == {"mission", "browser", "loyalist", "switcher"}

    mission = by_id["mission"]
    assert mission["policy_cached"] is True
    assert mission["trace_cached"] is True
    # The trace records which model produced it, and the panel's whole claim is
    # that a real model did - so a trace that cannot name one is not evidence.
    assert isinstance(mission["trace_model"], str) and mission["trace_model"]
    assert mission["trace_n_shoppers"] > 0
    assert mission["share_of_population"] > 0
    assert mission["description"]


def test_status_reports_a_missing_trace_as_missing(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ai, "TRACES_DIR", tmp_path / "no-traces")

    body = client.get("/ai/status").json()

    for persona in body["personas"]:
        assert persona["trace_cached"] is False
        assert persona["trace_model"] is None
        assert persona["trace_n_shoppers"] is None


# ---------------------------------------------------------------------------
# GET /ai/personas/{id}/policy - what the model said, and is being simulated
# ---------------------------------------------------------------------------

def test_get_policy_returns_the_committed_policy(client: TestClient) -> None:
    body = client.get("/ai/personas/mission/policy").json()

    assert body["persona_id"] == "mission"
    assert body["planogram_id"] == PLANOGRAM_ID
    assert body["source"] == "cache"
    committed = json.loads(
        (ai.POLICIES_DIR / f"mission_{PLANOGRAM_ID}.json").read_text(encoding="utf-8")
    )
    assert body["policy"] == committed


def test_get_policy_for_an_unknown_persona_is_404(client: TestClient) -> None:
    response = client.get("/ai/personas/nobody/policy")

    assert response.status_code == 404
    assert "nobody" in response.json()["detail"]


def test_get_policy_includes_the_prompt_that_produced_it(client: TestClient) -> None:
    """The panel shows what the model was asked, not only what it answered.

    A policy with no visible prompt is a number with no provenance, and the
    point of this screen is that a viewer can see the actual instruction.
    """
    body = client.get("/ai/personas/mission/policy").json()

    prompt = body["prompt"]
    assert "chips" in prompt  # the planogram's real categories are interpolated
    assert "Crunch" in prompt  # and its real brands
    assert "{description}" not in prompt  # rendered, not the raw template


# ---------------------------------------------------------------------------
# POST /ai/personas/{id}/policy - ask the model again, now
# ---------------------------------------------------------------------------

def test_post_policy_asks_the_model_and_returns_what_it_said(
    client: TestClient, ask_model: Any
) -> None:
    transport = ask_model(FRESH_POLICY)

    body = client.post("/ai/personas/mission/policy").json()

    assert len(transport.calls) == 1
    assert body["policy"] == FRESH_POLICY
    assert body["source"] == "llm"
    assert body["model"] == "claude-haiku-4-5-20251001"
    assert body["elapsed_s"] >= 0


def test_post_policy_does_not_overwrite_the_committed_policy(
    client: TestClient, ask_model: Any
) -> None:
    """The invariant this endpoint exists to respect.

    `data/cache/policies/` is a pre-registered input: `predictions/` locks are
    hashed against the simulation these numbers produce. A re-ask is allowed to
    show a different answer; it is not allowed to change what is being run.
    """
    committed_path = ai.POLICIES_DIR / f"mission_{PLANOGRAM_ID}.json"
    before = committed_path.read_text(encoding="utf-8")
    ask_model(FRESH_POLICY)

    client.post("/ai/personas/mission/policy")

    assert committed_path.read_text(encoding="utf-8") == before


def test_post_policy_writes_the_fresh_answer_to_the_preview_directory(
    client: TestClient, ask_model: Any, preview_dir: Path
) -> None:
    ask_model(FRESH_POLICY)

    client.post("/ai/personas/mission/policy")

    written = preview_dir / f"mission_{PLANOGRAM_ID}.json"
    assert json.loads(written.read_text(encoding="utf-8")) == FRESH_POLICY


def test_post_policy_reports_which_fields_the_model_changed(
    client: TestClient, ask_model: Any
) -> None:
    ask_model(FRESH_POLICY)

    body = client.post("/ai/personas/mission/policy").json()

    assert body["differs"] is True
    # FRESH_POLICY moves exploration (0.05 -> 0.09), Crunch affinity
    # (0.55 -> 0.7) and the time budget; it leaves price_sensitivity alone.
    assert "exploration" in body["changed_fields"]
    assert "brand_affinity" in body["changed_fields"]
    assert "price_sensitivity" not in body["changed_fields"]
    assert body["committed"]["exploration"] == 0.05


def test_post_policy_reports_an_identical_answer_as_identical(
    client: TestClient, ask_model: Any
) -> None:
    """Temperature 0 against the same prompt should reproduce, and when it does
    the screen must say so rather than inventing a difference."""
    committed = json.loads(
        (ai.POLICIES_DIR / f"mission_{PLANOGRAM_ID}.json").read_text(encoding="utf-8")
    )
    ask_model(committed)

    body = client.post("/ai/personas/mission/policy").json()

    assert body["differs"] is False
    assert body["changed_fields"] == []


def test_post_policy_when_offline_is_503_and_says_why(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_OFFLINE", "1")

    response = client.post("/ai/personas/mission/policy")

    assert response.status_code == 503
    assert "LLM_OFFLINE" in response.json()["detail"]


def test_post_policy_without_a_key_is_503_and_says_why(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    response = client.post("/ai/personas/mission/policy")

    assert response.status_code == 503
    assert "LLM_API_KEY" in response.json()["detail"]


def test_post_policy_naming_unstocked_brand_is_422(
    client: TestClient, ask_model: Any
) -> None:
    """`sim/policy.py`'s semantic layer, surfaced as a status code.

    The JSON schema allows any brand key, so this is the only thing standing
    between a confident-sounding hallucination and the simulator.
    """
    invented = dict(FRESH_POLICY)
    invented["brand_affinity"] = {**FRESH_POLICY["brand_affinity"], "Fictional": 0.9}
    ask_model(invented)

    response = client.post("/ai/personas/mission/policy")

    assert response.status_code == 422
    assert "Fictional" in response.json()["detail"]


def test_post_policy_that_never_validates_is_502(
    client: TestClient, ask_model: Any
) -> None:
    ask_model({"persona_id": "mission"})  # missing every other required field

    response = client.post("/ai/personas/mission/policy")

    assert response.status_code == 502
    assert "schema-valid" in response.json()["detail"]


def test_post_policy_for_an_unknown_persona_is_404(
    client: TestClient, ask_model: Any
) -> None:
    ask_model(FRESH_POLICY)

    response = client.post("/ai/personas/nobody/policy")

    assert response.status_code == 404


def test_post_policy_for_an_unknown_persona_calls_no_model(
    client: TestClient, ask_model: Any
) -> None:
    """A 404 must cost nothing. Re-asking is a paid call to a hosted model."""
    transport = ask_model(FRESH_POLICY)

    client.post("/ai/personas/nobody/policy")

    assert transport.calls == []


# ---------------------------------------------------------------------------
# GET /ai/personas/{id}/trace - the shopping the model actually reasoned through
# ---------------------------------------------------------------------------

def test_get_trace_returns_the_committed_trace(client: TestClient) -> None:
    body = client.get("/ai/personas/mission/trace").json()

    assert body["persona_id"] == "mission"
    assert body["planogram_id"] == PLANOGRAM_ID
    assert body["n_shoppers"] == len(body["shoppers"])
    assert body["model"]

    first = body["shoppers"][0]
    assert first["turns"], "a shopper with no turns is not a trace"
    turn = first["turns"][0]
    assert turn["action"] in {
        "look", "approach", "pickup", "add_to_cart", "next_station", "checkout"
    }
    # The reason is the whole point: it is the model's own words, and it is what
    # makes this screen evidence of reasoning rather than a chart of outcomes.
    assert turn["reason"]


def test_get_trace_names_the_products_in_each_cart(client: TestClient) -> None:
    """`cart` is sku ids; `cart_detail` is what a human can read. The endpoint
    must carry the second, or the screen is a list of SKU_008s again."""
    body = client.get("/ai/personas/mission/trace").json()

    detail = body["shoppers"][0]["cart_detail"]
    assert detail
    assert all(line["name"] for line in detail)
    assert all(line["slot_id"] for line in detail)


def test_get_trace_for_an_unknown_persona_is_404(client: TestClient) -> None:
    assert client.get("/ai/personas/nobody/trace").status_code == 404


def test_get_trace_when_none_was_generated_is_404_naming_the_command(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing trace is a run that has not happened, and the message should
    say which command makes it happen rather than only that a file is absent."""
    monkeypatch.setattr(ai, "TRACES_DIR", tmp_path / "no-traces")

    response = client.get("/ai/personas/mission/trace")

    assert response.status_code == 404
    assert "sim.slow_agent" in response.json()["detail"]


# ---------------------------------------------------------------------------
# The provider answered, and refused
# ---------------------------------------------------------------------------

class RefusingTransport:
    """A provider that rejects the request with an HTTP error status.

    The case a real run hits first and hardest: a stale key. Found by driving
    the finished panel in a browser, where the button produced
    `500 Internal Server Error` and nothing else - `httpx.HTTPStatusError` was
    escaping `complete_json` untranslated, so the one screen built to explain
    why the AI is not answering could not explain the commonest reason.
    """

    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text
        self.calls: List[Dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> Any:
        import httpx

        self.calls.append({"url": url, **kwargs})
        request = httpx.Request("POST", url)
        return httpx.Response(self.status_code, text=self.text, request=request)


@pytest.fixture(name="refusing")
def refusing_fixture() -> Iterator[Any]:
    def install(status_code: int, text: str = "") -> RefusingTransport:
        transport = RefusingTransport(status_code, text)
        app.dependency_overrides[ai.get_llm_client] = lambda: transport
        return transport

    yield install
    app.dependency_overrides.pop(ai.get_llm_client, None)


def test_post_policy_with_a_rejected_key_is_502_not_500(
    client: TestClient, refusing: Any
) -> None:
    refusing(401)

    response = client.post("/ai/personas/mission/policy")

    assert response.status_code == 502


def test_post_policy_with_a_rejected_key_names_the_setting_to_fix(
    client: TestClient, refusing: Any
) -> None:
    refusing(401)

    detail = client.post("/ai/personas/mission/policy").json()["detail"]

    assert "401" in detail
    assert "LLM_API_KEY" in detail


def test_post_policy_passes_through_what_the_provider_said(
    client: TestClient, refusing: Any
) -> None:
    refusing(402, '{"error":"insufficient credits"}')

    detail = client.post("/ai/personas/mission/policy").json()["detail"]

    assert "insufficient credits" in detail


def test_post_policy_leaves_the_committed_policy_alone_when_refused(
    client: TestClient, refusing: Any
) -> None:
    committed_path = ai.POLICIES_DIR / f"mission_{PLANOGRAM_ID}.json"
    before = committed_path.read_text(encoding="utf-8")
    refusing(401)

    client.post("/ai/personas/mission/policy")

    assert committed_path.read_text(encoding="utf-8") == before
