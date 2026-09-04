"""sim/persona_survey.py: the Brand Lift-style post-shop survey (S22, PLAN §5 Track E).

Every test here runs against an injected fake transport. There is no API key in this environment
and there must never be a real network call: `complete_json` only builds a request when a client
is injected or `LLM_API_KEY` is set, and every test below either injects its own transport or
deletes the key first.

Survey answers produced against a test double are not survey data. Nothing here writes to
`data/cache/surveys/`; every write goes to `tmp_path`, and
`test_the_real_survey_cache_is_untouched...` asserts the real directory is still empty.
"""
from __future__ import annotations

import json as json_module
import re
from pathlib import Path

import pytest

from sim.llm_client import LLMUnavailableError
from sim.persona_survey import (
    CONSTRUCTS,
    DEFAULT_SURVEY_DIR,
    MAX_REASON_WORDS,
    NO_BRAND,
    QUESTIONS,
    QUESTION_IDS,
    RESPONSE_FORMATS,
    SURVEY_VERSION,
    PersonaSurveyError,
    aggregate,
    default_focal_brand,
    main,
    population_aggregate,
    survey_panel,
    survey_persona,
    write_survey,
)
from sim.slow_agent import run_persona

ROOT = Path(__file__).resolve().parents[2]
INTEGRATION_DOC = ROOT / "docs" / "integration.md"

# Ids from data/planograms/demo_aisle.json.
B1_EYE_SLOT = "B1S3P1"      # holds SKU_005, a Crunch biscuit at eye level
B1_EYE_SKU = "SKU_005"
B3_AD_SLOT = "B3_ENDCAP"    # the only ad slot carrying a creative in variant A (AD_1, Crunch)
FOCAL_BRAND = "Crunch"      # AD_1's brand

THIRTY_WORDS = (
    "I picked that pack up because it was sitting right at eye level and the price "
    "looked about the same as the one I usually buy on a normal week"
)


# ---------------------------------------------------------------------------
# Fakes: one to manufacture a trip, one to answer the questionnaire.
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, body: dict):
        self._body = body

    def json(self) -> dict:
        return self._body

    def raise_for_status(self) -> None:
        pass


class TripLLM:
    """Replays a fixed script of shopping actions so `run_persona` can build a real trip."""

    def __init__(self, script: list[dict]):
        self._script = list(script)
        self.n_calls = 0

    def post(self, url, **kwargs):
        action = self._script[min(self.n_calls, len(self._script) - 1)]
        self.n_calls += 1
        return _FakeResponse({"content": [{"type": "text", "text": json_module.dumps(action)}]})


class SurveyLLM:
    """Answers whichever questionnaire item the prompt names, and records every prompt.

    `answers` maps a question id to one answer or to a list of answers replayed in order (the
    last repeats), which is how the re-ask tests hand back a bad answer and then a good one.
    """

    def __init__(self, answers: dict[str, dict | list[dict]]):
        self._answers = {qid: (a if isinstance(a, list) else [a]) for qid, a in answers.items()}
        self.prompts: list[str] = []
        self.asked: list[str] = []

    def post(self, url, **kwargs):
        prompt = kwargs["json"]["messages"][0]["content"]
        self.prompts.append(prompt)
        question_id = question_asked(prompt)
        self.asked.append(question_id)
        script = self._answers[question_id]
        index = min(self.asked.count(question_id) - 1, len(script) - 1)
        return _FakeResponse({"content": [{"type": "text", "text": json_module.dumps(script[index])}]})


def question_asked(prompt: str) -> str:
    """The question id the prompt is asking about."""
    match = re.search(r"^Survey question (\w+):", prompt, re.MULTILINE)
    assert match is not None, f"prompt named no question id:\n{prompt}"
    return match.group(1)


def answer(value, evidence=(), reason="It was the pack I actually reached for.") -> dict:
    return {"answer": value, "evidence": list(evidence), "reason": reason}


