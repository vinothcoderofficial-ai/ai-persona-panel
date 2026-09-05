# Methodology

> Written during S23. This file is what makes the accuracy claims checkable — do not skip it.

Everything below names the file it is implemented in, so any statement here can be checked
against the code rather than believed. Where a procedure is built but has not yet been run on
real data, this document says so in the same sentence as the procedure.

---

## 0. What has been measured, and what has not

This is the first section on purpose. The rest of the document describes a comparison between a
real panel and a synthetic one. **At the time of writing only one of those two panels exists.**

| | State |
|---|---|
| Synthetic panel (10,000 shoppers × 4 personas × 3 variants) | **Computed.** Regenerate with `make eval`; every number in `RESULTS.md`'s synthetic sections comes from it. |
| Real panel (`data/sessions/anon/`) | **Empty.** No human has shopped a recorded session. PLAN S9 (webcam pilot on 5 laptops) and S21 (collect ≥ 60 sessions) are outstanding. |
| Prediction locks (`predictions/`) | **Empty**, because a lock is written per real session and there are none. The lock machinery is built, tested and enforced at both ends (§6). |
| Persona decision traces (`data/cache/traces/`) | **Empty.** The S13 agent loop is built and tested; there is no `LLM_API_KEY` in this repo, and `sim/slow_agent.py` will not write a trace produced by a test double, because those traces are read on screen as if a model wrote them. |
| Persona post-shop survey answers (S22) | **Not produced**, for the same reason: `sim/persona_survey.py` is built and tested, `docs/integration.md` describes the design, and no CPS data has been obtained or used. |
| Every real-vs-synthetic metric | **Not computed.** `scripts/eval.py` prints `not yet collected` for each one and refuses to draw a figure whose bars would all be zero. |

So: the *procedures* below are implemented and tested. The *comparison* they exist to make has
not been made. Nothing in this document, in `README.md` or in `RESULTS.md` should be read as
"the synthetic panel was found to match real shoppers". It has not yet been checked against any.

---

## 1. Why shelf stations rather than free roam

The camera is fixed per bay. `bay.station` in `schemas/planogram.schema.json` carries a
`camera_pos` and a `look_at`; `web/src/store/StationController.tsx` lerps between adjacent
stations over 600 ms on ←/→ and otherwise holds the camera perfectly still. There is no
free-roam movement anywhere in the store.

This is a measurement constraint, not an art direction. Webcam gaze regression maps a face image
to a *screen* coordinate. Turning a screen coordinate into a *shelf* coordinate requires knowing
where the shelf was on screen at that instant. With a moving camera the projection changes every
frame, the mapping has to be resampled continuously, and the tracker's own error (already several
degrees) is compounded by any error in that resampling. With a fixed camera, one screen rectangle
per slot per station is enough, and `web/src/store/SlotMapper.ts` computes exactly that: the
`hitTest` vitest iterates every slot at every station.

The cost is honest and worth stating: shoppers cannot approach a shelf or crouch, so the
naturalness of the trip is reduced in exchange for a gaze signal that can be attributed to a
slot at all. A running lerp republishes the slot rectangles on arrival, so no fixation is ever
attributed against a stale projection.

---

## 2. Noise pipeline, with parameters

Raw webcam gaze is not data. Four filters stand between the camera and a stored fixation, and a
fifth decides whether the whole session counts. Per PLAN §13 the pipeline lives **only in the
browser**; the server stores fixations as received and there is no Python twin to drift from it.

### 2.1 Calibration gate — `web/src/capture/calibrationMath.ts`

Nine-point calibration, then a **separate** four-point validation at points the regression was
not trained on (measuring error where the model was fitted would measure nothing). The mean
validation error is compared to the screen width:

- `CURSOR_ONLY_ERROR_FRACTION = 0.12`
- error **strictly greater than** 12 % of screen width ⇒ `mode: "cursor_only"`. Exactly 12.0 %
  stays `webcam`.
- an unusable measurement (no error, non-finite, zero-width screen) also falls back to
  `cursor_only` rather than passing a NaN downstream.

Nobody is turned away. A person whose calibration fails still shops; their session is simply
fused with the cursor-only weights (§3) and labelled as such.

### 2.2 Sample filter, smoothing, fixation detection — `web/src/capture/FixationFilter.ts`

| Constant | Value | What it does |
|---|---|---|
| `MIN_CONFIDENCE` | `0.5` | Samples below it are dropped before anything else. |
| `MEDIAN_WINDOW` | `5` | Median filter over the last five surviving samples. |
| `DISPERSION_PX` | `60` | I-DT dispersion threshold: a candidate stays a fixation while the window's spread is ≤ 60 px. |
| `MIN_FIXATION_MS` | `100` | A dispersion cluster shorter than this is discarded, not emitted. |

A surviving fixation is reduced to its centroid and `hitTest`ed to a slot. A fixation that lands
on bare shelf carries `slot_id: null` and is *skipped* by the fusion formula — it enters no
denominator (§3).

### 2.3 Session gate — `web/src/capture/SessionGate.ts`

A session is accepted iff **all** of: consent given, `duration_s >= 45`,
`stations_visited >= 2`, at least one interaction, and — webcam sessions only —
`fixation_coverage >= 0.4`.

