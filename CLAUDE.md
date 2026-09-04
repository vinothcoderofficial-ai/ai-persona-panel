# CLAUDE.md — instructions for Claude Code

Read this first, every session. Then read `docs/PLAN.md` §13 (overrides) and the session block for whatever S-number we are building.

## What this project is

ShopperTwin: a browser 3D shelf store where real people shop with webcam gaze tracking, and AI shopper personas shop the same store autonomously. The persona prediction is **locked and hashed before** each real shopper starts. We report how closely synthetic matches real, benchmarked against the real panel's own split-half repeatability, then use the validated personas to recommend ad and shelf placements.

Hackathon deliverable: a recorded demo video plus this repo. 10 working days, 5 people.

## Non-negotiable working rules

1. **Tests before implementation.** Write the test, watch it fail, then implement. Paste the actual test run output — never say "this should work."
2. **One session = one S-number.** Do not start the next module in the same session.
3. **No TODOs, no placeholders, no stubbed returns.** If something can't be finished, say so and stop.
4. **Schemas in `schemas/` are the only cross-track contract.** Changing one means regenerating `web/src/contracts/` and `api/app/schemas.py` in the same commit.
5. **Dependencies are pinned** in `requirements.txt` and `package.json`. Ask before adding anything.
6. **Commit after every green test run.** `main` must always be demoable.

## Architecture facts you must not re-derive

- **`resolve()` lives only in `api/app/resolve.py`.** The web app fetches `GET /variants/{id}/resolved`. Do not write a TypeScript resolver.
- **The fixation filter lives only in the browser** (`web/src/capture/FixationFilter.ts`). The server stores fixations as received. Do not write a Python twin.
- **`analytics/fusion.py` is the single attention formula.** `api/app/live.py` imports it. Never duplicate the maths.
- **Camera is fixed per bay ("shelf stations").** The camera lerps between `bay.station.camera_pos` positions. No free-roam during measurement — webcam gaze is unusable with a moving camera.
- **Two attention layers.** `sim/saliency.py` is deterministic (shelf level, facings, colour contrast, ad adjacency) and answers "what would anyone notice". The persona policy reweights it by goals and brand affinity. An LLM never invents a gaze pattern.
- **Empty shelf positions are real slot objects** with `sku_id: null` and `facings: 0`. This is what makes "move SKU to eye level" possible.
- **The simulator is vectorised numpy.** Loop over stations and fixation steps with boolean masks, never over shoppers. Budget: 10,000 shoppers × 4 personas in under 800 ms.

## Layout

```
schemas/          JSON Schema — the contract. Everything else derives from here.
data/             seed planogram, variants, personas, LLM caches, anonymised sessions
scripts/          make_seed_data.py, validate_data.py, eval.py
api/app/          FastAPI: routers/, resolve.py, live.py, prediction.py, db.py
sim/              saliency.py, policy.py, simulator.py, slow_agent.py, llm_client.py, prompts/
analytics/        fusion.py, metrics.py, noise_ceiling.py, calibration.py, known_effect.py, report.py
vision/           video -> planogram (GPU laptop only; CPU machines replay committed output)
web/src/          store/ capture/ spectator/ whatif/ dashboard/ vision/ contracts/ api/
predictions/      one lock file per session (committed — they are evidence)
docs/             PLAN.md (what/when), SPEC.md (how), prompts.md (session prompts)
```

## Commands

```
make setup      install deps, generate seed data, validate      (Windows: make.bat setup)
make seed       regenerate planogram/variants/personas/textures
make validate   check every data file against its schema
make api        uvicorn on :8000
make web        vite on :5173
make test       pytest + vitest
make eval       regenerate RESULTS.md from committed sessions
```

## Definition of done for any module

- Its acceptance tests in `docs/PLAN.md` pass, output pasted.
- `make validate` still returns 0 errors.
- `make test` is green.
- Nothing outside the module's owned folders was edited.

## Things that will waste your time if you forget them

- WebGazer: call `showVideo(false)` and `showPredictionPoints(false)`; never `saveDataAcrossSessions`. Only `{x,y,conf,t}` leaves the browser — no frames, ever.
- The shopper's own screen must not show their gaze dot. People stare at the dot and corrupt the data. The dot belongs on the spectator view only.
- Calibration validation error above 12% of screen width means switch that session to `mode: "cursor_only"` and carry on. Do not reject the person.
- Randomise the order of the slot list you send to the LLM in `slow_agent.py`. Language models favour whatever is listed first.
- Prediction locks must be written on `POST /sessions`, before any event is accepted. `scripts/eval.py` asserts this ordering and will fail the build if it's violated.