def good_answers(**overrides) -> dict[str, dict | list[dict]]:
    """A well-formed set of answers whose evidence matches `buying_trip`."""
    base: dict[str, dict | list[dict]] = {
        "unaided_awareness": answer(FOCAL_BRAND, [B1_EYE_SLOT], "Their pack sat right at eye level."),
        "aided_awareness": answer("yes", [B1_EYE_SLOT], "I saw their biscuits on the middle shelf."),
        "ad_recall": answer("no", [], "I do not remember any advertising panel today."),
        "brand_consideration": answer(4, [B1_EYE_SKU], "It is already the one I put in my basket."),
        "purchase_intent": answer(3, [B1_EYE_SKU], "I would buy it again if the price holds."),
    }
    base.update(overrides)
    return base


def ungrounded_answers(**overrides) -> dict[str, dict | list[dict]]:
    """The same answers citing nothing, which is what a trip that touched nothing supports."""
    base = {qid: answer(a["answer"], [], a["reason"])
            for qid, a in good_answers().items()}
    base.update(overrides)
    return base


def buying_trip(persona: dict, planogram: dict) -> dict:
    """One completed trip that looked at, then bought, the Crunch biscuits at eye level."""
    llm = TripLLM([
        {"action": "look", "target": B1_EYE_SLOT, "reason": "Eye level biscuits catch my eye."},
        {"action": "add_to_cart", "target": B1_EYE_SLOT, "reason": "Taking this one."},
        {"action": "checkout", "target": None, "reason": "Done, heading out."},
    ])
    trace = run_persona(persona, planogram, n_shoppers=1, seed=7, client=llm)
    return trace["shoppers"][0]


def empty_handed_trip(persona: dict, planogram: dict) -> dict:
    """One completed trip that bought nothing at all."""
    llm = TripLLM([{"action": "checkout", "target": None, "reason": "Nothing I need today."}])
    trace = run_persona(persona, planogram, n_shoppers=1, seed=7, client=llm)
    trip = trace["shoppers"][0]
    assert trip["cart"] == []
    return trip


def answers_by_id(survey: dict) -> dict:
    return {a["question_id"]: a for a in survey["answers"]}


# ---------------------------------------------------------------------------
# The instrument itself: stable, complete, and cited by the document.
# ---------------------------------------------------------------------------

def test_the_question_set_covers_every_brand_lift_construct_and_is_stable():
    """The five constructs a Brand Lift questionnaire measures, in questionnaire order."""
    assert QUESTION_IDS == (
        "unaided_awareness",
        "aided_awareness",
        "ad_recall",
        "brand_consideration",
        "purchase_intent",
    )
    assert CONSTRUCTS == (
        "unaided brand awareness",
        "aided brand awareness",
        "advertising recall",
        "brand consideration",
        "purchase intent",
    )
    assert len(QUESTIONS) == len(QUESTION_IDS)
    assert len({q.question_id for q in QUESTIONS}) == len(QUESTIONS)
    for question in QUESTIONS:
        assert question.text.strip(), f"{question.question_id} has no question text"
        assert question.response_format in RESPONSE_FORMATS, question.response_format
        assert question.construct in CONSTRUCTS


def test_docs_integration_md_cites_every_question_in_the_constant():
    """The document may not describe an instrument the code does not implement."""
    assert INTEGRATION_DOC.exists(), "docs/integration.md is the S22 deliverable"
    text = INTEGRATION_DOC.read_text(encoding="utf-8")
    for question in QUESTIONS:
        assert question.question_id in text, f"{question.question_id} is not in docs/integration.md"
        assert question.construct in text, f"{question.construct} is not in docs/integration.md"


def test_the_focal_brand_defaults_to_the_advertised_brand(planogram):
    assert default_focal_brand(planogram) == FOCAL_BRAND


