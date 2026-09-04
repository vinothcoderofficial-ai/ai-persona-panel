"""A Brand Lift-style questionnaire, asked of a persona after its shopping trip (S22).

`sim/slow_agent.py` walks a persona through the store and leaves a trip: what it looked at, what
it picked up, what it left with. That is the *revealed* record. This module asks the same shopper
the questions a Brand Lift survey asks after an ad campaign -- unaided and aided brand awareness,
advertising recall, brand consideration, purchase intent -- and returns the *stated* answers, in
the response formats a real questionnaire uses, so the synthetic panel and a real panel can one
day be compared on the same instrument.

Read `docs/integration.md` before changing the question set. It cites `QUESTIONS` item by item,
`sim/tests/test_persona_survey.py` asserts the document and this constant still agree, and the
document is the S22 deliverable that explains what the instrument is for.

**Stated is not revealed, and that is the point.** `analytics/lift.py` already measures what the
ad was worth in purchases. This module measures what the shopper *says*. Nothing here forces the
two to agree: a persona may rate purchase intent 5 for a brand it did not buy today, exactly as a
real respondent may. What is *not* allowed is a fabricated fact about the trip -- so the
`evidence` field, and only the `evidence` field, is audited against the trip record. An answer
citing a sku that is not in the shopper's cart is rejected and re-asked, because that is the
shopper claiming a purchase it never made. Attitudes are free; claims about the record are not.

**Generating real survey data.** There is no API key in this repository, and answers produced
against a test double are not survey data. `data/cache/surveys/` therefore does not exist until a
real run creates it. Once `LLM_API_KEY` (and optionally `LLM_BASE_URL` / `LLM_MODEL`) are set in
`.env`, and `data/cache/traces/` has been filled by `python -m sim.slow_agent --all`, run:

    python -m sim.persona_survey --all                     # all four personas
    python -m sim.persona_survey --persona mission         # one persona
    python -m sim.persona_survey --all --max-shoppers 5    # cap the cost of a run

Each writes `data/cache/surveys/{persona_id}_{planogram_id}_survey.json`. No code change is
needed. One questionnaire item is one model call, so a run costs `5 x shoppers` calls per persona;
`--max-shoppers` is there because 20 shoppers x 4 personas is 400 of them.

**Design notes worth not re-deriving.**

- **The unaided item is asked first and never names the studied brand.** Naming it would turn
  unaided awareness into aided awareness, and the two items would measure the same thing.
  `Question.names_brand` carries that, and a test pins it.
- **The unaided item is only quasi-unaided.** A real Brand Lift asks it open-ended and back-codes
  the verbatim to a brand list afterwards; here the coding frame (the stocked brands, all four,
  symmetrically) is shown up front so the answer is directly comparable. That is a known
  difference between the two instruments and `docs/integration.md` states it as a caveat rather
  than hiding it.
- **Two validation layers, as in `sim/slow_agent.py`.** `sim/llm_client.py` enforces the JSON
  *shape* (`ANSWER_SCHEMA`) and retries on its own. This module enforces what one static schema
  cannot, because all three facts are per-call: the response format belongs to the *question*,
  the brand vocabulary belongs to the *planogram*, and the evidence vocabulary belongs to the
  *trip*. A failure here is re-asked with the reason fed back, capped by `max_reasks`.
- **A question that survives the re-ask budget is left unanswered**, not answered by this module.
  Item non-response is a real survey outcome; an invented answer is not. `aggregate` counts the
  unanswered and reports `None` rather than 0.0 when nothing was answered -- the same rule
  `RESULTS.md` follows when it says "not yet collected".
- **Temperature 0 by default.** `slow_agent` samples at 0.7 because 20 identical shopping trips
  would be a useless trace. Here the variety already exists -- each shopper answers about its own
  trip -- and the questionnaire is a measurement instrument, so it should not add sampling noise
  of its own.
- **`prompts/persona_survey.md` is rendered with `str.format`**, so any *literal* brace in that
  file must be doubled (`{{` / `}}`). The answer example in it already is.
- **The library functions do no I/O apart from the survey write**, which needs an explicit
  `cache_dir` -- deliberately no default, so a test cannot write to the real cache by accident.
  Only `main()` reads data files, and only from paths it was given.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from sim.llm_client import LLMUnavailableError, LLMValidationError, complete_json

ROOT = Path(__file__).resolve().parent.parent
PROMPT_TEMPLATE_PATH = Path(__file__).resolve().parent / "prompts" / "persona_survey.md"
DEFAULT_SURVEY_DIR = ROOT / "data" / "cache" / "surveys"
DEFAULT_TRACE_DIR = ROOT / "data" / "cache" / "traces"
DEFAULT_PLANOGRAM_PATH = ROOT / "data" / "planograms" / "demo_aisle.json"
DEFAULT_PERSONA_DIR = ROOT / "data" / "personas"
DEFAULT_POLICY_DIR = ROOT / "data" / "cache" / "policies"

PERSONA_IDS = ("mission", "browser", "loyalist", "switcher")

SURVEY_VERSION = 1
DEFAULT_TEMPERATURE = 0.0
DEFAULT_MAX_REASKS = 3
MAX_REASON_WORDS = 25

# A closed brand vocabulary, plus the one answer that names no brand at all.
NO_BRAND = "none"
YES, NO = "yes", "no"

# The 1-5 agreement scale a Brand Lift questionnaire uses, and its top-two-box cut.
SCALE_MIN, SCALE_MAX = 1, 5
TOP_BOX_MIN = 4

RESPONSE_FORMATS = ("brand", "yes_no", "scale_1_5")


@dataclass(frozen=True)
class Question:
    """One questionnaire item. `names_brand` is False only for the unaided item."""

    question_id: str
    construct: str
    response_format: str
    names_brand: bool
    text: str


# The instrument. Questionnaire order matters: unaided awareness must be asked before anything
# names the studied brand. `docs/integration.md` cites every id and construct below.
QUESTIONS: tuple[Question, ...] = (
    Question(
        question_id="unaided_awareness",
        construct="unaided brand awareness",
        response_format="brand",
        names_brand=False,
        text="Thinking back on the aisle you just shopped, which brand comes to mind first?",
    ),
    Question(
        question_id="aided_awareness",
        construct="aided brand awareness",
        response_format="yes_no",
        names_brand=True,
        text="Did you see {brand} on the shelves in that aisle today?",
    ),
    Question(
        question_id="ad_recall",
        construct="advertising recall",
        response_format="yes_no",
        names_brand=True,
        text="Do you remember seeing an advertising panel for {brand} anywhere in that aisle "
             "today?",
    ),
    Question(
        question_id="brand_consideration",
        construct="brand consideration",
        response_format="scale_1_5",
        names_brand=True,
        text="Next time you shop this aisle, how likely are you to consider {brand}?",
    ),
    Question(
        question_id="purchase_intent",
        construct="purchase intent",
        response_format="scale_1_5",
        names_brand=True,
        text="How likely are you to buy {brand} on your next shopping trip?",
    ),
)

QUESTION_IDS: tuple[str, ...] = tuple(q.question_id for q in QUESTIONS)
CONSTRUCTS: tuple[str, ...] = tuple(q.construct for q in QUESTIONS)
QUESTIONS_BY_ID: dict[str, Question] = {q.question_id: q for q in QUESTIONS}

# Shape only. Range, vocabulary and grounding are per-call facts and live in `reject_reason`.
ANSWER_SCHEMA: dict = {
    "type": "object",
    "required": ["answer", "evidence", "reason"],
    "additionalProperties": False,
    "properties": {
        "answer": {"type": ["string", "integer"]},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "reason": {"type": "string", "minLength": 1},
    },
}


class PersonaSurveyError(Exception):
    """Raised when a survey cannot be run at all (an unknown brand, a store with no advertising)."""


# ---------------------------------------------------------------------------
# The store, and the trip, as vocabularies
# ---------------------------------------------------------------------------

def planogram_brands(planogram: Mapping) -> tuple[str, ...]:
    """Every brand stocked in the store, sorted. The closed vocabulary for a brand answer."""
    return tuple(sorted({sku["brand"] for sku in planogram["skus"]}))


def default_focal_brand(planogram: Mapping) -> str:
    """The brand a Brand Lift study on this store would be about: its first advertised brand.

    A Brand Lift study is always run for one advertised brand, so a survey with no creative to
    hang it on is refused rather than defaulted to an arbitrary shelf brand.
    """
    creatives = planogram.get("creatives") or []
    if not creatives:
        raise PersonaSurveyError(
            f"planogram {planogram.get('planogram_id')!r} carries no creative, so there is no "
            "advertised brand to run a Brand Lift survey for; pass brand= explicitly."
        )
    return creatives[0]["brand"]


def _store_vocabulary(planogram: Mapping) -> tuple[frozenset[str], frozenset[str]]:
    """(every slot and ad-slot id in the store, every sku id in the store)."""
    slot_ids = {
        slot["slot_id"]
        for bay in planogram["bays"] for shelf in bay["shelves"] for slot in shelf["slots"]
    }
    slot_ids |= {ad["ad_slot_id"] for bay in planogram["bays"] for ad in bay["ad_slots"]}
    return frozenset(slot_ids), frozenset(sku["sku_id"] for sku in planogram["skus"])


def trip_vocabulary(trip: Mapping) -> tuple[frozenset[str], frozenset[str]]:
    """(what this shopper looked at or touched, what it actually left with).

    Looked-at ids are every non-null action target; bought ids are the cart's sku ids and the
    slots those items came from. Together these are the only things an answer may cite.
    """
    looked = {turn["target"] for turn in trip.get("turns", []) if turn.get("target")}
    bought = set(trip.get("cart", []))
    bought |= {item["slot_id"] for item in trip.get("cart_detail", []) if item.get("slot_id")}
    return frozenset(looked), frozenset(bought)


# ---------------------------------------------------------------------------
# Semantic validation -- what one static JSON Schema cannot express
# ---------------------------------------------------------------------------

def response_instruction(question: Question, brands: Sequence[str]) -> str:
    """The sentence that tells the respondent what shape the answer takes."""
    if question.response_format == "brand":
        return (f'Answer with exactly one of these brand names, or "{NO_BRAND}" if no brand comes '
                f'to mind: {", ".join(brands)}.')
    if question.response_format == "yes_no":
        return f'Answer exactly "{YES}" or "{NO}".'
    return (f"Answer with a whole number from {SCALE_MIN} to {SCALE_MAX}, where {SCALE_MIN} means "
            f"not at all likely and {SCALE_MAX} means extremely likely.")


def reject_reason(answer: Mapping, question: Question, *, brands: Sequence[str],
                  looked: frozenset[str], bought: frozenset[str],
                  store_slots: frozenset[str], store_skus: frozenset[str]) -> str | None:
    """Why this answer is not usable, or None if it is.

    The returned sentence is fed straight back to the model, so it names what was wrong and what
    to answer instead. Only the response format and the `evidence` claims are checked: the value
    of an attitude answer is the measurement and is never overruled here.
    """
    n_words = len(answer["reason"].split())
    if n_words == 0:
        return f"the reason was blank; say why in at most {MAX_REASON_WORDS} words."
    if n_words > MAX_REASON_WORDS:
        return f"the reason was {n_words} words; the limit is {MAX_REASON_WORDS} words."

    value = answer["answer"]
    if question.response_format == "brand":
        if not isinstance(value, str) or value not in set(brands) | {NO_BRAND}:
            return (f"{value!r} is not sold in this store; answer with one of "
                    f"{', '.join(brands)}, or {NO_BRAND!r} if no brand comes to mind.")
    elif question.response_format == "yes_no":
        if value not in (YES, NO):
            return (f"{value!r} is not an answer to a yes/no question; answer exactly "
                    f"'{YES}' or '{NO}'.")
    else:  # scale_1_5
        if isinstance(value, bool) or not isinstance(value, int) \
                or not SCALE_MIN <= value <= SCALE_MAX:
            return (f"{value!r} is not a whole number from {SCALE_MIN} to {SCALE_MAX}; answer "
                    f"with one number on that scale.")

    for cited in answer["evidence"]:
        if cited in looked or cited in bought:
            continue
        if cited in store_skus:
            return (f"{cited!r} is a product in this store but it is not in your cart, so you "
                    f"did not buy it on this trip; cite only what you saw or bought, or use an "
                    f"empty list.")
        if cited in store_slots:
            return (f"{cited!r} is in this store but you never looked at it on this trip; cite "
                    f"only ids from your own trip, or use an empty list.")
        return (f"{cited!r} is not anything in this store; cite only slot ids you looked at or "
                f"sku ids in your cart, or use an empty list.")

    return None


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------

def _dispositions(policy: Mapping | None) -> str:
    """The persona's standing policy, in words. This is who the respondent is, not what it did."""
    if policy is None:
        return "  - nothing recorded beyond your archetype."
    affinity = {brand: value for brand, value in policy["brand_affinity"].items()
                if brand != "_default"}
    ranked = ", ".join(f"{brand} {value:.2f}"
                       for brand, value in sorted(affinity.items(), key=lambda kv: -kv[1]))
    return (
        f"  - what you came for: {', '.join(policy['goal_categories']) or 'nothing in particular'}\n"
        f"  - how much you lean towards each brand, 0 to 1: {ranked}\n"
        f"  - price sensitivity {policy['price_sensitivity']:.2f}, promotion sensitivity "
        f"{policy['promo_sensitivity']:.2f}, receptiveness to advertising "
        f"{policy['ad_receptivity']:.2f}"
    )


