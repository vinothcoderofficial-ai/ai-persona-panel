# ShopperTwin — AI-Persona Panels: Population-Scale Shopper Behaviour POC
## Build Plan v2 (hand this file to Claude Opus)

**How to use this file with Opus:** commit it as `docs/PLAN.md` in an empty repo. Start every session with: *"Read docs/PLAN.md fully. We are building module M<n>. List the files you will create, write the tests first, then implement, then run the tests and paste the output. Do not add dependencies outside Section 3. No TODOs or placeholders."* One module per session, in the order in Section 7. Section 10 has the exact kickoff prompts.

**Submission format:** a recorded demo video (assume ≤ 5 min; confirm the portal limit) plus a git repository link. There is no live stage demo. Every "live" moment happens in the recording (retakes allowed) and must also work when a reviewer runs `make demo` on a CPU-only machine.

**Goal:** A browser-based virtual store where real shoppers (webcam + cursor) and a calibrated synthetic shopper panel run identical A/B/C shelf and ad-placement experiments. The synthetic prediction is locked and timestamped *before* each real shopper starts; a spectator screen shows the real heatmap building live against that prediction with an agreement meter; a what-if control re-runs 10,000 synthetic shoppers in about one second; a phone video of a real aisle becomes an editable 3D store; and an honest accuracy report compares both panels against a split-half noise ceiling.

**Architecture:** Editable planogram JSON → procedurally rendered 3D shelf stations (react-three-fiber). Real shoppers stream filtered attention events over a WebSocket to a live engine. Synthetic shoppers come from a hybrid: an LLM writes a decision *policy* per persona once, a numpy Monte Carlo simulator executes 10,000 shoppers in under a second, and an LLM "slow mode" runs 20 agents per persona for explainable traces. A stats layer fuses signals, calibrates on variant A, validates on held-out B and C, and reports agreement relative to the noise ceiling. A vision service (GPU laptop only) turns a phone clip into planogram JSON with streamed progress.

**Tech stack:** Vite + React 18 + TypeScript, @react-three/fiber, three, WebGazer.js, zustand, recharts; FastAPI + uvicorn[standard] (WebSockets) + SQLModel on SQLite; numpy/scipy/pandas; Grounding DINO tiny (transformers) + OpenCV + ffmpeg for vision; any OpenAI-compatible tool-calling LLM behind a thin adapter (cheap fast model for volume, e.g. Claude Haiku or an open HF model; Opus is for building only); Docker Compose; GitHub Actions.

---

## 0. Design decisions (gaps in the original idea → what this plan does instead)

| # | Gap in original proposal | Why it loses | Locked decision |
|---|---|---|---|
| 1 | Free-roam 3D store with webcam gaze | Webcam gaze error is 100–200 px; with a moving camera it is unusable | **Shelf stations**: camera animates between fixed viewpoints, one per bay, each facing the bay as a flat plane. Still full Three.js 3D |
| 2 | Video → Gaussian splat store | Splats are not editable → no A/B variants | **Video → Planogram JSON → procedural store** |
| 3 | Thousands of LLM agents | 70 h and $100+ per run; results drift with temperature | **Hybrid**: LLM writes policy JSON per persona once; numpy simulator runs 10,000 shoppers < 1 s; LLM slow-mode gives 20 traces/persona |
| 4 | LLM "decides what to look at" | No bottom-up attention; heatmap is a guess | **Two-layer attention**: deterministic saliency × persona relevance, blended by an exploration parameter |
| 5 | Real shoppers = 20–40 colleagues | No power, biased | Company-wide link (n ≥ 100), a **known-effect variant** (eye-level move) both panels must recover, **split-half noise ceiling** as benchmark |
| 6 | Calibrate and evaluate on the same data | Overfits | 3 variants: fit on A, holdout B and C, report both |
| 7 | "Higher accuracy than humans" | Incoherent | "Matches the reliability ceiling of real testing at ~1% cost, 100× sample" |
| 8 | "Ad positions" undefined | Nothing to measure | **Ad slots** are first-class planogram objects; output an Ad Slot Attention Index per persona |
| 9 | Synthetic result shown after the fact | Judges assume post-hoc fitting | **Pre-registered prediction**: locked, hashed, timestamped before the shopper starts; visible on screen; verified by `make eval` |
| 10 | Shopper sees their own gaze dot | People stare at the dot; data corrupted | Gaze dot and trail appear only on the **spectator view**; the shopper's screen is clean |
| 11 | Webcam in a data company | Privacy objection | Consent gate, gaze computed in-browser, only (x,y,t,conf) leaves the device, no frames stored, sessions anonymous |
| 12 | Demo depends on GPU, LLM API, network | Fails for reviewers | `make demo` runs CPU-only with cached policies, traces, sessions and the committed vision output. GPU used only when recording |
| 13 | Drag-drop editor, SAM2, post-shop survey, CPS seeding | Cost days, win nothing in 10 days | Cut. What-if uses dropdowns; SKU renaming is a table; CPS/Brand Lift are a roadmap slide |

---

## 1. Scope

**Must:** M1 store + variants · M2 capture + noise · M3 saliency · M4 policy + simulator · M5 analytics · M7 dashboard + report · M9 real-time layer · M8 submission package.
**Should:** M6 video → planogram with streamed progress (recorded on the GPU laptop); slow-mode traces.
**Non-goals:** photoreal rendering, auth, multi-user sessions, mobile browsers, real product catalogues, splat reconstruction, drag-drop editing, SAM2 segmentation, persona surveys, CPS data.