Rejection reports the **first** failure in a fixed order:

```
no_consent → too_short → one_station → no_interaction → low_coverage
```

The order is fixed and tested (`REJECT_ORDER` and `firstFailure` must agree) for one reason:
a reject-reason histogram is only readable if one session always yields one answer.

`fixation_coverage` is summed fixation `dur_ms` over session duration, clamped to [0, 1]. It
measures how much of the session the tracker was *resolving gaze at all*, not how much of it
landed on a product — so a fixation on bare shelf counts toward coverage. A cursor-only session
has no fixations and therefore coverage 0 by construction, which is exactly why the coverage
rule is webcam-only.

### 2.4 The Day-7 freeze

PLAN §6 requires these parameters to be frozen at the end of Day 7 and the freeze commit hash
recorded here. **The freeze has been made.**

```
tag     freeze-noise-params
commit  dc95a7b9d5c4135daac7499ed09d30f1453a341f
```

The annotated tag lists all nine values, and they were checked against this section at tag time
rather than assumed to still agree. The three files carrying them —
`web/src/capture/calibrationMath.ts`, `FixationFilter.ts` and `SessionGate.ts` — had not changed
since `69302fd`; the tag fixes the point at which they stop changing.

The freeze is what makes a collected panel meaningful: every session is measured through these
exact thresholds, so changing one retroactively changes what every earlier session *was*. Any
later edit to a value above is therefore a new tag and a new panel, never an amendment to this
one. Each parameter remains a named exported constant with exactly one definition site, so there
is nowhere for a second, drifting copy to hide.

---

## 3. Attention fusion, and what the weights actually are

One implementation, `analytics/fusion.py`. `api/app/live.py` imports `fuse_session` on its hot
path rather than reimplementing it, so the live agreement meter and the offline evaluation can
never disagree — a parity test replays a recorded session through the socket and asserts the
final vector equals the offline one.

### 3.1 The real panel

Per slot, per session, normalised so each channel sums to 1 across the session's slot vocabulary:

```
cursor_only:  att = 0.7 × cursor_dwell_norm + 0.3 × interaction_norm
webcam:       att = 0.5 × fixation_dwell_norm + 0.3 × cursor_dwell_norm + 0.2 × interaction_norm
```

`interaction_norm` is the **maximum** interaction weight seen for that slot
(`hover = 0.5`, `pickup = 1.0`, `add_to_cart = 1.0`) — never a sum and never a count, so a
shopper who fiddles with one pack ten times does not out-vote one who picked up ten packs once.

Across sessions the panel estimate is a 10 %-per-tail trimmed mean and its uncertainty is a
1,000-resample bootstrap 95 % interval.

**A correction to this document's own outline.** The S23 outline asked for "why gaze is weighted
below interaction". In the implemented formula it is not: fixation dwell carries 0.5 and
interaction 0.2. The defensible claim is the weaker and truer one — **gaze never carries the
whole vector.** At best half of a session's attention score comes from the eye tracker; the
other half comes from the cursor and from what the shopper actually touched. In `cursor_only`
mode, which is the default and the only mode any data has been collected in so far, gaze carries
nothing at all. That is the design response to a webcam eye tracker whose per-sample error is
large and whose confidence signal is fabricated (§12).

### 3.2 The synthetic side

A real attention vector fuses looking *and* touching. Scoring it against the simulator's raw
`fixation_prob` would correlate "looking plus touching and buying" against "looking only".
`fuse_synthetic` gives the synthetic side a matching interaction channel, with weights **derived
from the same table** rather than written down twice:

```
cursor_only:  0.7 × fixation_prob_norm + 0.3 × synthetic_interaction_norm
webcam:       0.8 × fixation_prob_norm + 0.2 × synthetic_interaction_norm
```

The synthetic side has one looking channel where the real side has two, so the two real looking
weights collapse onto `fixation_prob` and the interaction weight carries across unchanged.
`synthetic_weights()` computes this from `_MODE_WEIGHTS`; retuning the real weights retunes the
synthetic ones in the same edit.

The synthetic interaction channel is `purchase_share`: a simulated shopper who buys a SKU
necessarily picked it up and added it to cart. Each SKU's share is credited to the slot it
occupies **in the resolved planogram passed in**, because a SKU moves between slots from variant
to variant and a fixed slot id would measure the new occupant of the old shelf position.

This change was made because it was measured, not because it sounded better: see §9.

### 3.3 The live meter and the offline evaluation compute the same ρ

`api/app/live.py` fuses the real side with `fuse_session` and the synthetic side with
`fuse_synthetic` — the same two functions `scripts/eval.py` calls, imported, not reimplemented.
The ρ on the spectator screen during a recording and the ρ in `RESULTS.md` are therefore
correlations against the *same* synthetic vector.

They were not always. Until this was fixed the live meter compared against the lock's
`population_fixation_prob` **raw** while `eval.py` compared against `fuse_synthetic` of the same
run (§3.2), so a demo could show one number while the report showed another. Replaying one
session through both paths now gives identical values —
`api/tests/test_live.py::test_live_spearman_equals_the_offline_evaluation_spearman` measures
0.4417391304347826 both ways in webcam mode and 0.248695652173913 both ways in cursor-only mode,
a difference of exactly 0.

