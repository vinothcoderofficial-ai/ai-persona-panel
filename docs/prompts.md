# Session prompts for Claude Code

One session per prompt. Do not merge two. Every prompt assumes Claude has read `CLAUDE.md` (it does so automatically) and `docs/PLAN.md`.

**Prefix every prompt with this line:**

> Read CLAUDE.md and docs/PLAN.md §13. We are building S<n>. List the files you will create, write the tests first, implement, run the tests, and paste the output. No TODOs or placeholders. Do not add dependencies outside requirements.txt and package.json.

---

## Phase 0 — Baseline (Days 1–2, solo)

**S1 — Skeleton check and type generation**
The repo skeleton, schemas and seed data already exist. Verify `make validate` passes, then generate `web/src/contracts/` TypeScript types from `schemas/` via `npm run gen:types`, and generate `api/app/schemas.py` Pydantic models from the same schemas using datamodel-code-generator. Add `api/tests/test_schemas.py` asserting the seed planogram and all three variants validate against the generated Pydantic models. Paste `make validate` and `pytest` output.

**S2 — Saliency and simulator**
Build `sim/saliency.py` and `sim/simulator.py` per docs/SPEC.md M3 and M4, plus four hand-written policy JSON files in `data/cache/policies/` matching `schemas/policy.schema.json`. Tests in `sim/tests/`: (a) an eye-level slot scores above a bottom slot with all else equal; (b) attaching a creative to an ad slot raises the saliency of slots on the attached shelf; (c) saliency probabilities sum to 1 per bay; (d) with `exploration=0` the simulator only fixates slots in goal categories; (e) with `exploration=1` the fixation distribution matches `p_saliency` within ±0.02 at N=10,000; (f) 10,000 shoppers × 4 personas complete in under 800 ms; (g) the same seed produces an identical SimResult. Empty slots (`sku_id: null`) must be skipped as fixation targets but still occupy shelf space.

**S3 — API**
Build the FastAPI app: `api/app/main.py`, `db.py` (SQLModel on SQLite), `resolve.py`, and routers for planograms, variants, sessions and experiments. Endpoints: `POST/GET /planograms`, `POST /variants`, `GET /variants/{id}/resolved`, `POST /sessions`, `POST /sessions/{id}/events`, `POST /sessions/{id}/finish`, `POST /experiments`, `GET /experiments/{id}`. `resolve()` applies variant patches to a base planogram and returns a full planogram — `move_sku` to an empty slot leaves the source slot empty. Tests: A resolves to the base unchanged; B moves SKU_008 to B1S3P2 and empties B1S5P1; C clears B3_ENDCAP and sets AD_1 on B1_TALKER; every resolved output validates against the planogram schema.

**S4 — 3D store**
Build the R3F store per docs/SPEC.md M1 in `web/src/store/`: `PlanogramScene.tsx`, `Bay.tsx`, `ProductSlot.tsx`, `AdSlot.tsx`, `StationController.tsx`, `SlotMapper.ts`, plus `web/src/main.tsx` and `web/src/api/client.ts`. The scene loads `GET /variants/{id}/resolved` — no client-side resolve. Left/right arrows move the camera between bay stations with a 600 ms lerp. Hover raycast emits `hover`, click opens a 1.5× zoom card and emits `pickup`, an add-to-cart button emits `add_to_cart`, a cart panel emits `remove`, and a checkout button emits `checkout`. `EventLogger.ts` batches events and POSTs every 2 s. Test in `web/tests/`: for every non-empty slot at every station, `SlotMapper.hitTest` at the slot's projected centre returns that slot id.

**S5 — Fusion, metrics, dashboard**
Build `analytics/fusion.py` (cursor dwell + interaction only for now; interaction weight: hover 0.5, pickup 1.0, add_to_cart 1.0, take the max) and `analytics/metrics.py` (Spearman over slots, purchase-share MAE). Wire `POST /experiments` to run the simulator for a variant and compute metrics against a session's fused attention. Build one dashboard page in `web/src/dashboard/Experiment.tsx` showing real vs synthetic attention bars and the Spearman value. Tests on hand-computed arrays with known answers. Then run `make api` and `make web`, shop variant B for 60 seconds, and show me the dashboard result.

