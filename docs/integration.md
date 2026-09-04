# Integration: CPS demographics and Brand Lift

**S22 (PLAN §5, Track E).** PLAN §13 replaces SPEC's "CPS/Brand Lift = roadmap slide" with
"`docs/integration.md` + `sim/persona_survey.py`". This is that document: how Consumer Panel
Services (CPS) demographics would seed the persona population shares, and how Brand Lift questions
become a persona post-shop survey.

## 0. What is built and what is designed

Read this table before anything else in this document.

| | Status | Where |
|---|---|---|
| The post-shop survey instrument (5 items, response formats, guards) | **Built** | `sim/persona_survey.py` |
| Asking a persona those questions about a trip it actually took | **Built** | `survey_persona`, `survey_panel` |
| The Brand Lift read-out (mean, top-two-box, yes rate, brand distribution) | **Built** | `aggregate` |
| Rolling the segments up to a population by persona share | **Built** | `population_aggregate` |
| Persona shares fitted from a real panel by grid search | **Built** | `analytics/calibration.py` |
| Persona shares **seeded from CPS demographics** | **Designed only** | §1 below |
| A **real Brand Lift result** to compare the synthetic answers against | **Designed only** | §2.4 below |
| Survey answers from an actual model run | **Not produced** | needs `LLM_API_KEY`; see §3 |

No CPS data has been obtained, licensed, or used. No Brand Lift study has been run. No number in
this repository is derived from either. PLAN §8 lists "real CPS data (design only)" under *Not
building*, and this document does not quietly widen that. `data/cache/surveys/` does not exist
until someone runs the command in §3 with a real API key, and `sim/tests/test_persona_survey.py`
asserts it stays that way — the same rule `sim/slow_agent.py` applies to persona traces, and the
same rule `RESULTS.md` applies when it prints "not yet collected" instead of `0.00`.

---

## 1. CPS demographics → persona shares

### 1.1 What the model needs a panel for

The synthetic panel is four archetypes — `mission`, `browser`, `loyalist`, `switcher` — and one
number per archetype: `share_of_population` in `data/personas/*.json`, currently
0.35 / 0.25 / 0.25 / 0.15, summing to 1. Every population-level figure the project reports is that
mixture: `sim/simulator.py:combine()` blends each per-persona result as `sum(share × value)`, and
`population_aggregate` in `sim/persona_survey.py` weights the survey read-out the same way.

The shares are therefore the single most load-bearing assumption in the model, and today they come
from nowhere better than judgement (the seed values) or from a fit against our own small real panel
(`analytics/calibration.py`). A household panel is the standard instrument for getting them from
data instead.

### 1.2 The fields the integration would ask CPS for

CPS is a continuously reporting household panel: demographics plus recorded purchases. The
integration would request, for the category and market the store models, per panel household:

**Classification**
- household size, presence and age of children
- age and gender of the main shopper
- income band and socio-economic grade
- region and urbanicity
- store and channel mix (which banners the household actually shops)

**Behaviour, per household per category**
- trip frequency and average interval between category purchases
- basket size and category spend per trip
- brand repertoire — the count of distinct brands bought over the period
- share of category requirements (SCR) for the household's largest brand
- share of category volume bought on promotion
- own-label share
- rate of first-time-in-household SKUs (a proxy for exploration)

The first block is the demographic seed; the second is what actually separates the archetypes.

### 1.3 Mapping onto the four archetypes

The archetypes are **trip modes**, not household types. This is the load-bearing subtlety: one
household shops in mission mode on Tuesday and browses on Saturday. So the mapping is a
trip-weighted decomposition, not a household segmentation, and it produces a share of *trips*,
which is what the simulator consumes.

| Archetype | CPS signature |
|---|---|
| `mission` | high trip frequency, small baskets, short intervals, high repeat rate on the same SKU, low new-SKU rate |
| `browser` | low trip frequency, large baskets, broad category penetration, high first-time-in-household SKU rate |
| `loyalist` | high SCR on one brand, small brand repertoire, low promotion share of volume |
| `switcher` | low SCR, large brand repertoire, high promotion share of volume, higher own-label share |