**This does not weaken the pre-registration.** `fuse_synthetic` is a deterministic transform of
the locked run: the locked vector *is* its looking channel, and the other input is the same
resolved planogram and `purchase_share` the lock was computed from. Nothing was added to the lock
file, and `sha256` still covers exactly `population_fixation_prob` + `sim_run_id` + `created_at`
(§6). The lock stores neither the SimResult nor the planogram, so `live.open_state` recomputes the
locked simulation once per session through the same cached, deterministic `simcache.population`
call `prediction.write_lock` made, and then **verifies** it: `sim_run_id` must match, and the
freshly simulated `population_fixation_prob` must still equal the locked one to within `1e-12`. A
lock the simulator no longer reproduces closes the ingest socket (close code 4410) rather than
letting a session be recorded that could never be evaluated honestly. The second check is the one
with teeth — `sim_run_id` is a hash of `variant_id|persona_id|n_runs|seed` alone, so it does not
notice a changed planogram, changed policies or changed saliency maths, and the vector comparison
does.

**What the spectator screen shows beside the meter is still the raw locked vector, deliberately.**
`GET /sessions/{id}/prediction` serves `population_fixation_prob` unchanged and `LiveHeatmap`
draws that column, because its job is to display the exact vector the badge's hash covers. So the
locked heatmap column and the meter's ρ are against slightly different vectors — the fused one is
that column renormalised at weight 0.7 (or 0.8 in webcam mode) plus a `purchase_share` term, so
the two differ only where purchases reorder the ranking. Showing the fused vector there instead
would make the on-screen evidence something other than what was committed, which is a worse trade
than this footnote.

**One asymmetry remains, and it is in `eval.py`, not the live meter.** Each real session is fused
with its own `mode`, but the synthetic vector is one vector per variant, so `eval.py` fuses it
with the panel's *dominant* mode. On a mixed-mode panel a single session's live ρ and its
contribution to the offline number would use different synthetic weights. Every session that
exists is `cursor_only`, so this is currently a difference of nothing; it becomes real the day a
webcam panel is collected alongside a cursor-only one.

#### `meaningful`: a deliberate deviation from SPEC 4.7

SPEC 4.7 says *"`meaningful` is false until `n_fixations >= 15`"*. That was written assuming a
webcam session. There is no webcam panel — `data/sessions/anon/` is empty, the S9 pilot was never
run, and every session the demo can produce is `cursor_only`, a mode whose gaze trail is empty by
construction and whose fixation count is therefore permanently 0. Read literally, SPEC 4.7 leaves
the agreement meter reading "warming up" for the whole of every session that exists. That is not
a conservative safeguard; it is a dead readout on a headline shot.

So `meaningful` counts the channel that actually carries each mode's attention signal:

| mode | counts | threshold |
|---|---|---|
| `webcam` | `fixation` events — SPEC 4.7 unchanged | 15 |
| `cursor_only` | `cursor_dwell` events — the 0.7 term of its fusion formula | 15 |

The threshold stays 15 in both, and the two are comparable units rather than a reused number: a
fixation must last 100 ms to be emitted at all (`MIN_FIXATION_MS`) and a cursor dwell must last
300 ms (`CURSOR_DWELL_MIN_MS`), so 15 dwells is if anything the stricter bar in elapsed
attention. Measured boundaries, both exact: a cursor-only session is not meaningful at 14 dwells
and is at 15; a webcam session is not meaningful at 14 fixations and is at 15. Neither mode
counts the other's channel.

**No count on screen is labelled as something it is not.** The SPEC 4.7 message keeps
`n_fixations` with its literal meaning, adds `n_cursor_dwells` with its own, and adds
`evidence_count` / `evidence_kind` naming which of the two the threshold was applied to.
`web/src/spectator/AgreementMeter.tsx` prints the label the server sent, so a cursor-only
session's meter reads "9 of 15 cursor dwells", never "9 of 15 fixations", and it still refuses to
render ρ at all while `meaningful` is false.

---

## 4. Saliency model

`sim/saliency.py` answers "what would anyone notice", with no persona in it. Six terms, blended
with the only tunable constants in the module:

| Term | Weight |
|---|---|
| shelf level | 0.30 |
| horizontal centre | 0.15 |
| facings | 0.20 |
| colour contrast against neighbours | 0.15 |
| ad adjacency | 0.10 |
| slot area | 0.10 |

Shelf-level scores: `eye 1.0 · above_eye 0.75 · below_eye 0.7 · top 0.5 · bottom 0.35`.
Raw scores become a per-bay probability through a softmax at temperature `0.15`.
Ad-slot raw scores: `screen 0.7 · endcap_header 0.6 · shelf_talker 0.4 · floor_decal 0.3`.

A bay's fixation targets are its occupied slots plus its ad slots carrying a creative. **Empty
slots are real objects** (`sku_id: null`, `facings: 0`) and are never fixation targets, but they
still occupy shelf space: they count toward the bay's max facings and max slot area and they
break left/right adjacency for the colour term. That is what makes "move this SKU to eye level"
expressible as a patch rather than as a rebuild — the seed aisle keeps one eye-level position
free in every bay for exactly this reason (30 slots, 24 SKUs, 6 empty).