**Gate:** tag `v0.1-baseline`. The team starts Day 3 from this tag.

---

## Phase 1 — Parallel tracks (Days 3–7)

### Track A — Store & Spectator

**S6 — Variant C and GLB shell**
Add support for loading a store shell GLB from `data/models/` as the scene environment while shelves, products and ad slots stay procedural. If no GLB is present, fall back to the current procedural room. Verify variant C renders and every slot remains hit-testable with the shell loaded.

**S7 — Spectator view**
Build `web/src/spectator/`: `SpectatorView.tsx` subscribing to `ws/spectator/{session_id}`, `GazeTrail.tsx` (current gaze dot plus a 1.5 s fading trail over the station screenshot), `LiveHeatmap.tsx` (real attention beside the locked prediction heatmap), `AgreementMeter.tsx` (Spearman, greyed until `meaningful` is true), `PredictionBadge.tsx` (sha256 prefix and `created_at`), `ClockOverlay.tsx`. The shopper's own window must show none of this. If Track C's socket isn't ready, develop against a mock message stream.

**S8 — What-if UI**
Build `web/src/whatif/`: `WhatIfControls.tsx` with dropdowns (focal SKU → shelf level, creative → ad slot, promo on/off), `HeatmapDiff.tsx` animating between the previous and new attention over 600 ms, `LiftBars.tsx` showing per-persona lift versus baseline, and a visible `elapsed_ms`. Debounce requests by 300 ms.

### Track B — Capture

**S9 — Webcam pilot (do this on Day 3, before writing capture code)**
Write `web/src/capture/PilotCheck.tsx`: a single page that requests the camera, runs WebGazer's 9-point calibration, measures validation error against 4 held-out points, and prints the error in pixels and as a fraction of screen width. Run it on 5 office laptops and report the numbers to the team.

**S10 — Capture flow**
Build `web/src/capture/`: `Consent.tsx`, `IntakeSurvey.tsx` (3 questions: shopping list, same brand, in a hurry), camera check, `Calibration.tsx` (9-point calibrate, 4-point validate), `GazeTracker.ts` wrapping WebGazer with `showVideo(false)`, `showPredictionPoints(false)`, and never `saveDataAcrossSessions`. Only `{x,y,conf,t}` may leave the browser. Validation error above 12% of screen width sets `mode: "cursor_only"` and the session continues. Map intake answers to an archetype label in this order: has_list && hurry → mission; !has_list && !hurry → browser; same_brand → loyalist; else switcher.

**S11 — Fixation filter, gate, streaming**
Build `web/src/capture/FixationFilter.ts` (drop conf < 0.5 → median filter window 5 → I-DT with 60 px dispersion and 100 ms minimum → centroid through `SlotMapper.hitTest`; fixations inside a shelf but no slot get the shelf id), `CursorTracker.ts` (emit `cursor_dwell` after 300 ms inside a slot), `SessionGate.ts` (accept iff duration ≥ 45 s, ≥ 2 stations visited, ≥ 1 interaction, and for webcam mode fixation coverage ≥ 0.4; reject reasons from the session schema enum), and `SessionSocket.ts` (flush to `ws/session/{id}` every 500 ms, keep a local buffer, fall back to REST if the socket drops). Create `tests/fixtures/jittery_gaze.json` and `expected_fixations.json` and test the filter against them.

### Track C — Personas

**S12 — Policy generator**
Build `sim/llm_client.py` with `complete_json(system, user, schema) -> dict`: reads `LLM_BASE_URL`/`LLM_API_KEY`/`LLM_MODEL`, validates the response against the given JSON schema, retries up to 3 times appending the validation error, and serves from `data/cache/` when `LLM_OFFLINE=1`. Build `sim/policy.py` with `sim/prompts/persona_policy.md`, temperature 0, caching to `data/cache/policies/{persona}_{planogram}.json`. Tests use a mocked LLM: a valid response caches; a response naming a brand absent from the store fails validation; three invalid responses raise.