def _trip_lines(trip: Mapping, planogram: Mapping) -> str:
    """The shopper's own trip, one line per action, with what each target actually was."""
    by_slot = {
        slot["slot_id"]: slot["sku_id"]
        for bay in planogram["bays"] for shelf in bay["shelves"] for slot in shelf["slots"]
    }
    skus = {sku["sku_id"]: sku for sku in planogram["skus"]}
    lines = []
    for turn in trip.get("turns", []):
        target = turn.get("target")
        described = ""
        if target and by_slot.get(target):
            sku = skus[by_slot[target]]
            described = f" ({sku['sku_id']}, {sku['brand']} {sku['category']})"
        elif target:
            described = " (an advertising panel or an empty gap)"
        where = f" {target}{described}" if target else ""
        lines.append(f"  turn {turn['turn']} at {turn['station_id']}: {turn['action']}{where}"
                     f" -- \"{turn['reason']}\"")
    return "\n".join(lines) if lines else "  nothing: you walked out without doing anything."


def _cart_line(trip: Mapping) -> str:
    detail = trip.get("cart_detail") or []
    if not detail:
        return "nothing at all"
    return ", ".join(f"{item['sku_id']} ({item['brand']} {item['category']})" for item in detail)


def render_prompt(persona: Mapping, policy: Mapping | None, trip: Mapping, planogram: Mapping,
                  question: Question, *, brand: str) -> str:
    """Render `prompts/persona_survey.md` for one questionnaire item."""
    template = PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8")
    brands = planogram_brands(planogram)
    brand_line = f"The brand this questionnaire is about: {brand}\n" if question.names_brand else ""
    return template.format(
        description=persona["description"],
        dispositions=_dispositions(policy),
        max_reason_words=MAX_REASON_WORDS,
        brands=", ".join(brands),
        brand_line=brand_line,
        trip=_trip_lines(trip, planogram),
        cart=_cart_line(trip),
        question_id=question.question_id,
        question_text=question.text.format(brand=brand),
        response_instruction=response_instruction(question, brands),
    )