# ---------------------------------------------------------------------------
# A well-formed model produces a complete survey for every persona.
# ---------------------------------------------------------------------------

def test_a_well_formed_model_answers_every_question_for_all_four_personas(
        planogram, personas, policies):
    for persona_id, persona in personas.items():
        llm = SurveyLLM(good_answers())
        trip = buying_trip(persona, planogram)

        survey = survey_persona(persona, policies[persona_id], trip, planogram, client=llm)

        assert survey["persona_id"] == persona_id
        assert survey["planogram_id"] == "demo_aisle"
        assert survey["brand"] == FOCAL_BRAND
        assert [a["question_id"] for a in survey["answers"]] == list(QUESTION_IDS)
        assert survey["unanswered"] == []
        assert survey["rejections"] == []
        by_id = answers_by_id(survey)
        assert by_id["unaided_awareness"]["answer"] == FOCAL_BRAND
        assert by_id["brand_consideration"]["answer"] == 4
        assert by_id["purchase_intent"]["answer"] == 3
        assert all(a["reason"].strip() for a in survey["answers"])
        json_module.dumps(survey)  # self-describing and JSON-serialisable


def test_every_question_is_asked_once_and_the_prompt_carries_the_trip(
        planogram, personas, policies):
    llm = SurveyLLM(good_answers())
    trip = buying_trip(personas["mission"], planogram)

    survey_persona(personas["mission"], policies["mission"], trip, planogram, client=llm)

    assert llm.asked == list(QUESTION_IDS)
    first = llm.prompts[0]
    assert personas["mission"]["description"] in first
    assert B1_EYE_SLOT in first          # the slot the shopper actually looked at
    assert B1_EYE_SKU in first           # what ended up in the cart


def test_the_unaided_item_is_asked_first_and_never_names_the_studied_brand(
        planogram, personas, policies):
    """Naming the focal brand in the unaided item would turn it into an aided one."""
    llm = SurveyLLM(good_answers())
    trip = buying_trip(personas["mission"], planogram)

    survey_persona(personas["mission"], policies["mission"], trip, planogram, client=llm)

    assert llm.asked[0] == "unaided_awareness"
    assert "questionnaire is about" not in llm.prompts[0]
    assert f"questionnaire is about: {FOCAL_BRAND}" in llm.prompts[1]


# ---------------------------------------------------------------------------
# Semantic guards: what a static JSON Schema cannot express (the S12 precedent).
# ---------------------------------------------------------------------------

def test_an_answer_naming_a_brand_outside_the_planogram_is_rejected_and_reasked(
        planogram, personas, policies):
    llm = SurveyLLM(good_answers(unaided_awareness=[
        answer("Fizzo", [], "That is the brand that comes to mind."),
        answer(FOCAL_BRAND, [], "Their pack sat right at eye level."),
    ]))
    trip = buying_trip(personas["switcher"], planogram)

    survey = survey_persona(personas["switcher"], policies["switcher"], trip, planogram,
                            client=llm)

    assert [r["question_id"] for r in survey["rejections"]] == ["unaided_awareness"]
    assert "Fizzo" in survey["rejections"][0]["rejection"]
    assert "not sold in this store" in survey["rejections"][0]["rejection"]
    # The rejection was fed back to the model, and the corrected answer is the one kept.
    assert "Fizzo" in llm.prompts[1]
    assert "not sold in this store" in llm.prompts[1]
    assert answers_by_id(survey)["unaided_awareness"]["answer"] == FOCAL_BRAND


def test_an_out_of_range_scale_answer_is_rejected_and_reasked(planogram, personas, policies):
    llm = SurveyLLM(good_answers(purchase_intent=[
        answer(9, [], "I would definitely buy it again."),
        answer(5, [], "I would definitely buy it again."),
    ]))
    trip = buying_trip(personas["loyalist"], planogram)

    survey = survey_persona(personas["loyalist"], policies["loyalist"], trip, planogram,
                            client=llm)

    assert [r["question_id"] for r in survey["rejections"]] == ["purchase_intent"]
    assert "1 to 5" in survey["rejections"][0]["rejection"]
    assert "1 to 5" in llm.prompts[-1]
    assert answers_by_id(survey)["purchase_intent"]["answer"] == 5