**S13 — LLM persona agents (Must — never dropped)**
Build `sim/slow_agent.py`: 20 shoppers per persona stepping through the store via the LLM with the action schema `{"action": "look|approach|pickup|add_to_cart|next_station|checkout", "target": "slot_id|null", "reason": "≤20 words"}`. Randomise the order of the visible slot list on every turn. Reject and re-ask any action whose target is not in the current station. Cache reasoning traces to `data/cache/traces/`. Add `sim/prompts/slow_agent.md`. This is what satisfies the portal's requirement for personas that autonomously navigate and make purchase decisions — the numpy simulator scales them, it does not replace them. Tests with a mocked LLM: an out-of-station target is rejected and re-asked; all four personas produce non-empty traces; a trace records at least one purchase decision with a reason.

**S14 — Prediction lock and live engine**
Build `api/app/prediction.py`: on `POST /sessions`, snapshot the current population SimResult for that variant, write `predictions/{session_id}.json` matching `schemas/prediction.schema.json` with a sha256 over the canonical JSON, and return the prediction id and hash prefix. Build `api/app/live.py` and `api/app/routers/ws.py`: `ws/session/{id}` receives event batches and feeds a per-session in-memory `LiveState`; running fusion **imports `analytics/fusion.py`**; compute Spearman against the locked prediction; `meaningful` is false until 15 fixations; broadcast on `ws/spectator/{id}`; budget under 20 ms per batch. Ship the spectator socket with a fake message generator today so Track A is unblocked. Tests: the lock file exists before the first accepted event; replaying a recorded session through the socket yields the same final attention vector as `analytics/fusion.py` computed offline.

**S15 — What-if endpoint**
Build `api/app/routers/whatif.py`: `POST /whatif` takes a base planogram id and a patch list, resolves, recomputes saliency, runs the simulator (policies and simulator warm at app startup) and returns per-persona results, population fixation probabilities, lift versus baseline and `elapsed_ms`. Test: p95 under 1,000 ms over 20 calls at N=10,000.

### Track D — Analytics

**S16 — Full fusion and metrics**
Extend `analytics/fusion.py` to the full formula (0.5 fixation dwell + 0.3 cursor dwell + 0.2 interaction; cursor-only sessions 0.7 cursor + 0.3 interaction; each component normalised to sum to 1 within a session), aggregate across sessions with a 10% trimmed mean and a 1,000-sample bootstrap 95% CI. Extend `analytics/metrics.py` with heatmap KL (ε = 1e-3 smoothing), Ad Slot Index Spearman, and decision agreement (same argmax variant on the focal KPI). Tests on hand-computed arrays.

**S17 — Noise ceiling and calibration**
Build `analytics/noise_ceiling.py` (200 random half-splits of accepted sessions per variant, Spearman between halves, mean and 2.5/97.5 percentiles; `relative_agreement = min(1, spearman / ceiling_mean)`) and `analytics/calibration.py` (grid search over the four persona shares in steps of 0.05, objective `(1 - spearman) + 5 * purchase_share_mae`, fit on variant A only, then evaluate B and C as holdout and report fit and holdout separately). **Gating test:** generate fake "real" sessions from the simulator with a known mix of [0.5, 0.2, 0.2, 0.1] and assert calibration recovers each share within ±0.1. Also: two identical halves give a ceiling of exactly 1.0.

**S18 — Ad-to-Purchase Lift (headline metric)**
Add to `analytics/metrics.py`: `ad_to_purchase_lift = (purchase share of the advertised brand among ad-exposed shoppers − among non-exposed) / non-exposed`, per persona, with a bootstrap 95% CI, computed for both the synthetic panel and the real panel. The simulator must record per-shopper ad exposure and populate `ad_exposed_purchase_share` and `ad_unexposed_purchase_share` in SimResult. Tests: a persona with `ad_receptivity = 0` yields a lift near zero; raising receptivity raises the lift monotonically.