def _with_rejections(prompt: str, rejections: Sequence[Mapping]) -> str:
    if not rejections:
        return prompt
    lines = [
        f"- {json.dumps({k: r[k] for k in ('answer', 'evidence', 'reason')})} "
        f"was rejected because {r['rejection']}"
        for r in rejections
    ]
    return (
        f"{prompt}\n\n"
        "Your previous answer to this question was rejected:\n"
        + "\n".join(lines)
        + "\nReply with ONE corrected JSON answer to this same question."
    )


# ---------------------------------------------------------------------------
# One shopper's questionnaire
# ---------------------------------------------------------------------------

def survey_persona(persona: Mapping, policy: Mapping | None, trip: Mapping, planogram: Mapping, *,
                   brand: str | None = None, client: Any = None, model: str | None = None,
                   temperature: float = DEFAULT_TEMPERATURE,
                   max_reasks: int = DEFAULT_MAX_REASKS) -> dict:
    """Ask one persona the whole questionnaire about one completed trip.

    `trip` is a shopper record from `sim.slow_agent.run_persona` -- an element of a trace's
    `shoppers` list. `brand` is the advertised brand the study is about, defaulting to the
    planogram's first creative's brand.

    Pure: no file is read except the prompt template, and nothing is written. Every accepted
    answer lands in `answers`, every rejected one in `rejections` with the sentence the model was
    sent back, and any item that exhausted `max_reasks` lands in `unanswered` rather than being
    answered by this module.
    """
    brands = planogram_brands(planogram)
    brand = brand if brand is not None else default_focal_brand(planogram)
    if brand not in brands:
        raise PersonaSurveyError(
            f"brand {brand!r} is not stocked in planogram {planogram['planogram_id']!r}; "
            f"stocked brands are {', '.join(brands)}"
        )

    looked, bought = trip_vocabulary(trip)
    store_slots, store_skus = _store_vocabulary(planogram)

    answers: list[dict] = []
    rejections: list[dict] = []
    unanswered: list[str] = []

    for question in QUESTIONS:
        base_prompt = render_prompt(persona, policy, trip, planogram, question, brand=brand)
        item_rejections: list[dict] = []
        accepted: dict | None = None

        for _ in range(max_reasks + 1):
            try:
                candidate = complete_json(
                    _with_rejections(base_prompt, item_rejections), ANSWER_SCHEMA,
                    model=model, temperature=temperature, client=client,
                )
            except LLMValidationError as exc:
                # The model never produced schema-valid JSON for this item. Record it as a
                # non-response rather than losing the rest of the questionnaire.
                item_rejections.append({
                    "question_id": question.question_id,
                    "answer": None,
                    "evidence": [],
                    "reason": "",
                    "rejection": str(exc),
                })
                break

            why = reject_reason(candidate, question, brands=brands, looked=looked, bought=bought,
                                store_slots=store_slots, store_skus=store_skus)
            if why is None:
                accepted = candidate
                break
            item_rejections.append({
                "question_id": question.question_id,
                "answer": candidate["answer"],
                "evidence": candidate["evidence"],
                "reason": candidate["reason"],
                "rejection": why,
            })

        rejections.extend(item_rejections)
        if accepted is None:
            unanswered.append(question.question_id)
            continue
        answers.append({
            "question_id": question.question_id,
            "construct": question.construct,
            "response_format": question.response_format,
            "answer": accepted["answer"],
            "evidence": accepted["evidence"],
            "reason": accepted["reason"],
        })

    return {
        "persona_id": persona["persona_id"],
        "archetype": persona.get("archetype", persona["persona_id"]),
        "planogram_id": planogram["planogram_id"],
        "brand": brand,
        "shopper_index": trip.get("shopper_index"),
        "cart": list(trip.get("cart", [])),
        "n_turns": trip.get("n_turns", len(trip.get("turns", []))),
        "answers": answers,
        "unanswered": unanswered,
        "rejections": rejections,
        "n_rejections": len(rejections),
    }