def test_a_yes_no_question_answered_with_prose_is_rejected_and_reasked(
        planogram, personas, policies):
    llm = SurveyLLM(good_answers(ad_recall=[
        answer("I think I might have", [], "There was a sign somewhere."),
        answer("no", [], "I do not remember any advertising panel today."),
    ]))
    trip = buying_trip(personas["browser"], planogram)

    survey = survey_persona(personas["browser"], policies["browser"], trip, planogram, client=llm)

    assert survey["rejections"][0]["question_id"] == "ad_recall"
    assert "'yes' or 'no'" in survey["rejections"][0]["rejection"]
    assert answers_by_id(survey)["ad_recall"]["answer"] == "no"


def test_a_reason_longer_than_the_cap_is_rejected_and_reasked(planogram, personas, policies):
    assert len(THIRTY_WORDS.split()) > MAX_REASON_WORDS
    llm = SurveyLLM(good_answers(brand_consideration=[
        answer(4, [], THIRTY_WORDS),
        answer(4, [], "It is already the one I put in my basket."),
    ]))
    trip = buying_trip(personas["mission"], planogram)

    survey = survey_persona(personas["mission"], policies["mission"], trip, planogram, client=llm)

    assert survey["rejections"][0]["question_id"] == "brand_consideration"
    assert f"{MAX_REASON_WORDS} words" in survey["rejections"][0]["rejection"]
    assert answers_by_id(survey)["brand_consideration"]["reason"].endswith("basket.")


# ---------------------------------------------------------------------------
# Grounding: an answer may only cite what the shopper actually did.
# ---------------------------------------------------------------------------

def test_a_shopper_who_bought_nothing_cannot_cite_a_purchase_it_never_made(
        planogram, personas, policies):
    """The concrete inconsistency: purchase intent citing a sku that is not in an empty cart."""
    llm = SurveyLLM(ungrounded_answers(purchase_intent=[
        answer(5, [B1_EYE_SKU], "I already bought a pack of theirs today."),
        answer(2, [], "Nothing here was worth buying today."),
    ]))
    trip = empty_handed_trip(personas["mission"], planogram)

    survey = survey_persona(personas["mission"], policies["mission"], trip, planogram, client=llm)

    rejection = survey["rejections"][0]
    assert rejection["question_id"] == "purchase_intent"
    assert B1_EYE_SKU in rejection["rejection"]
    assert "did not buy it on this trip" in rejection["rejection"]
    assert "did not buy it on this trip" in llm.prompts[-1]
    kept = answers_by_id(survey)["purchase_intent"]
    assert kept["answer"] == 2
    assert kept["evidence"] == []


def test_evidence_naming_a_slot_the_shopper_never_looked_at_is_rejected(
        planogram, personas, policies):
    llm = SurveyLLM(ungrounded_answers(ad_recall=[
        answer("yes", [B3_AD_SLOT], "I remember the header above the endcap."),
        answer("no", [], "I never got as far as the endcap today."),
    ]))
    trip = empty_handed_trip(personas["browser"], planogram)

    survey = survey_persona(personas["browser"], policies["browser"], trip, planogram, client=llm)

    rejection = survey["rejections"][0]
    assert rejection["question_id"] == "ad_recall"
    assert B3_AD_SLOT in rejection["rejection"]
    assert "never looked at it on this trip" in rejection["rejection"]
    assert answers_by_id(survey)["ad_recall"]["answer"] == "no"


