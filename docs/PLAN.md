# ShopperTwin — Development Plan (v4, build-ready)

Commit as `docs/PLAN.md`. Detailed schemas and algorithms are in `docs/SPEC.md`. **§13 lists where this file overrides SPEC — read it before your first session.** This version supersedes all earlier plan files.

---

## 1. The product in one paragraph

A browser 3D shelf store. Real colleagues shop it with webcam gaze tracking; AI shopper personas shop the same store autonomously. The persona prediction is locked and hashed **before** each real shopper starts. We report how closely synthetic matches real, benchmarked against the real panel's own split-half repeatability — then use the validated personas to answer the business question: **which ad placement, shelf position and pack actually change what people buy, and what is each slot worth.**

Three outputs, in increasing value:
1. **Attention** — Ad Slot Attention Index per persona (what gets seen).
2. **Ad-to-Purchase Lift** — what the exposure was worth (headline metric).
3. **Placement optimizer** — search all slots × creatives, recommend the best (the product).

---

## 2. Environment (Day 1, before code)

- Python 3.11, Node 20, git. `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` in `.env` (a cheap fast model — Opus is for building, not for running 10,000 shoppers).
- **Two blocking checks, do these first:** (a) where will the collection link live so ~100 colleagues can reach it — if IT approval is needed, request today; (b) can a company laptop open `getUserMedia` in Chrome — test on 2 machines.
- Download one free store/shelf GLB from Sketchfab or HuggingFace into `data/models/`. This satisfies the portal's stated "sample 3D model from github or huggingface" requirement.

---

## 3. Working rules for Claude Code

- One numbered session below per Claude Code session. Do not merge two.
- Start each session with: *"Read docs/PLAN.md §13 and the session block for S<n>. List the files you'll create, write the tests first, implement, run the tests, paste the output. No TODOs, no placeholders. Do not add dependencies outside SPEC §3."*
- Tests before implementation, always. Make Claude paste the run output — "it should work" is where hackathon repos die on Day 9.
- Schemas are the only cross-track contract. A schema change means a 5-minute team sync and regenerated `types.ts`/`schemas.py` in the same commit.
- Commit after every green test run. `main` must always demo.
- Where you will need to intervene personally: WebGazer calibration behaviour, Three.js projection maths, CUDA install, the < 1 s what-if budget.

---

## 4. Phase 0 — Baseline (Days 1–2, you alone, ~10 h)

Mouse only. No webcam, no LLM, no vision, no Docker.

**S1 — Skeleton and data.** Repo (`web/ api/ sim/ analytics/ schemas/ data/ scripts/`), Makefile (`dev`, `test`), six schemas from SPEC §4 (`planogram`, `variant`, `session`, `event`, `simresult`, `metrics`), generate `types.ts` and `schemas.py`, `scripts/make_textures.py`, `data/planograms/demo_aisle.json` (3 bays × 5 shelves, 24 SKUs, 3 ad slots), variants `A.json` (baseline) and `B.json` (focal SKU → eye level). Test: both validate against the schemas.

**S2 — Saliency and simulator.** `sim/saliency.py` and `sim/simulator.py` per SPEC M3/M4, four hand-written policies in `data/cache/policies/`. Tests: eye > bottom all else equal; adding a creative raises the attached shelf; exploration=0 fixates goal slots only; exploration=1 matches `p_saliency` ±0.02 at N=10,000; **10,000 shoppers × 4 personas < 800 ms**; same seed → identical output.

**S3 — API.** FastAPI + SQLModel/SQLite. `POST/GET /planograms`, `POST /variants`, `GET /variants/{id}/resolved`, `POST /sessions`, `POST /sessions/{id}/events`, `POST /sessions/{id}/finish`, `POST /experiments`, `GET /experiments/{id}`. `resolve()` lives here only. Test: A and B resolve to the expected fixtures.