Timeline: 10 working days, 5 people. Data collection Days 5–8.

---

## 2. Architecture

```
  phone clip ──▶ vision service (GPU laptop) ──WS progress──▶ VisionProgress UI
                        │ Planogram JSON (source: video)                │ store assembles bay by bay
                        ▼                                               ▼
              ┌──────────────────────────────┐
              │ Planogram JSON + Variants A/B/C│◀── WhatIfControls (dropdowns)
              └──────────────┬───────────────┘
        ┌────────────────────┼─────────────────────────┐
        ▼                                              ▼
┌─────────────────────┐                     ┌────────────────────────┐
│ M1 store (R3F)      │                     │ M3 saliency            │
│ M2 capture + noise  │──WS events (500ms)─▶│ M4 policy (LLM, cached)│
│  shopper screen     │                     │    simulator (numpy)   │
└──────────┬──────────┘                     └───────────┬────────────┘
           │                                            │ SimResult
           ▼                                            ▼
┌──────────────────────────────────────────────────────────────────────┐
│ M9 live engine: prediction lock (hash+timestamp) → running fusion →  │
│     Spearman vs locked prediction → broadcast to spectator WS        │
│     what-if: resolve → saliency → 10k sim → push (< 1 s)             │
└──────────────────────────────────┬───────────────────────────────────┘
                                   ▼
┌─────────────────────┐   ┌──────────────────────────────────────────┐
│ Spectator view      │   │ M5 analytics (offline): fusion, metrics, │
│ gaze trail, heatmap │   │ calibration, noise ceiling, known effect │
│ vs prediction, meter│   └───────────────────┬──────────────────────┘
└─────────────────────┘                       ▼
                          ┌──────────────────────────────────────────┐
                          │ M7 dashboard + number-grounded report    │
                          │ M8 RESULTS.md, METHODOLOGY.md, video     │
                          └──────────────────────────────────────────┘
```

Both panels consume the **same resolved variant JSON** and the **same station list**.

---

## 3. Repository structure and pinned stack

```
shoppertwin/
  README.md  RESULTS.md  Makefile  docker-compose.yml  .github/workflows/ci.yml
  docs/PLAN.md  docs/METHODOLOGY.md  docs/figures/  docs/video/script.md  docs/video/shotlist.md
  schemas/   planogram variant session event persona policy simresult metrics prediction live whatif vision  (.schema.json)
  data/planograms/demo_aisle.json  data/planograms/video_aisle.json
  data/variants/A.json B.json C.json          # A baseline · B focal SKU to eye level (known effect) · C ad to endcap
  data/personas/mission.json browser.json loyalist.json switcher.json
  data/cache/policies/  data/cache/traces/    # committed LLM outputs
  data/sessions/anon/                         # committed anonymised accepted+rejected sessions for make eval
  data/vision/aisle_clip.mp4  data/vision/overlay_frames/  # committed for CPU replay
  predictions/                                # prediction locks, one file per session (committed for eval)
  web/                                        # Vite + React + TS
    src/contracts/types.ts                    # generated from schemas/
    src/store/PlanogramScene.tsx Bay.tsx ProductSlot.tsx AdSlot.tsx StationController.tsx SlotMapper.ts resolve.ts
    src/capture/Consent.tsx IntakeSurvey.tsx Calibration.tsx GazeTracker.ts FixationFilter.ts CursorTracker.ts
                EventLogger.ts SessionGate.ts SessionSocket.ts
    src/spectator/SpectatorView.tsx GazeTrail.tsx LiveHeatmap.tsx AgreementMeter.tsx PredictionBadge.tsx ClockOverlay.tsx
    src/whatif/WhatIfControls.tsx LiftBars.tsx HeatmapDiff.tsx
    src/vision/VisionProgress.tsx SkuRenameTable.tsx
    src/dashboard/Experiment.tsx Heatmaps.tsx MetricsPanel.tsx AdSlotIndex.tsx Traces.tsx NoiseDashboard.tsx
    src/api/client.ts   tests/ (vitest)
  api/
    app/main.py db.py schemas.py resolve.py live.py prediction.py
    app/routers/planograms.py variants.py sessions.py experiments.py whatif.py reports.py ws.py
    tests/
  sim/  saliency.py policy.py simulator.py llm_client.py slow_agent.py  prompts/persona_policy.md slow_agent.md narrative.md  tests/
  analytics/  fusion.py noise.py metrics.py calibration.py noise_ceiling.py known_effect.py report.py eval.py  tests/
  vision/  service.py extract_frames.py detect.py track.py cluster_shelves.py build_planogram.py  tests/
  scripts/  seed.py run_experiment.py collect_link.py anonymise_sessions.py make_readme_gif.py
```

**Pinned stack (ask before adding anything else):**
- web: react, react-dom, @react-three/fiber, @react-three/drei, three, webgazer, zustand, recharts, vite, vitest, json-schema-to-typescript
- api: fastapi, uvicorn[standard], sqlmodel, pydantic v2, python-multipart, orjson, datamodel-code-generator
- sim/analytics: numpy, scipy, pandas, httpx, jsonschema, matplotlib (figures only)
- vision: torch, transformers (Grounding DINO tiny), opencv-python-headless, ffmpeg-python
- infra: docker compose (services `web`, `api`; optional profile `vision`), GitHub Actions (pytest + vitest)
- LLM adapter `sim/llm_client.py`: `complete_json(system, user, schema) -> dict`; env `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`; validate against schema; retry ≤ 3 with the validation error appended; if `LLM_OFFLINE=1`, serve from `data/cache/`.