def test_evidence_naming_something_that_is_not_in_the_store_at_all_is_rejected(
        planogram, personas, policies):
    llm = SurveyLLM(good_answers(aided_awareness=[
        answer("yes", ["NOT_A_SLOT"], "I saw it somewhere over there."),
        answer("yes", [], "I remember their packs on the shelf."),
    ]))
    trip = buying_trip(personas["loyalist"], planogram)

    survey = survey_persona(personas["loyalist"], policies["loyalist"], trip, planogram,
                            client=llm)

    assert "NOT_A_SLOT" in survey["rejections"][0]["rejection"]
    assert "is not anything in this store" in survey["rejections"][0]["rejection"]
    assert answers_by_id(survey)["aided_awareness"]["evidence"] == []


def test_a_stubborn_model_leaves_the_item_unanswered_rather_than_inventing_one(
        planogram, personas, policies):
    """Item non-response is a real survey outcome. A fabricated answer is not."""
    llm = SurveyLLM(good_answers(unaided_awareness=answer("Fizzo", [], "That is what I recall.")))
    trip = buying_trip(personas["mission"], planogram)

    survey = survey_persona(personas["mission"], policies["mission"], trip, planogram,
                            client=llm, max_reasks=2)

    assert survey["unanswered"] == ["unaided_awareness"]
    assert [a["question_id"] for a in survey["answers"]] == list(QUESTION_IDS[1:])
    assert len([r for r in survey["rejections"] if r["question_id"] == "unaided_awareness"]) == 3
    assert llm.asked.count("unaided_awareness") == 3  # one ask plus max_reasks re-asks


# ---------------------------------------------------------------------------
# The panel: one survey per shopper, aggregated the way a Brand Lift is read out.
# ---------------------------------------------------------------------------

def test_survey_panel_surveys_every_shopper_and_aggregates_the_answers(
        planogram, personas, policies):
    llm = SurveyLLM(ungrounded_answers())
    # One shared fake serves all three shoppers, so the script must be one repeatable action.
    trip_llm = TripLLM([{"action": "checkout", "target": None, "reason": "Done, heading out."}])
    trace = run_persona(personas["mission"], planogram, n_shoppers=3, seed=5, client=trip_llm)

    panel = survey_panel(personas["mission"], policies["mission"], trace, planogram, client=llm)

    assert panel["survey_version"] == SURVEY_VERSION
    assert panel["persona_id"] == "mission"
    assert panel["n_shoppers"] == 3
    assert len(panel["shoppers"]) == 3
    assert [q["question_id"] for q in panel["questions"]] == list(QUESTION_IDS)
    agg = panel["aggregate"]
    assert agg["purchase_intent"]["n_answered"] == 3
    assert agg["purchase_intent"]["mean"] == pytest.approx(3.0)
    assert agg["purchase_intent"]["top2box"] == pytest.approx(0.0)   # a 3 is not top-two-box
    assert agg["brand_consideration"]["top2box"] == pytest.approx(1.0)  # three 4s are
    assert agg["ad_recall"]["yes_rate"] == pytest.approx(0.0)
    assert agg["unaided_awareness"]["distribution"] == {FOCAL_BRAND: pytest.approx(1.0)}
    assert agg["unaided_awareness"]["focal_brand_share"] == pytest.approx(1.0)


def test_an_unanswered_item_is_reported_as_such_and_never_as_a_zero():
    """RESULTS.md says 'not yet collected' rather than 0.00 for exactly this reason."""
    surveys = [{
        "answers": [],
        "unanswered": list(QUESTION_IDS),
        "rejections": [],
    }]

    agg = aggregate(surveys, brand=FOCAL_BRAND)

    for question_id in QUESTION_IDS:
        assert agg[question_id]["n_answered"] == 0
        assert agg[question_id]["n_unanswered"] == 1
    assert agg["purchase_intent"]["mean"] is None
    assert agg["purchase_intent"]["top2box"] is None
    assert agg["ad_recall"]["yes_rate"] is None
    assert agg["unaided_awareness"]["focal_brand_share"] is None
    assert agg["unaided_awareness"]["distribution"] == {}