The persona layer never invents a gaze pattern; it reweights this one (§5).

---

## 5. Persona policies, persona agents, and the fast path

Three distinct things, often conflated:

**A persona** (`data/personas/*.json`) is an archetype and a population share:
`mission`, `browser`, `loyalist`, `switcher`.

**A policy** (`schemas/policy.schema.json`) is the numeric decision profile:
`goal_categories`, `time_budget_s {mean, sd}`, `exploration`, `brand_affinity` (per brand plus
`_default`), `price_sensitivity`, `promo_sensitivity`, `ad_receptivity`, `purchase_threshold`,
`dwell_ms {mu, sigma}`, `fixations_per_station {lam}`.

`sim/policy.py` generates a policy from `sim/prompts/persona_policy.md` at temperature 0 and
caches it to `data/cache/policies/{persona}_{planogram}.json`. On top of JSON-schema validation
it adds the semantic check a schema cannot express: a policy naming a brand, a category or a
`persona_id` that does not exist in the target planogram is rejected, not stored.

> **Provenance, stated plainly.** The four policy files currently committed were **written by
> hand** in S2, not produced by a language model — there is no API key in this repository, and
> `sim/tests/test_policies.py` opens with that fact. The generator is built and tested against a
> mocked model; it has not yet authored the policies in use. Any claim that "an LLM designed the
> personas" would today be false.

**A persona agent** (`sim/slow_agent.py`, S13, never-drop) is the slow path: 20 shoppers per
persona step through the store one action at a time through an actual language model, with the
action schema `{"action": "look|approach|pickup|add_to_cart|next_station|checkout", "target":
"slot_id|null", "reason": "≤20 words"}`. Two validation layers: `sim/llm_client.py` enforces the
JSON shape and retries; `slow_agent.py` enforces what a schema cannot — that the target exists at
the shopper's *current* station, that a pickup target actually holds a product, and that the
reason is at most 20 words. A failure is re-asked with the reason fed back, capped by
`max_reasks`. The slot list is reshuffled from a seeded RNG **every turn**, because language
models favour whatever is listed first and without it a trace is a ranking of the planogram
file's own ordering; the test asserts the order actually varies. Nothing here feeds the metrics —
the output is evidence a human reads. `data/cache/traces/` is empty until a key exists; run
`python -m sim.slow_agent --all --n 20`.

**The fast path** (`sim/simulator.py`) is what scales the same policies to a population. It is
vectorised over shoppers — it loops over stations and over the two purchase candidates, never
over shoppers. The two attention layers meet here:

```
relevance = 0.5·goal_match + 0.3·brand_affinity + 0.1·(1−price_norm)·price_sensitivity
            + 0.1·promo·promo_sensitivity
gate      = 1.0 where goal_match > 0, else exploration
weight    = p_saliency^exploration × relevance^(1−exploration) × gate
```

At `exploration = 0` a shopper can only look at goal-category slots; at `exploration = 1` the
weights collapse to `p_saliency` exactly — measured worst per-target deviation **0.0037** against
a limit of 0.02, at n = 10,000 and a fixed seed. The same seed gives a byte-identical `SimResult`. Budget: 10,000 shoppers × 4
personas in under 800 ms; the acceptance test prints the measured time on every run and it has
been observed between 142 ms and 252 ms on the development laptops.

The simulator is not a substitute for the agents and the agents are not a substitute for the
simulator. The agents demonstrate autonomous navigation and purchase decisions; the simulator is
how 20 shoppers become 10,000.

---

## 6. Pre-registration protocol

The project's central claim is that each synthetic prediction was fixed **before** the human
shopped. It is enforced structurally at capture time and re-verified from the files afterwards.

**At capture time** (`api/app/prediction.py`, `api/app/routers/sessions.py`, `ws.py`):

- `POST /sessions` calls `write_lock()` *before* it writes the session row, so a session that
  exists always has a lock. The file is written to a temp name and `os.replace`d into place, so a
  lock is either complete or absent — never a half-written document that only looks like evidence.
- `POST /sessions/{id}/events` and `ws/session/{id}` both refuse a session with no lock. No event
  can be recorded ahead of the commitment it will be judged against.
- A lock is never rewritten. Re-registering a session reuses the existing file; re-timestamping a
  commitment after events had been recorded would destroy the thing the lock is evidence of.

**What is hashed.** `sha256` is the SHA-256 of the UTF-8 canonical JSON (`sort_keys=True`,
separators `(",", ":")`) of exactly three fields:

```json
{"population_fixation_prob": …, "sim_run_id": …, "created_at": …}
```

Three, no more. `prediction_id`, `session_id`, `variant_id` and `git_commit` are metadata *about*
the lock, not the prediction. The locked prediction is the expensive one: 10,000 synthetic
shoppers per persona at seed 42.

**What `scripts/eval.py` re-checks**, failing the build and writing no report on any violation:

- a lock exists for every accepted session, and its `session_id`, `variant_id` and
  `prediction_id` agree with the session document;