# ---------------------------------------------------------------------------
# A persona's whole panel, and the read-out
# ---------------------------------------------------------------------------

def aggregate(surveys: Sequence[Mapping], *, brand: str) -> dict:
    """Summarise one segment's answers the way a Brand Lift is read out.

    Scale items report the mean and the top-two-box rate (the share answering 4 or 5, which is
    how these questionnaires are normally reported); yes/no items report the yes rate; brand
    items report the answer distribution and the studied brand's share of it.

    An item nobody answered reports `None`, never 0.0 -- an unasked question and a question
    everybody answered "no" to are different findings.
    """
    out: dict[str, dict] = {}
    for question in QUESTIONS:
        values = [a["answer"] for s in surveys for a in s["answers"]
                  if a["question_id"] == question.question_id]
        n_unanswered = sum(1 for s in surveys if question.question_id in s["unanswered"])
        block: dict[str, Any] = {
            "construct": question.construct,
            "response_format": question.response_format,
            "n_answered": len(values),
            "n_unanswered": n_unanswered,
        }
        if question.response_format == "scale_1_5":
            block["mean"] = (sum(values) / len(values)) if values else None
            block["top2box"] = (sum(1 for v in values if v >= TOP_BOX_MIN) / len(values)
                                if values else None)
        elif question.response_format == "yes_no":
            block["yes_rate"] = (sum(1 for v in values if v == YES) / len(values)
                                 if values else None)
        else:
            block["distribution"] = {value: values.count(value) / len(values)
                                     for value in sorted(set(values))} if values else {}
            block["focal_brand_share"] = (values.count(brand) / len(values)) if values else None
        out[question.question_id] = block
    return out