**S4 — Store.** R3F per SPEC M1: `Bay`, `ProductSlot`, `AdSlot`, `StationController`, `SlotMapper`. ←/→ switches stations. Hover → `cursor_dwell`, click → `pickup` card, `add_to_cart`, `checkout`. `EventLogger` posts every 2 s. Scene loads `GET /variants/{id}/resolved` — no client-side resolve. Test: `hitTest(center)` returns the correct slot for every slot at every station.

**S5 — Metrics and dashboard.** `analytics/fusion.py` (cursor + interaction), `analytics/metrics.py` (Spearman, purchase-share MAE), wire `POST /experiments`, one dashboard page: real vs synthetic attention bars + ρ. Test on hand-computed arrays.

**Baseline done:** shop variant B for 60 s → dashboard shows your attention beside the synthetic prediction with ρ. Tag `v0.1-baseline`. The team starts Day 3 from this tag.

---

## 5. Phase 1 — Five parallel tracks (Days 3–7)

Each track owns its folders; nobody edits another's without a message. Integrate daily 17:00: rebase, `make test`, one end-to-end walk.

### Track A — Store & Spectator (P1)
Owns `web/src/store`, `web/src/spectator`, `web/src/whatif`.

**S6 — Variant C + GLB shell.** Variant C (ad → endcap). Load the GLB store shell as the environment; keep shelves and products procedural so patches still work. Test: the scene renders with the shell and all slots remain hit-testable.

**S7 — Spectator view.** `SpectatorView` subscribes to `ws/spectator/{id}`. `GazeTrail` (dot + 1.5 s fading trail over the station screenshot), `LiveHeatmap` (real attention beside the locked prediction), `AgreementMeter` (grey until `meaningful`), `PredictionBadge` (hash prefix + `created_at`), `ClockOverlay`. **The shopper's own screen shows none of this** — people stare at their own gaze dot and corrupt the data.

**S8 — What-if UI.** Dropdown controls (focal SKU → shelf level; creative → ad slot; promo on/off), `HeatmapDiff` animating 600 ms, `LiftBars` per persona, `elapsed_ms` displayed.

**Done-line:** a colleague shops in one window while a second window shows their heatmap building against the locked prediction.

### Track B — Capture (P2)
Owns `web/src/capture`.

**S9 — Webcam pilot (Day 3, before any code).** Run WebGazer's demo on 5 office laptops. Record: does the camera open, what is the calibration error, how does lighting affect it. This decides whether Track B ships gaze or cursor-only. Report to the team by Day 3 EOD.

**S10 — Capture flow.** Consent → intake (3 questions) → camera check → 9-point calibration → 4-point validation. `mode: cursor_only` when validation error > 12% of screen width — continue the session, don't reject the person. `GazeTracker` wraps WebGazer with `showVideo(false)`, `showPredictionPoints(false)`, never `saveDataAcrossSessions`; only `{x,y,conf,t}` leaves the browser.

**S11 — Fixation filter + gate.** Browser only (server stores what it receives): drop `conf < 0.5` → median filter window 5 → I-DT (dispersion ≤ 60 px, min 100 ms) → centroid → `hitTest`. `CursorTracker` emits `cursor_dwell` after 300 ms in a slot. `SessionGate`: accept iff duration ≥ 45 s, ≥ 2 stations, ≥ 1 interaction, and (webcam) fixation coverage ≥ 0.4; reject reasons enumerated. WS streaming every 500 ms with a local buffer and REST fallback. Test against `fixtures/jittery_gaze.json` → `expected_fixations.json`.

**Done-line:** 20 real webcam sessions accepted through the gate, reject reasons visible.

### Track C — Personas (P3)
Owns `sim/`, `api/app/live.py`, `api/app/prediction.py`, `api/app/routers/whatif.py`.

**S12 — Policy generator.** `sim/llm_client.py` (`complete_json` with schema validation, 3 retries, `LLM_OFFLINE=1` serves cache) and `sim/policy.py` with `prompts/persona_policy.md`, temperature 0, cached to `data/cache/policies/`. Test with a mocked LLM; a policy naming an unknown brand must fail validation.