Demographics do not appear in that table on purpose. They enter one step earlier: they are what
lets a share estimated on the panel be **projected** to a store's own shopper base. The panel is
weighted to the population; a specific store is not the population, so the seeded share for a
given store is the panel share re-weighted onto that store's catchment demographics (age, income,
household size, urbanicity), which is exactly the calculation a panel provider already does for
projection. Without that step the shares describe the country, not the aisle being modelled.

The output of §1.2–1.3 is four numbers summing to 1: the **prior**.

### 1.4 How the seeded prior meets the calibration that exists

`analytics/calibration.py` today runs an unconstrained grid search: every 4-way split of 1.0 in
steps of 0.05 (1,771 candidates), scored as `(1 − attention_spearman) + 5 × purchase_share_mae`
against variant A's real panel, with B and C held out. It takes no prior and has no place to put
one. The three ways a CPS prior could be brought in, in increasing order of intrusiveness:

1. **Report both, adjust neither.** The CPS prior and the fitted shares are printed side by side
   and the distance between them is a diagnostic. If a fit on 60 sessions lands far from a panel
   of thousands of households, that is a finding about the fit, not about the population. This
   needs no change to `calibration.py` at all and is what we would do first.
2. **Penalise the distance.** Add `λ × ‖shares − prior‖₁` to the objective, so the search may
   move away from the panel only when the store data pays for it. This is a new keyword argument
   on `calibrate()` and a new term in `_score`; neither exists today.
3. **Restrict the grid.** Keep only candidates within a fixed radius of the prior — the panel's
   own sampling error on each share is the natural radius. Cheapest to implement (it filters
   `share_grid`), harshest in effect, and it makes the fit uninterpretable if the radius is
   guessed rather than taken from the panel's standard errors.

The division of labour that makes sense: **CPS says how the population divides; calibration says
how this store's shoppers differ from it.** PLAN §11 already names "calibration overfits" as a
medium risk and answers it with "persona shares only; report fit and holdout side by side" — a
prior is the natural second answer to the same risk, because it bounds how far 60 sessions are
allowed to move a population estimate.

One trap to avoid: if CPS seeds the shares *and* the same shares are then fitted on variant A, the
holdout (B, C) is still clean but the prior is not independent evidence for the fit. Option 1 keeps
them separable, which is why it is first.

### 1.5 What would have to be true for this to be valid

- The panel covers the **same market, category and channel** as the modelled store. A biscuit
  aisle in one market says nothing about another.
- The **trip-mode decomposition is estimated, not asserted.** §1.3 is a hypothesis; turning
  household purchase metrics into trip-mode shares requires a linking study — a subset of panel
  households whose individual trips are labelled by mode, which CPS purchase records alone do not
  contain.
- The **projection weights are the store's**, not the country's (§1.3).
- The **benchmark panel is projectable.** Ours is not: the real panel in this project is a
  convenience sample of whoever sat at the laptop. Comparing a projectable CPS share against a
  convenience-sample fit is a category error, and would need saying out loud in any report that
  did it.
- The archetype set is **stable across the two sources**. Four archetypes are a modelling choice
  made in `data/personas/`; nothing guarantees a panel's natural clusters are these four, and if
  they are not, the mapping table above is where the disagreement has to be resolved.

---

## 2. Brand Lift → the persona post-shop survey

### 2.1 The instrument

A Brand Lift study asks a short questionnaire after ad exposure and compares an exposed cell
against a control cell. `sim/persona_survey.py` asks a persona the same five constructs after its
shopping trip. The question set is the module constant `QUESTIONS`; the table below is that
constant, and `sim/tests/test_persona_survey.py::test_docs_integration_md_cites_every_question_in_the_constant`
fails if this document and the code drift apart.

| `question_id` | Construct | Response format | Question as asked |
|---|---|---|---|
| `unaided_awareness` | unaided brand awareness | one stocked brand, or `none` | "Thinking back on the aisle you just shopped, which brand comes to mind first?" |
| `aided_awareness` | aided brand awareness | yes / no | "Did you see {brand} on the shelves in that aisle today?" |
| `ad_recall` | advertising recall | yes / no | "Do you remember seeing an advertising panel for {brand} anywhere in that aisle today?" |
| `brand_consideration` | brand consideration | 1–5 | "Next time you shop this aisle, how likely are you to consider {brand}?" |
| `purchase_intent` | purchase intent | 1–5 | "How likely are you to buy {brand} on your next shopping trip?" |