**Makefile targets:** `make demo` (compose up, seed, open http://localhost:5173) · `make eval` (regenerates RESULTS.md and docs/figures from data/sessions/anon and predictions/) · `make test` · `make vision-record` (runs vision service natively on the GPU laptop) · `make readme-gif`.

---

## 4. Data contracts (all in `schemas/`, mirrored in `types.ts` and `schemas.py`)

### 4.1 Planogram
```json
{"planogram_id":"demo_aisle","name":"Demo snacks aisle","source":"manual",
 "bays":[{"bay_id":"B1","type":"shelf","width_m":1.2,"height_m":1.8,
   "station":{"camera_pos":[0,1.5,2.2],"look_at":[0,1.1,0]},
   "shelves":[{"shelf_id":"B1S3","height_m":1.45,"level":"eye",
     "slots":[{"slot_id":"B1S3P1","sku_id":"SKU_001","facings":3,"x_m":0.05,"width_m":0.30,"height_m":0.22}]}],
   "ad_slots":[{"ad_slot_id":"B1_TALKER","type":"shelf_talker","attached_to":"B1S3","x_m":0.4,"width_m":0.3,"creative_id":null}]}],
 "skus":[{"sku_id":"SKU_001","name":"Crunch Original 150g","brand":"Crunch","category":"chips","price":45.0,"promo":false,
          "texture_url":"/textures/sku_001.png","color_lab":[62,48,51]}],
 "creatives":[{"creative_id":"AD_1","brand":"Crunch","texture_url":"/textures/ad_1.png"}]}
```
`level` ∈ {top, above_eye, eye, below_eye, bottom}; `type` ∈ {shelf, endcap}; ad `type` ∈ {shelf_talker, endcap_header, floor_decal, screen}; `source` ∈ {manual, video}; video-sourced slots and ad slots carry `"confidence"` in [0,1].

### 4.2 Variant
```json
{"variant_id":"B","base_planogram_id":"demo_aisle","name":"Focal SKU at eye level",
 "patches":[{"op":"move_sku","sku_id":"SKU_007","to_slot_id":"B1S3P2"},
            {"op":"set_ad_creative","ad_slot_id":"B3_ENDCAP","creative_id":"AD_1"},
            {"op":"swap_texture","sku_id":"SKU_007","texture_url":"/textures/sku_007_v2.png"},
            {"op":"set_price","sku_id":"SKU_007","price":39.0,"promo":true}]}
```
`resolve(base, variant)` exists in `api/app/resolve.py` and `web/src/store/resolve.ts`; a shared fixture test asserts byte-identical output for A, B, C.

### 4.3 Session and events
```json
{"session_id":"uuid","variant_id":"B","consent":true,"started_at":"iso","ended_at":"iso",
 "screen_w":1440,"screen_h":900,"mode":"webcam",
 "calibration_error_px":84.2,"intake":{"has_list":true,"same_brand":false,"hurry":true},
 "archetype_label":"mission","prediction_id":"pred_uuid","accepted":true,"reject_reason":null,
 "quality":{"fixation_coverage":0.71,"stations_visited":3,"duration_s":96}}
```
Event: `{"t_ms":12345,"type":"gaze|fixation|cursor_dwell|hover|pickup|add_to_cart|remove|station_enter|station_exit|checkout","station_id":"B1","payload":{}}`.
Payloads: `gaze {x,y,conf}` · `fixation {x,y,dur_ms,slot_id|null,shelf_id|null}` · `cursor_dwell {slot_id,dur_ms}` · `hover|pickup|add_to_cart|remove {sku_id,slot_id}`.
`mode` ∈ {webcam, cursor_only}. Intake → archetype, evaluated in order: `has_list && hurry → mission`; `!has_list && !hurry → browser`; `same_brand → loyalist`; else `switcher`.

### 4.4 Persona and policy
Persona: `{"persona_id":"mission","archetype":"mission","description":"Comes with a list, time-pressed, low exploration, buys first acceptable match.","share_of_population":0.35}`
Policy (LLM output validated against `policy.schema.json`):
```json
{"persona_id":"mission","goal_categories":["chips","cola"],"time_budget_s":{"mean":60,"sd":15},
 "exploration":0.15,"brand_affinity":{"Crunch":0.8,"Zapp":0.4,"_default":0.5},
 "price_sensitivity":0.4,"promo_sensitivity":0.3,"ad_receptivity":0.2,"purchase_threshold":0.45,
 "dwell_ms":{"mu":5.6,"sigma":0.5},"fixations_per_station":{"lam":6}}
```

### 4.5 SimResult (per variant × persona)
```json
{"sim_run_id":"uuid","variant_id":"B","persona_id":"mission","n_runs":2500,"seed":42,
 "fixation_prob":{"B1S3P1":0.041},"dwell_ms_mean":{"B1S3P1":812.0},
 "ad_slot_attention":{"B3_ENDCAP":0.18},"purchase_share":{"SKU_001":0.22},
 "path":{"stations_mean":2.1,"duration_s_mean":58.3},"traces":["..."]}
```

### 4.6 Prediction lock (written before the shopper starts)
```json
{"prediction_id":"uuid","session_id":"uuid","variant_id":"B","sim_run_id":"uuid",
 "created_at":"2026-09-14T10:32:07.412Z",
 "population_fixation_prob":{"B1S3P1":0.038},
 "sha256":"a3f9...","git_commit":"abc1234"}
```
`sha256` = SHA-256 of the canonical JSON of `population_fixation_prob` + `sim_run_id` + `created_at`. The spectator screen shows the first 8 hex chars and `created_at`. `make eval` asserts `created_at < first event timestamp` for every accepted session.

### 4.7 Live update (server → spectator, every batch)
```json
{"session_id":"uuid","t_ms":41200,"n_fixations":37,"stations_visited":2,
 "attention":{"B1S3P1":0.11},"latest_gaze":{"x":812,"y":344},
 "spearman":0.58,"meaningful":true,"prediction_id":"uuid"}
```
`meaningful` is false until `n_fixations ≥ 15`; the meter shows "warming up" before that.

### 4.8 What-if
Request: `{"base_planogram_id":"demo_aisle","patches":[...variant patches...],"n_synth":10000,"seed":42}`
Response: `{"sim_run_id":"uuid","elapsed_ms":640,"per_persona":{"mission":{...SimResult fields...}},
 "population_fixation_prob":{},"lift_vs_baseline":{"focal_sku_attention":0.31,"focal_sku_purchase_share":0.12},
 "ad_slot_attention":{"B3_ENDCAP":0.18}}`

### 4.9 Vision progress (server → UI over WS)
`{"job_id":"uuid","stage":"extract|detect|track|cluster|build|done","frame_idx":17,"n_frames":40,"boxes":[{"label":"product package","xyxy":[..],"conf":0.62}],"bays_ready":["B1"],"elapsed_s":9.8}`

---

## 5. Modules

### M1 — Planogram, variants, procedural store (web + api)
- `Bay.tsx`: box `width_m × height_m × 0.4 m`; shelves as thin boxes at `height_m`; `ProductSlot.tsx` tiles `facings` textured planes; `AdSlot.tsx` textured plane; `PlanogramScene.tsx` places bays 0.3 m apart; ambient + one directional light.
- `StationController.tsx`: camera lerps to `bay.station.camera_pos` in 600 ms; ←/→ keys or on-screen arrows; emits `station_enter/exit`. Optional 8-second free-roam walk-in before station 1 (no events logged during walk-in).
- `SlotMapper.ts`: at each station project every slot and ad slot to screen rectangles via `camera.project`; `hitTest(x,y,pad_px=25) -> {slot_id|ad_slot_id|shelf_id|null}`; recompute on resize/station change.
- Interactions: hover raycast → `hover`; click → 1.5× zoom card with price → `pickup`; "Add to cart" → `add_to_cart`; cart panel → `remove`; "Checkout" → `checkout`.
- API: `POST/GET /planograms`, `POST /variants` → resolved planogram; `GET /variants/{id}/resolved`.
**Acceptance:** `demo_aisle.json` renders ≥ 50 fps on an integrated-GPU laptop; TS and Python `resolve()` byte-identical on A/B/C; vitest iterates every slot at every station and `hitTest(center)` returns it.

### M2 — Real shopper capture and noise pipeline (web + analytics)
**Flow:** Consent → Intake (3 questions) → Camera check → Calibration (9 points) → Validation (4 points) → Shop (min 45 s, ≥ 2 stations) → Checkout → `POST /sessions/{id}/finish`.
**Gaze:** `GazeTracker.ts` wraps WebGazer; `showVideo(false)`, `showPredictionPoints(false)`, never `saveDataAcrossSessions`; keep only `{x,y,conf,t}`. Validation mean error `> 0.12 × screen_w` → `mode: cursor_only`, continue.
**Fixation filter** (`FixationFilter.ts`, mirrored in `analytics/noise.py`): drop `conf < 0.5` → median filter window 5 → I-DT (dispersion ≤ 60 px, min 100 ms) → centroid → `hitTest`; no slot but inside a shelf rect → `shelf_id`.
**Cursor:** `CursorTracker.ts` emits `cursor_dwell` when the cursor stays inside one slot rect ≥ 300 ms.
**Streaming:** `EventLogger.ts` buffers events; `SessionSocket.ts` flushes every 500 ms to `ws://api/ws/session/{id}`; falls back to `POST /sessions/{id}/events` if the socket drops; local buffer is retained until acknowledged.
**Gate** (`SessionGate.ts` + `noise.py`): accept iff `duration_s ≥ 45`, `stations_visited ≥ 2`, ≥ 1 interaction, and (webcam) `fixation_coverage ≥ 0.4`. Reject reasons enumerated: `too_short`, `one_station`, `no_interaction`, `low_coverage`, `no_consent`.
**Noise parameters freeze on Day 7** (before numbers are locked); the freeze commit hash goes in METHODOLOGY.md.
**Acceptance:** `tests/fixtures/jittery_gaze.json` → exactly `expected_fixations.json` in both TS and Python; 30 s session rejected `too_short`; socket drop mid-session loses zero events (test with a mocked socket).

### M3 — Saliency (`sim/saliency.py`)
```
f_level   = {eye:1.0, above_eye:0.75, below_eye:0.7, top:0.5, bottom:0.35}[shelf.level]
f_center  = 1 - 0.4 * |slot_center_x - bay_center_x| / (bay.width_m/2)
f_facings = log(1+facings) / log(1+max_facings_in_bay)
f_color   = mean ΔE(Lab) to left/right neighbours, min-max normalised within bay
f_ad      = 1 if an ad slot with a creative is attached to this shelf or bay else 0
f_size    = slot area / max slot area in bay
saliency_raw = 0.30*f_level + 0.15*f_center + 0.20*f_facings + 0.15*f_color + 0.10*f_ad + 0.10*f_size
p_saliency   = softmax(saliency_raw / 0.15) over slots + ad slots in the bay
```
Ad slot raw saliency: endcap_header 0.6, shelf_talker 0.4, floor_decal 0.3, screen 0.7. Weights live in `DEFAULT_WEIGHTS` and are the only tunable constants.
**Acceptance:** eye > bottom all else equal; adding a creative raises attached shelf saliency; probabilities sum to 1 per bay; runs for all bays in < 5 ms (it is on the what-if hot path).

### M4 — Persona policy (LLM) and simulator (`sim/`)
**Policy** (`policy.py`, `prompts/persona_policy.md`), temperature 0, cached to `data/cache/policies/{persona}_{planogram}.json`:
> System: You convert a shopper archetype into a numeric decision policy. Output only JSON matching the schema. Every scalar is in [0,1] unless the schema says otherwise. Do not invent brands or categories not listed.
> User: Archetype: {description}. Store categories: {categories}. Brands: {brands}. Baseline conversion in this category is {baseline_conv}; set purchase_threshold so a neutral shopper converts near this rate.

**Simulator** (`simulator.py`, vectorised over N shoppers; loop over stations and fixation steps with boolean masks, never over shoppers):
```
time_left ~ N(mean, sd); goals = goal_categories; cart = []
while time_left > 0 and (goals non-empty or archetype == browser):
  station = argmax_unvisited[(1-exploration)*goal_match(bay) + exploration*mean_saliency(bay)] + Gumbel
  k ~ Poisson(lam)
  for k fixations:
    relevance = 0.5*goal_match + 0.3*brand_affinity[brand] + 0.1*(1-price_norm)*price_sensitivity + 0.1*promo*promo_sensitivity
    p(slot) ∝ p_saliency^exploration * relevance^(1-exploration)
    dwell ~ lognormal(mu, sigma); time_left -= dwell/1000; record fixation
  ad_exposure = any fixation on an ad slot in this bay
  for top-2 fixated SKUs matching a goal category:
    u = 0.4*brand_affinity + 0.25*(1-price_norm)*price_sensitivity + 0.15*promo*promo_sensitivity
        + 0.2*ad_exposure*ad_receptivity*(ad.brand == sku.brand) + Gumbel(0, 0.1)
    if u > purchase_threshold: cart += sku; goals -= category; break
  time_left -= 4
aggregate → SimResult; population result = Σ share_of_population × persona result
```
**Slow mode** (`slow_agent.py`): 20 shoppers per persona through the LLM with action schema `{"action":"look|approach|pickup|add_to_cart|next_station|checkout","target":"slot_id|null","reason":"≤20 words"}`; slot list order randomised; any `target` not in the current station is rejected and re-asked; cache to `data/cache/traces/`.
**Acceptance:** exploration=0 never fixates a non-goal slot; exploration=1 matches `p_saliency` ±0.02 at N=10,000; **10,000 shoppers × 4 personas complete in < 800 ms** on a laptop CPU (pytest benchmark, this is the what-if budget); policy validation fails on an unknown brand; same seed → identical SimResult.

### M5 — Analytics (`analytics/`)
**Fusion** (`fusion.py`), per accepted session, per slot: `att = 0.5*fix_dwell_norm + 0.3*cursor_dwell_norm + 0.2*interaction` (hover 0.5, pickup 1.0, add_to_cart 1.0; take max); cursor-only: `0.7*cursor + 0.3*interaction`; each component normalised to sum 1 within the session. Across sessions: 10 % trimmed mean; bootstrap 1,000 → 95 % CI.
**Metrics** (`metrics.py`): Spearman over slots (real trimmed-mean vs population `fixation_prob`); `KL(P_real || P_synth)`, ε = 1e-3; purchase-share MAE over the focal category; Ad Slot Index Spearman; decision agreement = same argmax variant on the focal KPI.
**Noise ceiling** (`noise_ceiling.py`): 200 random half-splits per variant → Spearman between halves → mean, 2.5/97.5 percentiles; `relative_agreement = min(1, spearman / ceiling_mean)`.
**Calibration** (`calibration.py`): free params = 4 persona shares (softmax-parameterised), `exploration_multiplier`, `dwell_multiplier`; objective on **A only**: `(1 - spearman) + 5*purchase_share_mae`; Nelder-Mead, ≤ 300 evaluations; freeze; evaluate B and C; report fit and holdout separately, always.
**Known effect** (`known_effect.py`): `uplift = (att_focal_B − att_focal_A) / att_focal_A` for real and synthetic; `same_direction` flag.
**Eval** (`eval.py`): loads `data/sessions/anon/` and `predictions/`, verifies every prediction predates its session's first event, runs everything above, writes `RESULTS.md` and `docs/figures/*.png`.
**Acceptance (critical):** fake "real" sessions generated by the simulator with mix `[0.5,0.2,0.2,0.1]` → calibration recovers each share within ±0.1; metrics on hand-computed arrays; identical halves → ρ = 1; a prediction file dated after its session fails `eval`.

### M6 — Video → planogram with streamed progress (`vision/`, GPU laptop only)
`service.py` (FastAPI, port 8100): `POST /vision/ingest` (mp4 ≤ 60 s) → `job_id`; `ws /vision/progress/{job_id}` streams 4.9 messages.
1. `extract_frames.py`: ffmpeg 2 fps, long edge 1280 → `stage: extract`.
2. `detect.py`: Grounding DINO tiny, fp16 on CUDA, prompts `["shelf","product package","price tag","promotional sign"]`, box threshold 0.35; one message per frame with boxes → `stage: detect`. Load the model once at service start (warm).
3. `track.py`: IoU > 0.5 dedupe across consecutive frames; keep highest-confidence crop.
4. `cluster_shelves.py`: 1-D k-means on product-box centre y, k = detected shelf count (fallback 5); `level` by rank; bay height 1.8 m, equal spacing.
5. `build_planogram.py`: emit planogram (`source: video`, per-item `confidence`), crops as textures, `promotional sign` → `ad_slots`, SKU names `Unknown 01…`; message per bay → `bays_ready`.
`VisionProgress.tsx`: left pane draws current keyframe + boxes as messages arrive; right pane is the R3F scene adding bays on `bays_ready`; footer shows stage log and `elapsed_s`. `SkuRenameTable.tsx` renames SKUs and fixes levels; save.
If `VISION_URL` is unreachable (reviewers, office machine), the UI plays `data/vision/overlay_frames/` at the recorded cadence and loads `video_aisle.json` — labelled "replay of a recorded run".
Run on the real aisle clip before Day 9 and commit outputs.
**Acceptance:** `tests/fixtures/aisle_clip.mp4` → ≥ 3 shelves, ≥ 12 slots with confidence > 0.35, valid against schema; a 20 s clip completes in < 45 s on the laptop GPU; the replay path works with the service down.

### M7 — Dashboard and report (web + `analytics/report.py`)
Pages: Experiment · Heatmaps (real vs synthetic per variant, over the station screenshot, per-persona toggle) · Metrics (gauges vs noise ceiling, holdout badge) · Ad Slot Index (slot × persona) · Winner (predicted lift per segment with CI) · Traces · Noise Dashboard (accepted/rejected, reasons, calibration-error histogram, mode split).
`report.py` builds a numbers-only JSON; `prompts/narrative.md`: *"Write a headline and 6 bullets for a retail brand manager. Use only numbers present in the input JSON. If a number is missing, do not mention it."* Post-check extracts every number in the narrative and rejects any not present in the input; regenerate once, then fall back to a template.
**Acceptance:** a narrative with a foreign number is rejected by the test; cost/time table renders from Section 8.

### M8 — Submission package (repo + video)
**README.md** (in this order): one-paragraph idea · architecture diagram (from Section 2) · 15-second GIF of the what-if (`make readme-gif`) · `make demo` / `make eval` / `make test` · headline results table (copied from RESULTS.md) · link to METHODOLOGY and the demo video · success-criteria traceability table (Section 9) · team.
**RESULTS.md** (generated): per-variant metrics table, noise ceiling with CI, relative agreement, known-effect uplifts, decision agreement, `n_real_accepted / rejected`, `n_synth`, figures: real vs synthetic heatmaps (A/B/C), agreement vs ceiling bar, calibration fit-vs-holdout, reject-reason histogram.
**METHODOLOGY.md**: shelf-station rationale · noise pipeline with parameters and the freeze commit · attention fusion · saliency model · persona policy + simulator · pre-registration protocol · metrics definitions · noise ceiling · calibration/holdout protocol · known-effect check · privacy · limitations (sample bias, webcam error, persona policies from an LLM).
**CI:** `.github/workflows/ci.yml` runs `pytest` and `vitest` on push; badge in README.
**Video** (`docs/video/script.md`, `shotlist.md`): OBS 1920×1080 30 fps, laptop mic or headset, `ClockOverlay` visible in live segments, subtitles `.srt`, export H.264 MP4. Shot list with timings:

| t | Shot | Live? |
|---|---|---|
| 0:00–0:20 | Problem: $100K physical tests, weeks, say vs do | slides |
| 0:20–0:40 | Pipeline diagram | slides |
| 0:40–1:40 | Phone clip → `VisionProgress`: boxes appear per frame, store assembles, timer visible | **live, one take** |
| 1:40–2:55 | Colleague shops on a clean screen (picture-in-picture); spectator view: prediction badge with hash + timestamp appears first, then gaze trail builds heatmap, agreement meter climbs | **live, one take** |
| 2:55–3:35 | What-if: move the ad to the endcap → 10,000 re-run, `elapsed_ms` on screen, per-persona lift animates | **live** |
| 3:35–4:15 | Honesty panel: Noise Dashboard, noise ceiling, fit vs holdout, known-effect | dashboard |
| 4:15–4:45 | Cost/time table; Brand Lift / CPS roadmap; repo QR/URL | slides |

Rules: live segments are never edited internally; a visible clock runs throughout; say "processed live on a laptop GPU" and "prediction locked at 10:32:07, shopping began 10:32:41". Retake until clean.
**Acceptance:** `make demo` verified on the CPU office machine from a fresh clone in < 5 min; `make eval` reproduces RESULTS.md byte-identically from committed data; CI green; video ≤ portal limit.

### M9 — Real-time layer (api + web)
**`api/app/routers/ws.py`:** `ws/session/{id}` receives event batches, acks each batch id, appends to the DB every 2 s, and feeds `live.py`. `ws/spectator/{id}` broadcasts 4.7 messages. In-memory `LiveState` per session; no DB reads on the hot path.
**`api/app/live.py`:** on each batch: update per-slot fixation/cursor/interaction accumulators → fused attention (same formula as `fusion.py`, imported, not duplicated) → Spearman vs locked `population_fixation_prob` → `meaningful = n_fixations ≥ 15` → broadcast. Budget: < 20 ms per batch.
**`api/app/prediction.py`:** on `POST /sessions`: load the current `sim_run_id` for the variant (cached SimResults, refreshed when policies or planogram change), write the 4.6 lock file to `predictions/`, return `prediction_id` and the hash prefix to the shopper page (hidden) and the spectator page (visible).
**`api/app/routers/whatif.py`:** `POST /whatif` → `resolve` → `saliency` → `simulator.run(policies, n=10000, seed)` → response with `elapsed_ms`; the simulator and policies are loaded at startup (warm). Target p95 < 1,000 ms end-to-end on a laptop CPU; debounce 300 ms in the UI.
**`web/src/spectator/`:** `SpectatorView.tsx` subscribes to `ws/spectator/{id}`; `GazeTrail.tsx` draws the latest gaze as a dot with a 1.5 s fading trail over the station screenshot; `LiveHeatmap.tsx` renders `attention` as a per-slot heat overlay beside the prediction heatmap; `AgreementMeter.tsx` shows ρ (grey while not meaningful) and relative-to-ceiling; `PredictionBadge.tsx` shows hash prefix + `created_at`; `ClockOverlay.tsx` shows wall-clock time. Opened on a second window or second monitor during recording; the shopper window has none of this.
**`web/src/whatif/`:** `WhatIfControls.tsx` (focal SKU → shelf level; creative → ad slot; promo on/off), `HeatmapDiff.tsx` animates from previous to new attention over 600 ms, `LiftBars.tsx` per-persona lift vs baseline.
**Acceptance:** replaying a recorded session file through `ws/session` produces the same final attention vector as `fusion.py` offline (parity test); spectator receives ≥ 1 message per 500 ms during replay; `POST /whatif` p95 < 1,000 ms over 20 calls in the test; a lock file is created before the first event is accepted (test asserts ordering); dropping the socket for 3 s loses no events.

---

## 6. Build schedule (10 working days, 5 people)

| Day | P1 web store + spectator | P2 capture + streaming | P3 sim + LLM | P4 analytics + eval | P5 vision + submission |
|---|---|---|---|---|---|
| 1 | Repo, schemas, codegen, seed planogram, CI, compose skeleton, Makefile (all) ||||
| 2–3 | M1 Bay/Slot/Station/hitTest, resolve | M2 consent, intake, calibration, GazeTracker, FixationFilter | M3 saliency; M4 policy + adapter (mock LLM) | M5 metrics, noise ceiling on synthetic fixtures | M6 extract + detect on GPU laptop; record aisle clip |
| 4 | Cart, checkout, variant resolve API | CursorTracker, gate, EventLogger, SessionSocket | M4 simulator vectorised, < 800 ms | Fusion, calibration + recovery test | M6 track, cluster, build_planogram |
| 5 | **End-to-end: one real session → metrics JSON. Company link live via `collect_link.py` (all)** ||||
| 6 | Spectator view + PredictionBadge + Clock | ws/session router + acks | Slow-mode agents, trace cache | live.py parity test, prediction.py | M6 service + progress WS; VisionProgress UI |
| 7 | What-if controls, HeatmapDiff, LiftBars | **Freeze noise params (commit hash → METHODOLOGY)** | whatif router warm path, p95 test | known_effect, report.py + grounding check | Replay path for CPU; SkuRenameTable |
| 8 | Dashboard pages | Noise Dashboard | Persona traces UI | **Lock numbers: calibrate A, holdout B/C, `make eval` → RESULTS.md, figures** | METHODOLOGY.md, video script + shot list |
| 9 | README, GIF | `make demo` on the CPU office machine from fresh clone | Fix anything eval surfaced | RESULTS review | **Record video (all live segments), retakes** |
| 10 | Final cut, subtitles, repo tidy, tag `v1.0`, submit (all) ||||

Gates: Day 3 store renders A/B/C · Day 5 end-to-end · Day 7 params frozen · Day 8 numbers locked · Day 9 `make demo` verified on CPU.

---

## 7. Module order for Opus sessions
M1 → M3 → M4 (policy) → M2 → M4 (simulator) → M5 → M9 → M7 → M6 → M8. Pure-Python modules (M3, M4, M5) run in parallel with M1/M2 by different people.

---

## 8. Numbers for the pitch (recompute from your data on Day 8)

| | Physical test store | Survey | Webcam-only virtual | ShopperTwin |
|---|---|---|---|---|
| Cost / study | $100K+ (from the proposal) | $10–30K (indicative) | ~$5K recruiting (indicative) | < $100 compute after calibration |
| Time | Weeks–months | 2–4 weeks | Days | Minutes for a what-if; hours for a calibrated study |
| n | 50–200 | 500–1,000 | 30–100 | 10,000 |
| 95 % CI on a 30 % conversion | ±13 pp at n=50 | — | ±14 pp at n=40 | ±0.9 pp at n=10,000 |
| Measures | Do | Say | Do (noisy) | Do, calibrated to real |

Headline: *"The synthetic panel reaches {relative_agreement}% of the real panel's own repeatability, recovers the known eye-level effect, picks the same winning variant — and its prediction was locked and hashed before the shopper started."*

---

## 9. Portal success criteria → deliverable

| Criterion | Delivered by |
|---|---|
| Functional browser-based virtual store | M1 |
| Webcam-based gaze and engagement | M2 (cursor-only fallback disclosed) |
| Dwell time, interactions, navigation, purchases | M2 events, M5 fusion |
| AI personas autonomously navigating and buying | M4 simulator + slow-mode agents |
| Identical experiments real vs AI | Shared resolved variant JSON + station list; pre-registered prediction (M9) |
| Benchmark similarity with defined accuracy metrics | M5: Spearman, KL, MAE, decision agreement, noise ceiling, known effect |
| Actionable insights automatically | M7 report with number-grounding check; live what-if |
| Reduce time and cost of physical studies | Section 8 |
| Roadmap for Brand Lift, CPS | README/video roadmap: CPS demographics seed persona shares; Brand Lift questions become a persona post-shop survey |
| Foundation for AR / spatial / AI shopping | Planogram JSON is renderer-agnostic; video ingest path (M6) |

---

## 10. Kickoff prompts for Opus (one per session)

- **M1:** "Read docs/PLAN.md. Build M1. Start with `schemas/planogram.schema.json` and `variant.schema.json`, generate `types.ts` and `schemas.py`, write the `resolve()` fixture test in both languages, then Bay/ProductSlot/AdSlot/StationController/SlotMapper with the hitTest vitest that iterates all slots. Paste `npm test` and `pytest` output."
- **M3:** "Build `sim/saliency.py` per Section 5 M3, acceptance tests first, including the < 5 ms timing test."
- **M4-policy:** "Build `sim/llm_client.py` (`complete_json`, schema validation, 3 retries, `LLM_OFFLINE` cache mode) and `sim/policy.py` with `prompts/persona_policy.md`. Mock the LLM in tests. Add the unknown-brand validation test."
- **M2:** "Build M2. `FixationFilter.ts` and `analytics/noise.py` first against the shared fixtures; they must produce identical fixations. Then Consent, Intake, Calibration, GazeTracker, CursorTracker, SessionGate, EventLogger, SessionSocket with the zero-loss-on-disconnect test."
- **M4-sim:** "Build `sim/simulator.py` vectorised per the pseudocode. Tests: exploration=0 goal-only; exploration=1 matches p_saliency ±0.02 at N=10,000; 10,000 × 4 personas < 800 ms; same seed → identical output."
- **M5:** "Build fusion, metrics, noise_ceiling, calibration, known_effect, eval. The calibration recovery test must pass first. `eval.py` must fail on a prediction dated after its session."
- **M9:** "Build `ws.py`, `live.py`, `prediction.py`, `whatif.py` and the spectator/whatif components. Parity test: a replayed session through the socket equals `fusion.py` offline. `POST /whatif` p95 < 1,000 ms. Lock file precedes the first accepted event."
- **M7:** "Build the dashboard pages and `report.py` with the number-grounding post-check and its test."
- **M6:** "Build `vision/` per Section 5 M6 with the progress WebSocket and the CPU replay path. Test on `tests/fixtures/aisle_clip.mp4`."
- **M8:** "Write README.md, docs/METHODOLOGY.md, the Makefile targets, CI workflow, `make readme-gif`, and the video script/shot list. Then run `make demo` from a fresh clone and paste the output."

**Standing rules for Opus:** tests before implementation; no TODO/TBD; contracts change only via `schemas/` plus regenerated types; the fusion formula exists once (`analytics/fusion.py`) and is imported by `live.py`; run the full suite before declaring a module done; commit after every green run.

---

## 11. Risks and fallbacks

| Risk | Trigger | Fallback |
|---|---|---|
| Real n stays below 60 by Day 8 | count on Day 7 | Extend the link one more day, widen CIs, lean on known-effect + decision agreement, state it plainly in RESULTS |
| Webcam validation fails for many colleagues | reject rate > 40 % on Day 6 | Loosen the validation threshold *before* the Day 7 freeze; cursor-only sessions still count |
| What-if slower than 1 s | p95 test fails | N = 5,000 for what-if (10,000 for locked predictions); profile the fixation loop |
| Vision detection poor on the real clip | < 3 shelves | Re-shoot with slower pan and better light; lower threshold to 0.30; committed `video_aisle.json` is the reviewer path anyway |
| LLM API unavailable | 5xx/429 | `LLM_OFFLINE=1` serves cached policies and traces |
| `make demo` breaks on the office machine | Day 9 test | Pin Docker images and Node/Python versions; the office machine test is a gate, not optional |
| Calibration overfits | holdout ρ ≪ fit ρ | Reduce free params to persona shares only; report both numbers |
| Recording day slips | Day 9 | Live segments are recorded independently and can be re-shot on Day 10 morning; slides are pre-rendered |