def test_population_aggregate_weights_the_personas_by_share_of_population(personas):
    """This is exactly where a CPS-seeded share would enter the read-out."""
    def panel_with(mean: float, yes_rate: float, distribution: dict) -> dict:
        agg = aggregate([], brand=FOCAL_BRAND)
        agg["purchase_intent"].update({"n_answered": 10, "mean": mean, "top2box": 0.0})
        agg["ad_recall"].update({"n_answered": 10, "yes_rate": yes_rate})
        agg["unaided_awareness"].update({
            "n_answered": 10 if distribution else 0,
            "distribution": distribution,
            # `aggregate` reports 0.0 for a segment that answered but named other brands, and
            # None only for a segment that answered nothing at all.
            "focal_brand_share": distribution.get(FOCAL_BRAND, 0.0) if distribution else None,
        })
        return {"aggregate": agg}

    panels = {
        "mission": panel_with(1.0, 0.0, {FOCAL_BRAND: 1.0}),          # share 0.35
        "browser": panel_with(2.0, 1.0, {"Zapp": 1.0}),               # share 0.25
        "loyalist": panel_with(3.0, 0.0, {FOCAL_BRAND: 0.5, "Nimbus": 0.5}),  # share 0.25
        "switcher": panel_with(5.0, 1.0, {}),                         # share 0.15, answered none
    }

    rolled = population_aggregate(panels, personas)

    # 0.35*1 + 0.25*2 + 0.25*3 + 0.15*5 = 2.35, where the unweighted mean would be 2.75
    assert rolled["aggregate"]["purchase_intent"]["mean"] == pytest.approx(2.35)
    # 0.25 + 0.15 = 0.40, where the unweighted rate would be 0.50
    assert rolled["aggregate"]["ad_recall"]["yes_rate"] == pytest.approx(0.40)
    assert rolled["aggregate"]["purchase_intent"]["n_answered"] == 40
    assert rolled["shares"] == {"mission": 0.35, "browser": 0.25, "loyalist": 0.25,
                                "switcher": 0.15}
    assert rolled["n_personas"] == 4

    # switcher answered nothing, so the distribution renormalises over the 0.85 that did.
    unaided = rolled["aggregate"]["unaided_awareness"]
    assert unaided["distribution"] == {
        FOCAL_BRAND: pytest.approx((0.35 + 0.25 * 0.5) / 0.85),
        "Nimbus": pytest.approx((0.25 * 0.5) / 0.85),
        "Zapp": pytest.approx(0.25 / 0.85),
    }
    assert sum(unaided["distribution"].values()) == pytest.approx(1.0)
    assert unaided["focal_brand_share"] == pytest.approx((0.35 + 0.25 * 0.5) / 0.85)
    assert unaided["n_answered"] == 30


def test_population_aggregate_refuses_a_persona_it_has_no_share_for(personas):
    panels = {"mission": {"aggregate": aggregate([], brand=FOCAL_BRAND)}}
    with pytest.raises(PersonaSurveyError):
        population_aggregate(panels, {"browser": personas["browser"]})


# ---------------------------------------------------------------------------
# Writing: explicit destination only. The real cache stays empty.
# ---------------------------------------------------------------------------

def test_write_survey_returns_the_path_it_wrote(planogram, personas, policies, tmp_path):
    llm = SurveyLLM(ungrounded_answers())
    trace = run_persona(personas["mission"], planogram, n_shoppers=1, seed=7,
                        client=TripLLM([{"action": "checkout", "target": None,
                                         "reason": "Nothing I need today."}]))
    panel = survey_panel(personas["mission"], policies["mission"], trace, planogram, client=llm)

    path = write_survey(panel, cache_dir=tmp_path / "nested")

    assert path == tmp_path / "nested" / "mission_demo_aisle_survey.json"
    assert json_module.loads(path.read_text(encoding="utf-8")) == panel