Two ordering rules are enforced by the code, not by convention:

- **`unaided_awareness` is asked first, and its prompt never names the studied brand.** Naming it
  would make the item aided, and the questionnaire would measure the same thing twice.
  `Question.names_brand` carries this and a test pins it.
- **Answers are closed-format, not free text**, so a synthetic answer and a human answer are the
  same kind of object. A brand answer must be a brand the planogram actually stocks (Crunch,
  Nimbus, Orchid, Zapp in `demo_aisle`) or `none`; a scale answer must be a whole number 1–5;
  a yes/no answer must be exactly `yes` or `no`.

### 2.2 How a synthetic answer is produced

1. A persona shops the store via `sim/slow_agent.py`, which leaves a **trip**: every action, its
   target, the reason, and the final cart.
2. For each questionnaire item in order, `survey_persona` renders `sim/prompts/persona_survey.md`
   with the persona's description, its policy dispositions from `sim/policy.py` (goal categories,
   brand affinities, price/promo/ad sensitivities), **its own trip**, and its cart.
3. The model replies with one JSON object: `{"answer": …, "evidence": [...], "reason": "…"}`.
   `sim/llm_client.py:complete_json` validates the *shape* against `ANSWER_SCHEMA` and retries on
   its own; `persona_survey.reject_reason` then applies the checks a single static schema cannot,
   because they are per-call facts — the response format belongs to the question, the brand
   vocabulary to the planogram, the evidence vocabulary to the trip. A rejection is fed back
   verbatim and the item is re-asked, capped by `max_reasks`.
4. An item that survives the re-ask budget is left **unanswered**. Item non-response is a real
   survey outcome; an answer invented by the harness is not.

Every answer carries a `reason` of at most 25 words, so a read-out is auditable down to the
individual response rather than being a bare number.

### 2.3 What is guarded, and what deliberately is not

The `evidence` field lists ids from the shopper's own trip — a slot it looked at, or a sku in its
cart. **Only `evidence` is audited against the record.** Citing a sku that is not in the cart is
rejected with "you did not buy it on this trip"; citing a slot the shopper never looked at, or an
id that is not in the store at all, is likewise rejected.

The *attitude values are never overruled*. A persona may rate `purchase_intent` 5 for a brand it
did not buy today, or answer `no` to `ad_recall` after walking past the endcap header. Forcing
stated answers to match the behaviour log would make the survey a re-reading of the trip file and
destroy the only thing it measures. The rule is: **attitudes are free, claims about the record are
not.**

This is also something a synthetic panel can do that a real one cannot. A real Brand Lift has no
per-respondent ground truth to audit an answer against; here the trip is the ground truth, so a
fabricated claim is caught at collection time.

### 2.4 Comparing against a real Brand Lift

Nothing below has been run — this is the design.

- **Cells.** A real Brand Lift compares an exposed cell against a matched control. Two ways to
  build the synthetic pair: (a) **by variant** — survey the same personas over a variant carrying
  the creative and a variant with the ad slot emptied. Note that the committed variants do *not*
  give this for free: A and C both carry `AD_1` and only differ in *where* it hangs (`B3_ENDCAP`
  versus `B1_TALKER`), so a true control cell means a new variant that sets the creative to
  `null`. (b) **by exposure within one variant** — split the shoppers on trip-level ad exposure,
  which is exactly the split `analytics/lift.py` already uses (did this trip fixate a
  creative-carrying ad slot at any point). (b) needs no new data and is where we would start;
  (a) is the cleaner design because exposure in (b) is self-selected rather than assigned.
- **Statistic.** For each construct, the lift is the difference between cells: percentage points
  for `aided_awareness` and `ad_recall` (yes rate), percentage points of **top-two-box** for
  `brand_consideration` and `purchase_intent`, and percentage points of focal-brand share for
  `unaided_awareness`. `aggregate` emits exactly those three quantities, so the arithmetic would
  be the same on both panels — the property `analytics/lift.py` already enforces for the purchase
  metric.