**S19 — Known effect, eval, report**
Build `analytics/known_effect.py` (focal SKU attention uplift from A to B for both panels, with a same-direction flag), `scripts/eval.py` (load `data/sessions/anon/` and `predictions/`, **assert every lock's `created_at` precedes its session's first event timestamp**, run fusion, metrics, noise ceiling, calibration, known effect and lift, then write `RESULTS.md` and figures to `docs/figures/`), and `analytics/report.py` (template-built numbers with an LLM-written headline only). Test: a prediction file dated after its session's first event makes `eval.py` fail.

### Track E — Vision, data ops, submission

**S20 — Video to planogram (GPU laptop; timebox CUDA setup to 4 hours)**
Build `vision/`: `extract_frames.py` (ffmpeg, 2 fps, long edge 1280), `detect.py` (Grounding DINO tiny, fp16 on CUDA, prompts "shelf", "product package", "price tag", "promotional sign", box threshold 0.35, model loaded once at service start), `track.py` (IoU > 0.5 dedupe across consecutive frames, keep the highest-confidence crop), `cluster_shelves.py` (1-D k-means on product box centre y, k = detected shelf count, fallback 5, assign levels by rank), `build_planogram.py` (emit planogram JSON with `source: "video"` and per-item confidence, crops saved as textures, promotional signs become ad slots), and `service.py` (FastAPI on :8100 with `POST /vision/ingest` and a `ws /vision/progress/{job_id}` streaming stage, frame index, boxes and ready bays). Build `web/src/vision/VisionProgress.tsx` (keyframe with boxes on the left, bays assembling on the right) and `SkuRenameTable.tsx`. When `VISION_URL` is empty, replay the committed frames from `data/vision/overlay_frames/` and load `data/planograms/video_aisle.json`, labelled as a replay. Test on a committed 20 s clip: at least 3 shelves and 12 slots above 0.35 confidence, output valid against the planogram schema.

**S21 — Data collection**
Build `scripts/collect_link.py` (serve the capture flow with a variant assigned round-robin) and `scripts/anonymise_sessions.py` (strip anything identifying, write to `data/sessions/anon/`). Add a daily counts report: accepted, rejected, reject reason breakdown, mode split.

**S22 — Brand Lift / CPS integration artifact**
Write `docs/integration.md` describing how CPS demographics would seed persona population shares and how Brand Lift questions become a persona post-shop survey. Build `sim/persona_survey.py`: after a synthetic shopping run, ask each persona a short Brand Lift–style questionnaire (aided awareness, purchase intent, brand association) through the LLM and aggregate by segment. Test with a mocked LLM.

**S23 — Submission package**
Write `README.md` (idea in one paragraph, the two Mermaid diagrams from `docs/`, a GIF of the what-if, headline results from RESULTS.md, how to run, the criteria traceability table from docs/PLAN.md §12, team), `docs/METHODOLOGY.md` (shelf-station rationale, noise pipeline with parameters and the freeze commit, fusion formula, saliency model, persona policies and agents, pre-registration protocol, metric definitions, noise ceiling, calibration and holdout protocol, privacy, limitations), and `docs/video/script.md` plus `shotlist.md`.

---

## Phase 2 — After the numbers are locked (Days 8–10)

**S24 — Placement optimizer (Day 8 PM, only if the Day 8 AM number lock is done)**
Build `sim/optimizer.py`: greedy search over ad slot × creative combinations and focal SKU × shelf level, scoring each candidate with a 10,000-shopper simulation, returning a ranked list with predicted purchase lift and a bootstrap CI, plus the rank of the current placement. Add a dashboard page showing the ranked recommendations. Tests: the top recommendation beats the current placement on the target metric; a full search over 12 slots × 2 creatives completes in under 90 s.

**S25 — Ad slot value (Day 9 AM, only if S24 landed)**
Add `analytics/slot_value.py`: `slot value per week = predicted incremental units × margin × store-weeks`, with margin and store-weeks as configurable inputs. Surface it in the optimizer table. Test on hand-computed inputs.