def test_the_real_survey_cache_is_untouched_and_write_survey_demands_an_explicit_cache_dir(
        planogram, personas, policies):
    existing = [] if not DEFAULT_SURVEY_DIR.exists() else sorted(
        p.name for p in DEFAULT_SURVEY_DIR.iterdir())
    assert existing in ([], [".gitkeep"]), (
        "data/cache/surveys/ must stay empty until a real LLM run fills it"
    )
    with pytest.raises(TypeError):
        write_survey({"persona_id": "mission", "planogram_id": "demo_aisle"})


# ---------------------------------------------------------------------------
# Offline honesty: no key means no survey, and nothing on disk.
# ---------------------------------------------------------------------------

def test_the_cli_entry_point_fails_loudly_without_an_api_key_and_writes_nothing(
        monkeypatch, capsys, tmp_path, planogram, personas):
    """`python -m sim.persona_survey` must never fabricate survey data. Deleting the key here
    also means this test can never reach the network, even on a machine that has one."""
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    traces = tmp_path / "traces"
    traces.mkdir()
    trace = run_persona(personas["mission"], planogram, n_shoppers=1, seed=7,
                        client=TripLLM([{"action": "checkout", "target": None,
                                         "reason": "Nothing I need today."}]))
    (traces / "mission_demo_aisle.json").write_text(json_module.dumps(trace), encoding="utf-8")
    out = tmp_path / "surveys"

    exit_code = main(["--persona", "mission", "--traces-dir", str(traces), "--out", str(out)])

    assert exit_code == 2
    stderr = capsys.readouterr().err
    assert "LLM_API_KEY" in stderr
    assert "no offline fallback" in stderr
    assert not out.exists() or list(out.iterdir()) == []


def test_the_cli_says_which_trace_is_missing_rather_than_surveying_nothing(
        monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    out = tmp_path / "surveys"

    exit_code = main(["--persona", "mission", "--traces-dir", str(tmp_path / "empty"),
                      "--out", str(out)])

    assert exit_code == 2
    stderr = capsys.readouterr().err
    assert "mission_demo_aisle.json" in stderr
    assert "sim.slow_agent" in stderr
    assert not out.exists()


def test_llm_unavailable_still_propagates_out_of_the_library_functions(
        monkeypatch, planogram, personas, policies):
    """Only the CLI turns LLMUnavailableError into a message; callers still get the exception."""
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    trip = empty_handed_trip(personas["mission"], planogram)
    with pytest.raises(LLMUnavailableError):
        survey_persona(personas["mission"], policies["mission"], trip, planogram)


# ---------------------------------------------------------------------------
# Refusals that are not the model's fault.
# ---------------------------------------------------------------------------

def test_a_brand_that_is_not_in_the_planogram_is_refused_before_any_call(
        planogram, personas, policies):
    trip = empty_handed_trip(personas["mission"], planogram)
    with pytest.raises(PersonaSurveyError):
        survey_persona(personas["mission"], policies["mission"], trip, planogram,
                       brand="Fizzo", client=SurveyLLM(good_answers()))


def test_a_planogram_with_no_creative_has_no_default_focal_brand():
    with pytest.raises(PersonaSurveyError):
        default_focal_brand({"planogram_id": "empty", "skus": [], "creatives": [], "bays": []})


def test_no_brand_is_a_legal_unaided_awareness_answer(planogram, personas, policies):
    llm = SurveyLLM(ungrounded_answers(unaided_awareness=answer(
        NO_BRAND, [], "No brand really stuck with me today.")))
    trip = empty_handed_trip(personas["switcher"], planogram)

    survey = survey_persona(personas["switcher"], policies["switcher"], trip, planogram,
                            client=llm)

    assert survey["rejections"] == []
    assert answers_by_id(survey)["unaided_awareness"]["answer"] == NO_BRAND