**S13 — LLM persona agents (Must — never dropped).** `sim/slow_agent.py`: 20 shoppers per persona step through the store via the LLM with action schema `{"action":"look|approach|pickup|add_to_cart|next_station|checkout","target":"slot_id|null","reason":"≤20 words"}`. Slot list order randomised each turn (LLMs favour the first item). Any `target` not in the current station is rejected and re-asked. Reasons cached to `data/cache/traces/`. **This is what satisfies the portal's "personas capable of autonomously navigating and making purchase decisions" — the numpy simulator is the fast path that scales them, not a substitute.** Test: an invalid target is rejected; traces are non-empty for all 4 personas.

**S14 — Prediction lock + live engine.** `prediction.py`: on `POST /sessions`, snapshot the current SimResult for that variant, write `predictions/{session_id}.json` with `sha256` and `created_at`, return the hash prefix to the spectator page. `live.py`: per-batch running fusion (imports `analytics/fusion.py` — one implementation only) → Spearman vs the lock → broadcast on `ws/spectator/{id}`; `meaningful` false until 15 fixations; budget < 20 ms/batch. Ship `ws/spectator` with fake data on Day 3 so Track A isn't blocked. Test: the lock file exists before the first accepted event; live fusion equals offline fusion on a replayed session.

**S15 — What-if endpoint.** `POST /whatif` → resolve → saliency → simulator (warm at startup) → response with `elapsed_ms`. Test: p95 < 1,000 ms over 20 calls.

**Done-line:** a persona's decision trace is readable on screen; what-if returns in < 1 s; every session has a lock predating its first event.

### Track D — Analytics & value metrics (P4)
Owns `analytics/`, `RESULTS.md`, `docs/METHODOLOGY.md`.

**S16 — Fusion and metrics.** Full `fusion.py` (0.5 fixation dwell + 0.3 cursor + 0.2 interaction; cursor-only 0.7/0.3), 10% trimmed mean across sessions, 1,000-sample bootstrap CI. `metrics.py`: Spearman, KL (ε = 1e-3), purchase-share MAE, Ad Slot Index Spearman, decision agreement.

**S17 — Noise ceiling and calibration.** `noise_ceiling.py`: 200 half-splits → mean ρ + 2.5/97.5 percentiles; `relative_agreement = min(1, ρ / ceiling)`. `calibration.py`: **grid search over the 4 persona shares (step 0.05) on variant A only**, objective `(1 − ρ) + 5 × MAE`; freeze; evaluate B and C as holdout; always report fit and holdout separately. **Critical test:** generate fake "real" sessions from the simulator with mix `[0.5, 0.2, 0.2, 0.1]` — calibration must recover each share within ±0.1. This test gates the whole track.

**S18 — Ad-to-Purchase Lift (headline metric).**
`lift = (purchase share of the advertised brand among ad-exposed shoppers − among non-exposed) / non-exposed`, per persona, with bootstrap CI, computed for **both** panels. Attention alone is a commodity — predictive-attention vendors already sell heatmaps. This number is what they cannot produce, because they don't model purchase. Test: a persona with `ad_receptivity = 0` yields lift ≈ 0; raising receptivity raises lift monotonically.

**S19 — Known effect + eval + report.** `known_effect.py` (focal SKU uplift A→B, real vs synthetic, same-direction flag). `scripts/eval.py` loads committed sessions and predictions, **verifies every lock predates its session's first event**, runs everything above, writes `RESULTS.md` and `docs/figures/*.png`. `report.py`: template-based numbers with an LLM-written headline only.

**Done-line:** `eval.py` runs from committed sessions and produces RESULTS.md with every metric.

### Track E — Vision, data ops & submission (P5)
Owns `vision/`, `README.md`, `docs/video/`, `docs/integration.md`, data collection.