- **Uncertainty.** The real cell would carry a bootstrap CI over respondents, following
  `analytics/lift.py:bootstrap_lift_ci`. As there, the synthetic side's spread is a run-size
  decision, not a sampling interval, so the interval attaches to the real number and the synthetic
  number is the one being judged against it.
- **Benchmark.** The same discipline as the rest of the project: a synthetic-vs-real agreement is
  meaningless without the real instrument's own repeatability, so a real Brand Lift comparison
  would need a split-half of the real survey to quote against, the way `analytics/noise_ceiling.py`
  does for attention.

### 2.5 Stated versus revealed

This is the contrast that makes the survey worth building rather than a checkbox.

| | Instrument | What it measures |
|---|---|---|
| **Stated** | `sim/persona_survey.py` | what the shopper *says* after the trip — awareness, recall, consideration, intent |
| **Revealed** | `analytics/lift.py` | what the ad was *worth* — purchase share of the advertised brand, exposed versus not |

Both are computed from the **same shoppers on the same trips**. That is unusual: a real Brand Lift
survey and a real purchase panel are almost never the same people, so the industry compares a
stated lift for one sample against a sales result for another and hopes. Here they are the same
sample by construction.

A gap between them is a **finding, not a bug**. High recall with no purchase lift points at a
creative that is noticed and ignored; purchase lift with no stated recall points at an effect
running below self-report, which is precisely what stated-preference measurement is bad at and
where a behavioural model earns its place. The revealed half is the metric PLAN §9 puts on the
never-drop list, for the reason S18 gives: attention is a commodity, and a purchase number is what
an attention vendor cannot produce. The survey is the half a survey vendor *can* produce — having
both, on the same shoppers, is what neither can.

### 2.6 Known differences between the two instruments

Stated plainly, because they bound how far the comparison in §2.4 can be pushed.

- **The unaided item is only quasi-unaided.** A real questionnaire asks it open-ended and
  back-codes the verbatim to a brand list afterwards. Here the coding frame — all four stocked
  brands, listed symmetrically — is shown up front so the answer is directly comparable. The
  studied brand is not revealed, so the item is not *aided* in the damaging sense, but it is not
  the same item a human is asked.
- **Response style is not calibrated.** Human scale answers carry acquiescence and scale-use
  biases that vary by market and by respondent. A language model has its own, and they are not the
  same ones. Any real comparison must either compare *differences between cells* (where a constant
  style bias cancels) rather than levels, or fit the mapping between the two response
  distributions explicitly.
- **No control cell has been run**, synthetic or real, so no lift of any kind is reported here.
- **One item is one model call.** A full run is `5 × shoppers` calls per persona; `--max-shoppers`
  exists to cap that.

---

## 3. Running the survey

The survey has no offline fallback, for the same reason persona traces do not: an answer nobody
was asked is not a survey response. With no `LLM_API_KEY`, the entry point prints why and writes
nothing.

```
# 1. produce the trips (needs LLM_API_KEY)
python -m sim.slow_agent --all --n 20

# 2. survey the personas about those trips
python -m sim.persona_survey --all
python -m sim.persona_survey --persona mission          # one segment
python -m sim.persona_survey --all --max-shoppers 5     # cap the cost
```

Each writes `data/cache/surveys/{persona_id}_{planogram_id}_survey.json`, containing the
instrument as asked, every shopper's answers with their evidence and reasons, every rejection, and
the segment aggregate. With more than one persona the command also prints the share-weighted
population roll-up — the one place a persona share, CPS-seeded or calibration-fitted, touches the
survey.

## 4. What this document does not claim

- No CPS data has been licensed, obtained, or used, and no CPS-seeded share exists. §1 is a design.
- No Brand Lift study has been run against these personas or against anyone else, and no lift is
  reported. §2.4 is a design.
- No survey answers have been generated at all: there is no API key in this repository, and
  answers produced against a test double would be fabricated data, so none are committed.
- The persona shares in `data/personas/*.json` are seed values chosen by hand, refined only by
  `analytics/calibration.py` when a real panel exists. They are not measured from any panel.