def survey_panel(persona: Mapping, policy: Mapping | None, trace: Mapping, planogram: Mapping, *,
                 brand: str | None = None, client: Any = None, model: str | None = None,
                 temperature: float = DEFAULT_TEMPERATURE,
                 max_reasks: int = DEFAULT_MAX_REASKS, max_shoppers: int | None = None) -> dict:
    """Survey every shopper in one persona's trace and aggregate the segment's answers.

    `trace` is a document from `sim.slow_agent.run_persona` / `write_trace`. `max_shoppers` caps
    how many of its shoppers are surveyed, because one questionnaire item is one model call.

    Pure: nothing is written. Pass the result to `write_survey` to cache it.
    """
    brand = brand if brand is not None else default_focal_brand(planogram)
    shoppers = list(trace["shoppers"])
    if max_shoppers is not None:
        if max_shoppers < 1:
            raise PersonaSurveyError(f"max_shoppers must be at least 1, got {max_shoppers}")
        shoppers = shoppers[:max_shoppers]
    if not shoppers:
        raise PersonaSurveyError(
            f"trace for persona {trace.get('persona_id')!r} holds no shoppers to survey"
        )

    surveys = [
        survey_persona(persona, policy, trip, planogram, brand=brand, client=client, model=model,
                       temperature=temperature, max_reasks=max_reasks)
        for trip in shoppers
    ]

    return {
        "survey_version": SURVEY_VERSION,
        "persona_id": persona["persona_id"],
        "archetype": persona.get("archetype", persona["persona_id"]),
        "description": persona["description"],
        "planogram_id": planogram["planogram_id"],
        "brand": brand,
        "n_shoppers": len(surveys),
        "model": model,
        "temperature": temperature,
        "max_reasks": max_reasks,
        "generated_by": "sim/persona_survey.py",
        "questions": [
            {"question_id": q.question_id, "construct": q.construct,
             "response_format": q.response_format, "names_brand": q.names_brand,
             "text": q.text.format(brand=brand)}
            for q in QUESTIONS
        ],
        "shoppers": surveys,
        "aggregate": aggregate(surveys, brand=brand),
        "n_rejections": sum(s["n_rejections"] for s in surveys),
        "n_unanswered": sum(len(s["unanswered"]) for s in surveys),
    }