**S20 — Vision (Day 3–5, GPU laptop; timebox CUDA to 4 h).** Record a 20 s aisle clip. `extract_frames` (ffmpeg 2 fps) → `detect` (Grounding DINO tiny fp16; prompts: shelf, product package, price tag, promotional sign; threshold 0.35) → `track` (IoU 0.5 dedupe) → `cluster_shelves` (1-D k-means on centre y) → `build_planogram` (emit JSON, `source: video`, per-item confidence, signs → ad slots). Progress over WS; `VisionProgress.tsx` draws boxes per frame and assembles bays. Commit `data/planograms/video_aisle.json` + overlay frames so a CPU machine replays it. **If CUDA eats more than 4 h, drop this and reallocate to data collection.**

**S21 — Data collection (Day 5 onward).** Collection link live, `scripts/anonymise_sessions.py`, daily monitoring of accepted/rejected counts and reject reasons. Target ≥ 60 accepted, aim 100.

**S22 — Integration artifact (Day 6, 2 h).** `docs/integration.md`: how CPS demographics seed persona shares, how Brand Lift questions become a persona post-shop survey. `sim/persona_survey.py`: asks each persona Brand Lift-style questions after shopping. This converts a roadmap bullet into a delivered artifact against the portal's integration criterion.

**S23 — Submission (Day 7–9).** README (idea, architecture diagram, GIF, headline results, how to run, criteria traceability table), `docs/METHODOLOGY.md`, video script and shot list.

**Done-line:** `video_aisle.json` committed, ≥ 60 accepted sessions, integration page written.

---

## 6. Phase 2 — Lock, then value layer (Days 8–10)

| Day | Work |
|---|---|
| **7 EOD** | **Freeze noise parameters** (commit hash → METHODOLOGY). Feature freeze on Tracks A–C. |
| **8 AM** | Calibrate on A, holdout B/C, run `eval.py`, **lock the numbers**. Nothing below starts until this is done. |
| **8 PM** | **S24 — Placement optimizer.** Greedy search over ad slots × creatives (and focal SKU × shelf level), scoring each with 10,000 shoppers. Output ranked recommendations with predicted purchase lift and CI: *"Best placement for AD_1: endcap header bay 3, +11% (CI 8–14). Current placement ranks 6th of 12."* This is the jump from "A/B testing tool" to "recommendation engine" — no attention vendor does it, because they don't model purchase. Test: the optimizer's top pick beats the current placement on the same metric; runtime < 90 s for 12 slots × 2 creatives. |
| **9 AM** | **S25 — Ad slot value (2 h, only if S24 landed).** `slot value/week = predicted incremental units × margin × store-weeks`. Puts a price on inventory before it's sold. |
| **9 PM** | **Record.** Live segments one take, clock visible, retakes fine: video → store · live shop vs locked prediction · what-if · optimizer recommendation · honesty panel. |
| **10** | Final cut, subtitles, repo tidy, tag `v1.0`, submit video + repo link. |

The optimizer sits entirely on top of the existing simulator and needs no new data, which is why it is safe to add after the freeze. **If any track is behind on Day 8, do not start S24.** An unfinished optimizer is worth less than a finished honest result.

---

## 7. Session order (dependency-safe)

S1 → S2 → S3 → S4 → S5 *(baseline, solo)*
then in parallel: **A** S6 → S7 → S8 · **B** S9 → S10 → S11 · **C** S12 → S13 → S14 → S15 · **D** S16 → S17 → S18 → S19 · **E** S20 → S21 → S22 → S23
then S24 → S25.