- `sha256` recomputes — by *calling* `api.app.prediction.compute_sha256`, the production recipe,
  not a second implementation that could quietly bless a tampered file;
- `created_at` strictly precedes the arrival of the session's first event.

That last check needs care and is worth stating precisely, because the obvious version of it is
wrong. Events carry `t_ms`, an offset from the start of the session, **not** a wall clock. So the
first event's arrival is reconstructed as `started_at + min(t_ms)`. The naive check
`created_at <= started_at` would fail on every honest session: the browser stamps `started_at`
and only then calls `POST /sessions`, which simulates 40,000 shoppers before it can write the
lock, so an honest `created_at` is always slightly *later* than `started_at`. What matters — and
what SPEC 4.6 actually asks for — is that no behaviour was recorded before the commitment.

The spectator screen shows the hash prefix and `created_at` in a badge before the shopping
starts, so the ordering is visible on the recording rather than only in a file.

**Current state:** zero locks exist, because zero real sessions exist. `scripts/eval.py` treats
an empty panel as a successful run (exit 0) and reports `Prediction locks found: 0` rather than
implying a verification that did not happen.

---

## 7. Metric definitions

All in `analytics/metrics.py` and `analytics/lift.py`. Both panels go through the same functions;
"the synthetic number and the real number are the same arithmetic" is a property of the code, not
a claim in a README.