_WEIGHTED_FIELDS = ("mean", "top2box", "yes_rate", "focal_brand_share")


def population_aggregate(panels: Mapping[str, Mapping], personas: Mapping[str, Mapping]) -> dict:
    """Roll the per-persona read-outs up to a population, weighted by `share_of_population`.

    This is the single place a persona share touches the survey, and therefore exactly where a
    CPS-seeded (or `analytics/calibration.py`-fitted) share would enter -- see
    `docs/integration.md`. Weights are renormalised over the personas actually present, and again
    over the personas that answered a given item, so an item one segment never answered is not
    silently counted as a zero for that segment.
    """
    missing = [persona_id for persona_id in panels if persona_id not in personas]
    if missing:
        raise PersonaSurveyError(
            f"no persona definition, and therefore no share_of_population, for {sorted(missing)}"
        )
    if not panels:
        raise PersonaSurveyError("no panels to roll up")

    shares = {persona_id: float(personas[persona_id]["share_of_population"])
              for persona_id in panels}
    total = sum(shares.values())
    if total <= 0.0:
        raise PersonaSurveyError(f"persona shares sum to {total}, so there is nothing to weight by")

    out: dict[str, dict] = {}
    for question in QUESTIONS:
        blocks = {persona_id: panel["aggregate"][question.question_id]
                  for persona_id, panel in panels.items()}
        block: dict[str, Any] = {
            "construct": question.construct,
            "response_format": question.response_format,
            "n_answered": sum(b["n_answered"] for b in blocks.values()),
            "n_unanswered": sum(b["n_unanswered"] for b in blocks.values()),
        }
        for field in _WEIGHTED_FIELDS:
            if not any(field in b for b in blocks.values()):
                continue
            defined = {persona_id: b[field] for persona_id, b in blocks.items()
                       if b.get(field) is not None}
            weight = sum(shares[persona_id] for persona_id in defined)
            block[field] = (sum(shares[persona_id] * value
                                for persona_id, value in defined.items()) / weight
                            if weight > 0.0 else None)
        if question.response_format == "brand":
            distributions = {persona_id: b["distribution"] for persona_id, b in blocks.items()
                             if b.get("distribution")}
            names = sorted({name for dist in distributions.values() for name in dist})
            dist_weight = sum(shares[persona_id] for persona_id in distributions)
            block["distribution"] = {
                name: sum(shares[persona_id] * dist.get(name, 0.0)
                          for persona_id, dist in distributions.items()) / dist_weight
                for name in names
            } if dist_weight > 0.0 else {}
        out[question.question_id] = block

    return {
        "shares": {persona_id: share / total for persona_id, share in shares.items()},
        "n_personas": len(panels),
        "aggregate": out,
    }