Cross-track: C ships `ws/spectator` with fake data Day 3 (unblocks A/S7) · D works on simulator-generated fake sessions until Day 6 (unblocks itself) · B ships the gate by Day 4 (unblocks E's collection link Day 5).

---

## 8. Not building

SAM2 · drag-drop editor · Docker Compose · Playwright · dual-language resolve or fixation filter · WebSocket ack protocol · Nelder-Mead calibration · regex number-grounding · real CPS data (design only) · photoreal rendering · auth · mobile.

---

## 9. Drop order if behind on Day 6

what-if animation (keep the number) → vision progress UI (keep the committed planogram) → variant C (keep A/B, state that holdout is unavailable) → GLB shell (back to procedural) → S24 optimizer.
**Never drop:** LLM persona agents (S13), spectator view (S7), prediction lock (S14), noise ceiling (S17), Ad-to-Purchase Lift (S18), `eval.py` (S19).

---

## 10. Effort

| | Hours |
|---|---|
| Phase 0, solo | ~10 |
| Tracks A–E, Days 3–7, 5 people | ~90 combined |
| Phase 2, Days 8–10 | ~35 combined |

Needs ~3 h/person/day. At 1 h/day, drop Track E's vision work entirely and ship with the seed planogram.

---

## 11. Risks

| Risk | Likelihood | Response |
|---|---|---|
| Corporate laptops block the webcam or the link | Medium | Day-1 and Day-3 checks; cursor-only fusion still yields every metric; say "gaze where quality permits, behaviour always" |
| Fewer than 60 accepted sessions | Medium | Wider CIs, lean on known-effect and decision agreement, state n plainly |
| CUDA eats Day 3 | Medium | 4 h timebox, then drop vision |
| Personas behave identically | Low | Inter-persona divergence is a reported metric — Mission must take shorter paths than Browser |
| What-if misses 1 s | Low | N = 5,000 for what-if, 10,000 for locked predictions |
| Calibration overfits | Medium | Persona shares only; report fit and holdout side by side |

---

## 12. Criteria traceability (put this table in the README)

| Portal criterion | Delivered by |
|---|---|
| Browser-based virtual retail store | S4, S6 (GLB shell = their sample-3D-model requirement) |
| Webcam gaze and engagement | S10, S11 (cursor-only fallback disclosed) |
| Dwell, interactions, navigation, purchases | S4, S11, S16 |
| AI personas autonomously navigating and buying | **S13** (LLM agents) + S2 (scale) |
| Identical experiments, both panels | Shared resolved variant JSON; S14 prediction lock |
| Defined accuracy metrics, benchmarked | S16, S17 (noise ceiling), S19 (known effect) |
| Automated actionable insights | S18 lift, S19 report, **S24 optimizer** |
| Reduce time and cost | SPEC §8 table, recomputed Day 8 |
| Roadmap for Brand Lift / CPS | **S22** — a written artifact, not a slide |
| Foundation for AR / spatial / AI shopping | Planogram JSON is renderer-agnostic; S20 video ingest |

---

## 13. Overrides — these beat docs/SPEC.md

| SPEC says | Now |
|---|---|
| `resolve()` in TS and Python with parity test | **Server only.** Web fetches `GET /variants/{id}/resolved` |
| `FixationFilter.ts` + `analytics/noise.py` parity | **Browser only.** Server stores fixations as received |
| Docker Compose, `make demo` | **Cut.** `make dev` + README |
| WebSocket acks, zero-loss test | **Cut.** Plain WS + local buffer + REST fallback |
| Nelder-Mead over 6 params | **Grid search over 4 persona shares** (step 0.05); the 2 global params stay fixed |
| `report.py` regex number-grounding | **Template report; LLM writes the headline only** |
| Slow-mode agents "Should", first to drop | **Must (S13). Track C done-line. Never dropped** |
| Procedural store only | **Free GLB shell** + procedural products |
| CPS/Brand Lift = roadmap slide | **`docs/integration.md` + `sim/persona_survey.py` (S22)** |
| Webcam testing Days 4–6 | **Pilot on 5 laptops Day 3 (S9)** |
| Metrics end at attention and purchase share | **Add Ad-to-Purchase Lift (S18) and the placement optimizer (S24)** |
