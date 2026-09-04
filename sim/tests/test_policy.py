"""sim/policy.py: get_policy caches LLM-generated persona policies (SPEC M4, S12).

The real `data/cache/policies/` directory already holds four hand-written, committed,
schema-valid policies (from S2) that sim/simulator.py's own tests load. Those files must never
be touched by this test module: every test that exercises a cache *write* points cache_dir at
`tmp_path`. Only the read-only "cache hit" test touches the real directory, and it must never
invoke the client while doing so.
"""
from __future__ import annotations

import json as json_module

import pytest

from sim.policy import PolicyValidationError, get_policy

PLANOGRAM_ID = "demo_aisle"


class _FakeResponse:
    def __init__(self, body: dict):
        self._body = body

    def json(self) -> dict:
        return self._body

    def raise_for_status(self) -> None:
        pass


class StubTransport:
    """Always returns `policy` as the model's completion text; records every request."""

    def __init__(self, policy: dict):
        self._policy = policy
        self.requests: list[dict] = []

    def post(self, url, **kwargs):
        self.requests.append({"url": url, **kwargs})
        text = json_module.dumps(self._policy)
        return _FakeResponse({"content": [{"type": "text", "text": text}]})

    @property
    def calls(self) -> int:
        return len(self.requests)


class _ExplodingTransport:
    """A transport that fails the test if it is ever contacted."""

    def post(self, *args, **kwargs):
        raise AssertionError("get_policy must not call the LLM client on a cache hit")


def _valid_policy_body(persona_id: str, **overrides) -> dict:
    """A policy that is valid against schemas/policy.schema.json, for persona_id `persona_id`,
    referencing only brands/categories that actually exist in data/planograms/demo_aisle.json."""
    body = {
        "persona_id": persona_id,
        "goal_categories": ["chips", "cola"],
        "time_budget_s": {"mean": 60.0, "sd": 10.0},
        "exploration": 0.5,
        "brand_affinity": {
            "_default": 0.5,
            "Crunch": 0.6,
            "Nimbus": 0.5,
            "Orchid": 0.5,
            "Zapp": 0.5,
        },
        "price_sensitivity": 0.5,
        "promo_sensitivity": 0.5,
        "ad_receptivity": 0.5,
        "purchase_threshold": 0.3,
        "dwell_ms": {"mu": 6.2, "sigma": 0.5},
        "fixations_per_station": {"lam": 4.0},
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# Cache hit: the committed, real cache -- read-only, no LLM call, ever.
# ---------------------------------------------------------------------------

def test_cache_hit_serves_the_real_committed_policy_without_any_llm_call(planogram, personas, policies):
    result = get_policy(personas["mission"], planogram, client=_ExplodingTransport())
    assert result == policies["mission"]


# ---------------------------------------------------------------------------
# Cache miss / force: all against tmp_path, never the real cache dir.
# ---------------------------------------------------------------------------

def test_cache_miss_writes_to_tmp_path_and_returns_the_generated_policy(planogram, personas, tmp_path):
    generated = _valid_policy_body("mission")
    transport = StubTransport(generated)

    result = get_policy(personas["mission"], planogram, cache_dir=tmp_path, client=transport)

    assert result == generated
    assert transport.calls == 1
    cache_file = tmp_path / f"mission_{PLANOGRAM_ID}.json"
    assert cache_file.exists()
    assert json_module.loads(cache_file.read_text(encoding="utf-8")) == generated


def test_force_true_on_an_existing_cache_entry_calls_the_client(planogram, personas, tmp_path):
    cache_file = tmp_path / f"mission_{PLANOGRAM_ID}.json"
    cache_file.write_text(json_module.dumps(_valid_policy_body("mission", exploration=0.05)),
                          encoding="utf-8")

    fresh = _valid_policy_body("mission", exploration=0.9)
    transport = StubTransport(fresh)
    result = get_policy(personas["mission"], planogram, cache_dir=tmp_path, client=transport,
                        force=True)

    assert transport.calls == 1
    assert result == fresh
    assert json_module.loads(cache_file.read_text(encoding="utf-8")) == fresh


def test_without_force_an_existing_tmp_path_cache_entry_is_returned_untouched(planogram, personas,
                                                                              tmp_path):
    cache_file = tmp_path / f"mission_{PLANOGRAM_ID}.json"
    seeded_text = json_module.dumps(_valid_policy_body("mission", exploration=0.05))
    cache_file.write_text(seeded_text, encoding="utf-8")

    transport = StubTransport(_valid_policy_body("mission", exploration=0.9))
    result = get_policy(personas["mission"], planogram, cache_dir=tmp_path, client=transport)

    assert transport.calls == 0
    assert result == json_module.loads(seeded_text)
    assert cache_file.read_text(encoding="utf-8") == seeded_text


# ---------------------------------------------------------------------------
# Semantic validation beyond the JSON schema -- the session's headline acceptance test.
# ---------------------------------------------------------------------------

def test_policy_naming_an_unknown_brand_fails_validation(planogram, personas, tmp_path):
    bad = _valid_policy_body("mission", brand_affinity={"_default": 0.5, "Pepsi": 0.9})
    transport = StubTransport(bad)

    with pytest.raises(PolicyValidationError):
        get_policy(personas["mission"], planogram, cache_dir=tmp_path, client=transport)

    assert not (tmp_path / f"mission_{PLANOGRAM_ID}.json").exists()


def test_policy_naming_an_unknown_category_fails_validation(planogram, personas, tmp_path):
    bad = _valid_policy_body("mission", goal_categories=["chips", "soap"])
    transport = StubTransport(bad)

    with pytest.raises(PolicyValidationError):
        get_policy(personas["mission"], planogram, cache_dir=tmp_path, client=transport)

    assert not (tmp_path / f"mission_{PLANOGRAM_ID}.json").exists()


def test_brand_affinity_containing_only_default_is_accepted(planogram, personas, tmp_path):
    ok = _valid_policy_body("mission", brand_affinity={"_default": 0.5})
    transport = StubTransport(ok)

    result = get_policy(personas["mission"], planogram, cache_dir=tmp_path, client=transport)

    assert result == ok
    assert (tmp_path / f"mission_{PLANOGRAM_ID}.json").exists()


def test_policy_whose_persona_id_does_not_match_the_requested_persona_is_rejected(planogram,
                                                                                  personas,
                                                                                  tmp_path):
    wrong = _valid_policy_body("browser")  # requested persona below is "mission"
    transport = StubTransport(wrong)

    with pytest.raises(PolicyValidationError):
        get_policy(personas["mission"], planogram, cache_dir=tmp_path, client=transport)

    assert not (tmp_path / f"mission_{PLANOGRAM_ID}.json").exists()


# ---------------------------------------------------------------------------
# Prompt rendering.
# ---------------------------------------------------------------------------

def test_rendered_prompt_contains_description_and_planogram_brands_and_categories(planogram,
                                                                                   personas,
                                                                                   tmp_path):
    transport = StubTransport(_valid_policy_body("mission"))

    get_policy(personas["mission"], planogram, cache_dir=tmp_path, client=transport)

    assert transport.calls == 1
    sent_prompt = transport.requests[0]["json"]["messages"][0]["content"]
    assert personas["mission"]["description"] in sent_prompt
    for sku in planogram["skus"]:
        assert sku["brand"] in sent_prompt
        assert sku["category"] in sent_prompt