def write_survey(survey: Mapping, *, cache_dir: Path | str) -> Path:
    """Write `survey` to `{cache_dir}/{persona_id}_{planogram_id}_survey.json`; return the path.

    `cache_dir` has no default on purpose, for the same reason `sim.slow_agent.write_trace` has
    none: an answer produced against a test double is not survey data, and making the destination
    explicit means no test can write to `data/cache/surveys/` by accident.
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{survey['persona_id']}_{survey['planogram_id']}_survey.json"
    path.write_text(json.dumps(survey, indent=2) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Entry point: `python -m sim.persona_survey --all`
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m sim.persona_survey",
        description="Ask each persona a Brand Lift-style questionnaire about the shopping trips "
                    "in its committed trace. Requires LLM_API_KEY; there is no offline fallback, "
                    "because an answer that did not come from a model is not survey data.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--persona", action="append", choices=PERSONA_IDS,
                       help="persona to survey; repeat for several")
    group.add_argument("--all", action="store_true", help=f"survey all of: {', '.join(PERSONA_IDS)}")
    parser.add_argument("--brand", default=None,
                        help="advertised brand the study is about (default: the planogram's "
                             "first creative's brand)")
    parser.add_argument("--max-shoppers", type=int, default=None,
                        help="survey at most this many shoppers per persona")
    parser.add_argument("--planogram", type=Path, default=DEFAULT_PLANOGRAM_PATH)
    parser.add_argument("--personas-dir", type=Path, default=DEFAULT_PERSONA_DIR)
    parser.add_argument("--policy-dir", type=Path, default=DEFAULT_POLICY_DIR)
    parser.add_argument("--traces-dir", type=Path, default=DEFAULT_TRACE_DIR,
                        help="where sim.slow_agent wrote its traces (default data/cache/traces)")
    parser.add_argument("--out", type=Path, default=DEFAULT_SURVEY_DIR,
                        help="survey cache directory (default data/cache/surveys)")
    parser.add_argument("--model", default=None, help="override LLM_MODEL for this run")
    parser.add_argument("--temperature", type=float, default=DEFAULT_TEMPERATURE)
    parser.add_argument("--max-reasks", type=int, default=DEFAULT_MAX_REASKS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    persona_ids = list(PERSONA_IDS) if args.all else list(dict.fromkeys(args.persona))

    planogram = _load_json(args.planogram)
    planogram_id = planogram["planogram_id"]

    traces: dict[str, dict] = {}
    for persona_id in persona_ids:
        trace_path = args.traces_dir / f"{persona_id}_{planogram_id}.json"
        if not trace_path.exists():
            print(f"persona_survey: no trace at {trace_path}", file=sys.stderr)
            print(f"The survey asks a persona about a trip it actually took, so "
                  f"{trace_path.name} must exist first. Run "
                  f"`python -m sim.slow_agent --persona {persona_id}` to produce it.",
                  file=sys.stderr)
            return 2
        traces[persona_id] = _load_json(trace_path)

    personas = {persona_id: _load_json(args.personas_dir / f"{persona_id}.json")
                for persona_id in persona_ids}
    policies = {}
    for persona_id in persona_ids:
        policy_path = args.policy_dir / f"{persona_id}_{planogram_id}.json"
        if policy_path.exists():
            policies[persona_id] = _load_json(policy_path)

    panels: dict[str, dict] = {}
    try:
        for persona_id in persona_ids:
            panels[persona_id] = survey_panel(
                personas[persona_id], policies.get(persona_id), traces[persona_id], planogram,
                brand=args.brand, model=args.model, temperature=args.temperature,
                max_reasks=args.max_reasks, max_shoppers=args.max_shoppers,
            )
    except LLMUnavailableError as exc:
        # Deliberately no cached fallback: a questionnaire answer that did not come from a model
        # is not survey data, and these answers are compared against a real Brand Lift. Nothing is
        # written; the cache stays empty until a real run fills it.
        print(f"persona_survey: {exc}", file=sys.stderr)
        print("No surveys were written. The persona survey has no offline fallback -- an answer "
              "nobody was asked is not a survey response.", file=sys.stderr)
        return 2

    for persona_id, panel in panels.items():
        path = write_survey(panel, cache_dir=args.out)
        agg = panel["aggregate"]
        print(f"{persona_id}: {panel['n_shoppers']} shoppers surveyed, "
              f"{panel['n_rejections']} answers rejected, {panel['n_unanswered']} unanswered, "
              f"purchase-intent mean {agg['purchase_intent']['mean']} -> {path}")

    if len(panels) > 1:
        rolled = population_aggregate(panels, personas)
        print(f"population (share-weighted over {rolled['n_personas']} personas): "
              f"purchase-intent mean {rolled['aggregate']['purchase_intent']['mean']}, "
              f"ad recall {rolled['aggregate']['ad_recall']['yes_rate']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