| Metric | Definition |
|---|---|
| **Attention Spearman** | Rank correlation between the two per-slot attention vectors over the full slot vocabulary. A slot missing from either mapping counts as 0.0. If either vector is constant (rank correlation undefined) it returns 0.0 rather than propagating a NaN. |
| **Heatmap KL** | `KL(P_real ‖ P_synth)` in nats over the same slot vocabulary, with `eps = 1e-3` added to both vectors before normalising so a zero on either side stays finite. |
| **Purchase-share MAE** | Mean absolute difference in per-SKU purchase share, over a given SKU list (the focal category, or the union of both mappings' keys). Missing SKU ⇒ 0.0 share. |
| **Ad Slot Index Spearman** | The same rank correlation restricted to ad slots only. |
| **Decision agreement** | Would both panels recommend the same variant on the focal KPI? Compare `argmax` over variants on each side; ties broken by sorted variant id so the answer is deterministic. |
| **Ad-to-Purchase Lift** | `(brand share among ad-exposed − among non-exposed) / non-exposed`, per persona plus a population row, for both panels. |

**Ad-to-Purchase Lift deserves its own paragraph**, because it is the headline metric and because
its failure modes are where a careless implementation invents numbers.

*Which exposure.* The simulator uses two scopes by design: bay-local exposure with a brand match
inside the purchase utility (that is how SPEC M4 writes the utility term), and **trip-level**
exposure for the `ad_exposed_purchase_share` / `ad_unexposed_purchase_share` split. Trip-level is
the correct basis for the metric, because it is how a real panel splits — you know whether a
person saw the ad on their trip, not which shelf they were standing at when the utility moved.
`analytics/lift.py` reads the two committed vectors and never re-derives exposure; the real panel
is split the same way (did this session fixate a creative-carrying ad slot at any point).

*What is undefined is reported as undefined.* An empty arm, or an arm that bought nothing, gives
`None`. A zero denominator — the unexposed arm bought things but none of the advertised brand —
gives `None`, never `inf` and never `0.0`. In the emitted block an undefined real value is JSON
`null` and an undefined synthetic value is an **absent key**, because the schema types `synth` as
a plain number and cannot hold null.

*The interval.* `ci95` is a bootstrap over the **real** panel's shoppers, resampled pooled with
each shopper's exposure flag kept and then re-split, so uncertainty in the exposure rate itself is
inside the interval. It attaches to `real` only, and that is a limitation, not a preference: a
`SimResult` carries the two arms as normalised shares, not per-shopper baskets, so there is
nothing synthetic to resample. `bootstrap_lift_ci` is panel-agnostic and will produce the
synthetic interval the moment a caller holds per-shopper synthetic baskets.

---

## 8. The noise ceiling, and why "more accurate than humans" is not a coherent claim

`analytics/noise_ceiling.py`. Procedure, per split: shuffle the accepted sessions, cut them into
two disjoint halves, aggregate each half with `fusion.trimmed_mean` — the same panel estimator the
reported numbers use, not a second aggregation invented for this purpose — and take the attention
Spearman between the two halves. Repeat 200 times; report the mean and the 2.5 / 97.5 percentiles.
Below four sessions it refuses to report: at n = 3 each half holds a single session and the
"ceiling" would be the agreement between two individuals wearing a panel statistic's clothes.

```
relative_agreement = min(1, ρ_synthetic_vs_real / ρ_ceiling)
```

A negative ceiling means "this panel does not repeat", not "the code is broken", and
`relative_agreement` returns 0.0 rather than a ratio.

**Why this is the correct benchmark.** A synthetic-vs-real Spearman of 0.6 means nothing on its
own. If the real panel split in half agrees with *itself* at 0.65, then 0.6 is close to
everything the data can support and the personas are doing well. If it agrees with itself at
0.95, the same 0.6 is a poor model. The ceiling is the scale the accuracy number is printed on.

**Why "more accurate than humans" is not a coherent claim.** It is worth spelling out because it
is the sentence a hackathon pitch reaches for, and it does not survive contact with the
definition.

1. *There is no third thing to be accurate about.* The real panel is not a noisy reading of some
   external ground truth that the synthetic panel might read more cleanly. In this study the real
   panel **is** the target. "The model is more accurate than the target" is a category error: the
   quantity being estimated is defined by the measurement.
2. *`relative_agreement` is capped at 1 for that reason.* Not for modesty — because a ratio above
   1 has no referent. A synthetic panel that correlated with a half-panel better than the other
   half-panel does would have told us something about the split, or about a shared artefact
   between simulator and pipeline, not about shoppers.
3. *Exceeding the ceiling is a warning, not a triumph.* The realistic causes are all bad:
   over-fitted persona shares, a synthetic vector that happens to share structure with the
   pipeline's own biases, or a ceiling depressed by too few sessions. The honest response is to
   investigate, not to put it on a slide.

What *can* be claimed, once a panel exists: the synthetic panel reaches some stated fraction of
the real panel's own repeatability; it recovers the known eye-level effect in the same direction;
it picks the same winning variant; and its prediction was locked and hashed before the shopper
started. Today none of those four can be claimed, because the panel does not exist.

---

## 9. Calibration and holdout protocol

`analytics/calibration.py`. Per PLAN §13, a grid search over the **four persona shares only**,
step 0.05 — never a policy parameter, never a saliency weight. That restraint is the answer to
PLAN §11's "calibration overfits" risk: the model has four degrees of freedom that sum to one.

Objective, minimised: `(1 − attention_spearman) + 5 × purchase_share_mae`. Both terms come from
`analytics/metrics.py`; neither is reimplemented in the calibration module.

**Fit on variant A only.** `calibrate()` takes one variant's real panel and one variant's
per-persona simulation, and the caller passes A. Fitting on B or C would consume the holdout.
The fitted variant id is echoed in the result, and `evaluate()` scores the other variants under
the **frozen** shares without re-fitting. `RESULTS.md` reports fit and holdout separately, always.

**Why the search is fast.** Step 0.05 over four shares summing to 1 is every composition of 20
units into 4 parts: `C(23,3) = 1,771` candidates. Re-simulating each would take about seven
minutes. It is unnecessary because `simulator.combine()` blends every field linearly, so each
persona is simulated once and every candidate is a matrix row plus a Spearman and an MAE. The
equivalence is asserted against the real `combine()` rather than trusted. Fusion, however, is
**not** linear — normalisation is not — so the grid mixes first and fuses after, which is also
the fast order: all 1,771 candidate rows are fused in two array operations. Measured:
**1,771 candidates in 0.79 s.**

**The gating recovery test.** `analytics/tests/test_calibration.py` generates fake "real"
sessions from the simulator at a known mix `[browser 0.5, loyalist 0.2, mission 0.2, switcher 0.1]`
and requires calibration to recover each share within ±0.1 (PLAN S17). Measured on the primary
seed:

```
true mix      [0.5, 0.2, 0.2, 0.1]
recovered     [0.45, 0.2, 0.25, 0.1]
per-share err [0.05, 0.0, 0.05, 0.0]
rho +0.9574  mae 0.00806 obj 0.0829
```

and across four panel seeds the worst per-share error is 0.05, 0.00, 0.05, 0.05.

**What fusing the synthetic side bought, measured.** The same panel, basis, objective and grid,
changing only what the Spearman is taken against — raw `fixation_prob` versus `fuse_synthetic`:

| panel seed | 7 | 11 | 23 | 31 | mean |
|---|---|---|---|---|---|
| raw `fixation_prob` (max per-share error) | 0.100 | 0.150 | 0.100 | 0.200 | **0.1375** |
| fused synthetic | 0.050 | 0.000 | 0.050 | 0.050 | **0.0375** |

The raw comparison misses the ±0.1 bar on half of them; the fused comparison is inside it on
every one. A residual mismatch remains — see §12.

**Current state:** never run on real data. Calibration needs a real panel on the fit variant, so
`RESULTS.md` reports it as not collected and `scripts/eval.py` does not draw the
fit-vs-holdout figure.

---

## 10. Known-effect check

`analytics/known_effect.py`. Variant B moves `SKU_008` from `B1S5P1` (bottom shelf) to `B1S3P2`
(eye level) in bay 1. A shelf-level effect of this size is one of the few things in shopper
research that is not in dispute, so it is the check that catches a pipeline which is internally
consistent and measuring nothing.

The focal slot is looked up in **each variant's own resolved planogram**, so both panels measure
the SKU rather than a fixed shelf position — comparing a fixed slot id would measure the old
shelf's new occupant. Uplift is computed for both panels and a `same_direction` flag is reported.
A zero baseline gives `None`, not `inf`.

Measured, synthetic panel, fused attention (`RESULTS.md`):

| Panel | Attention under A | Attention under B | Uplift |
|---|---|---|---|
| synthetic | 0.0267 | 0.0497 | **+0.86** |
| real | *not yet collected* | *not yet collected* | *not yet collected* |

`same_direction` is therefore undefined: the check has one side.

The same effect through `POST /whatif`, which reports the relative change in the population's
raw `fixation_prob` and `purchase_share` rather than the fused vector (two different quantities —
do not read one as the other), is stable across seeds:

| seed | 7 | 8 | 42 | 99 | 2024 |
|---|---|---|---|---|---|
| focal attention lift | +0.790 | +0.745 | **+0.777** | +0.761 | +0.808 |
| focal purchase-share lift | +1.128 | +1.023 | **+1.147** | +1.107 | +1.042 |

---

## 11. Privacy

- **Gaze is computed in the browser.** `web/src/capture/GazeTracker.ts` calls
  `showVideo(false)`, `showVideoPreview(false)` and `showPredictionPoints(false)` before the
  camera starts, and `saveDataAcrossSessions(false)` — WebGazer's default for that last one is
  *true*, which would persist a face model in the visitor's browser storage.
- **Only `{x, y, conf, t}` leaves the device.** No frame, no crop, no face descriptor is
  transmitted or stored, and the server has no endpoint that would accept one.
- **Sessions are anonymous** and consent is explicit and first: `no_consent` is the first
  rejection reason in the gate order, because a session without consent is not data at all
  whatever else it managed to do. Declining ends the flow. The dev shortcut `?skip_capture=1`
  records `consent: false` — the truth — which makes developer sessions self-rejecting rather
  than quietly admissible.
- **The shopper never sees their own gaze dot.** The gaze trail, the live heatmap and the
  agreement meter live on `#/spectator`, a separate window intended for a second monitor. People
  stare at their own dot and corrupt the data.
- Sessions destined for `data/sessions/anon/` are anonymised before they are committed. That
  script (PLAN S21's `scripts/anonymise_sessions.py`) is **not yet written**; no sessions have
  been collected, so nothing has been anonymised, correctly or otherwise.

---

## 12. Limitations

Ordered roughly by how much they should change your reading of the results.

### 12.1 There is no real panel, so there is no accuracy result

Everything in §§7–9 is machinery. `data/sessions/anon/` is empty; `predictions/` is empty;
every real-vs-synthetic cell in `RESULTS.md` reads *not yet collected*. The webcam pilot (S9) and
the collection round (S21) need people and laptops, and neither has happened. This is the
limitation; the rest are refinements to a comparison nobody has run.

### 12.2 Sample bias, when the panel does exist

The intended panel is colleagues at a hackathon, not shoppers in a store. They are more
technical, more motivated, more likely to know what the study is about, and they shop a browser
at a desk rather than an aisle with a trolley. Effects that depend on physical scale, reach,
crowding or dwell under time pressure will not appear. The target of ≥ 60 accepted sessions
(aim 100) is small enough that the bootstrap intervals will be wide, and the noise ceiling
itself will be estimated from those same few sessions.

### 12.3 Webcam gaze error, and a confidence signal that is derived

- Commodity-webcam gaze regression has an error of several degrees. That is why the fixation
  filter is aggressive (§2.2) and why gaze never carries more than half the fused vector (§3.1).
- **WebGazer supplies no confidence.** `conf` is derived in `GazeTracker.ts`: a numeric
  `confidence` on the prediction wins if one exists, otherwise an `eyeFeatures` object missing
  either eye patch scores 0 and anything else scores 1. With stock WebGazer this is
  **effectively binary**, so `MIN_CONFIDENCE = 0.5` acts as "both eyes were found", not as a
  graded quality filter. Any statement of the form "we filtered low-confidence samples" should be
  read with that in mind.
- Sessions that fail calibration are not discarded but downgraded to `cursor_only`, so the panel
  will be a mixture of two fusion formulas, and the mode split is a reported quantity rather than
  a footnote. On such a panel `eval.py` fuses its single synthetic vector per variant with the
  panel's *dominant* mode, so a minority-mode session's live ρ and its contribution to the
  offline ρ use different synthetic weights (§3.3). Today every session is `cursor_only` and this
  costs nothing; it becomes a real discrepancy the first time both modes appear in one panel.

### 12.4 Residual calibration mismatch, which grows with panel size

Fusing the synthetic side (§9) cut the mean worst-share displacement from 0.1375 to 0.0375. A
mismatch remains, and it is *larger* on big panels, not smaller. At 600 sessions × 400 dwells
the fused comparison lands at 0.150 on three of the seeds. The cause is a genuine asymmetry
between the two interaction channels:

- the **real** channel is a trimmed mean of per-session **max** weights: saturating (a slot
  bought twice in one session still scores 1.0) and truncated (the 10 % trim drops the top
  sessions per slot, which for a sparse channel is most of the non-zero ones);
- the **synthetic** channel is a smooth population purchase share.

At the panel sizes this project will reach (≥ 60), that second-order difference sits well under
sampling noise. It is recorded here because it does not vanish with more data — closing it would
mean changing how the real side aggregates interactions, which is a different change.

### 12.5 The synthetic interaction channel models purchases only

`fuse_synthetic` credits `purchase_share`. The real interaction channel also records `hover` and
`pickup` — items examined and put back. A SKU that is picked up often and bought rarely is
visible to the real panel and invisible to the synthetic one, which will systematically
under-weight considered-but-rejected products.

### 12.6 Ad-to-Purchase Lift is not monotonic in `ad_receptivity` for every persona on this aisle

PLAN S18's acceptance criterion is that raising `ad_receptivity` raises lift monotonically. It
holds strictly on a purpose-built brand-symmetric single-bay store, where the mechanism is
isolated, and on the committed three-bay demo aisle for the **browser** persona. It does **not**
hold on the demo aisle for the mission and loyalist personas, and both causes are understood:

- **Saturation.** The loyalist's `brand_affinity["Crunch"] = 0.95` already clears its purchase
  threshold without any ad pull, so extra receptivity has little left to move.
- **Shared-RNG coupling across bays.** On a three-bay aisle the exposed and unexposed arms differ
  in composition before the ad does anything — who reaches the endcap is not random — and the
  arms are not independent draws.

The tests assert monotonicity where the mechanism is isolated and say in the docstring why it is
not asserted elsewhere. This is a known non-monotonicity, not an unexplained one.

### 12.7 No synthetic confidence interval

`ci95` attaches to the real panel only, for both the noise ceiling and the lift. A committed
`SimResult` carries normalised shares, not per-shopper baskets, so there is nothing to resample.
The synthetic number's Monte Carlo error is a run-size decision, not a sampling interval — but a
reader used to seeing intervals on both sides of a table should know why only one side has them.

### 12.8 Persona policies are hand-written today

See §5. The generator exists and is tested; the committed policies are human-authored. A limitation
in both directions: they are not LLM-designed as the pitch describes, and they were written by
someone who had seen the saliency model.

### 12.9 One category, one aisle, one store

Three bays, five shelves, 24 SKUs, four brands, four categories, two creatives, three ad slots.
Every result is conditional on that planogram. Nothing here says whether the personas transfer to
a different category, a different fixture or a different country.

### 12.10 Vision ingest was dropped

PLAN S20 (phone video → planogram, Grounding DINO on a GPU laptop) was dropped under PLAN §5's own
four-hour CUDA timebox: no aisle clip was recorded, and the available GPU is far below what fp16
Grounding DINO needs. `vision/` contains only a package stub, `web/src/vision/` is empty, and
`data/planograms/video_aisle.json` does not exist. The seed planogram carries the demo, and the
"foundation for AR / spatial" claim rests on the planogram JSON being renderer-agnostic, not on a
working ingest path.

### 12.11 The store shell is procedural

PLAN §9's drop order permits "GLB shell (back to procedural)" and that is what happened.
`data/models/` is empty and nothing in `web/src/store/` loads a GLB. Consequence, stated because
it is a scored criterion and not only an aesthetic one: **the portal's "sample 3D model from
github or huggingface" requirement is not met.**

### 12.12 The CPS / Brand Lift integration is a design plus code, not a result

`docs/integration.md` and `sim/persona_survey.py` (S22) deliver the survey instrument, the
per-persona read-out and the population roll-up, and they describe how census demographics would
seed the persona shares. **No CPS data has been obtained, licensed or used; no Brand Lift study
has been run; no persona has answered the survey.** PLAN §8 lists real CPS data under *not
building*, and nothing here widens that.

### 12.13 The optimizer's ranking is not resolved

S24 (placement optimizer) and S25 (ad slot value) are both built, so PLAN §1's "three outputs,
in increasing value" now describes three built outputs. What follows is what they do *not* show.

Three limits on what the optimizer's ranking and its price tag can be said to show:

* **The order is not settled.** Re-rolling the same 10,000-shopper simulation at seeds 42-46 moves
  the top pick between +6.2% and +14.2% and the current placement between +1.3% and +8.9%. Those
  ranges overlap, so "AD_1 on B1_TALKER at +12.7% beats B3_ENDCAP at +4.5%" is a seed-42 result and
  not a settled ordering. `Ranking.top_pick_is_resolved` is `False` on the committed planogram and
  `summary()` names every candidate the top pick is unresolved against.
* **`SeedSpread` is not a confidence interval,** and PLAN §6's example sentence ("+11% (CI 8-14)")
  is not reproduced. It is Monte Carlo run-to-run variability: the spread of the objective when the
  same simulation is re-rolled at different seeds. A confidence interval would measure sampling
  error, and §12.7 records why a committed `SimResult` cannot support one -- its arms are normalised
  shares and `n_runs` counts shoppers rather than purchase events, so bootstrapping those fields
  returns an interval narrower than the truth. The module, its docstrings and its printed output all
  say which of the two it is.
* **The slot value is mostly assumption by construction.** S25 multiplies the measured lift by a
  baseline unit volume, a margin per unit and a store-week footprint. None of those three exist in
  this repository: `schemas/` and `data/` carry no margin and no traffic, and a `SimResult`'s arms
  are normalised shares. They are required parameters with no defaults, and every printed figure
  carries a `basis` line naming them as assumed. So the *ranking* is the result and the money is
  "what it would be worth if these were your numbers". The value spreads overlap worse than the
  lifts do -- top pick 2,883-6,651 against the current placement's 602-4,150 over seeds 42-46 --
  because the unresolved ordering above propagates straight through the multiplication.
