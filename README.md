# ShopperTwin — AI-Persona Panels

Population-scale shopper behaviour: a browser 3D shelf store where real people shop with webcam gaze tracking, and AI shopper personas shop the same store autonomously. **The persona prediction is locked and hashed before each real shopper starts.** We report how closely synthetic matches real, benchmarked against the real panel's own split-half repeatability — then use the validated personas to recommend where ads and products should sit.

NIQ Innovation Council hackathon · team Media Mavericks.

> Status: skeleton. Modules are built session by session per `docs/PLAN.md`.

## Three outputs, in increasing value

1. **Attention** — Ad Slot Attention Index per persona: what gets seen.
2. **Ad-to-Purchase Lift** — what the exposure was worth. Attention alone is a commodity; this is not.
3. **Placement optimizer** — search all slots and creatives, recommend the best.

## Quick start

```bash
# Windows
make.bat setup
make.bat api        # terminal 1
make.bat web        # terminal 2

# macOS / Linux
make setup
make api            # terminal 1
make web            # terminal 2
```

`make setup` installs Python and Node dependencies, generates the seed planogram, variants, personas and textures, then validates every data file against its schema.

Copy `.env.example` to `.env` and add your LLM key before running persona sessions. Set `LLM_OFFLINE=1` to work entirely from `data/cache/`.

## Repository map

| Path | What lives there |
|---|---|
| `schemas/` | JSON Schema — the only cross-track contract |
| `data/` | Seed planogram, variants A/B/C, personas, LLM caches, anonymised sessions |
| `scripts/` | `make_seed_data.py`, `validate_data.py`, `eval.py` |
| `api/app/` | FastAPI: routers, `resolve.py`, `live.py`, `prediction.py` |
| `sim/` | Saliency, persona policies, Monte Carlo simulator, LLM agents |
| `analytics/` | Fusion, metrics, noise ceiling, calibration, report |
| `vision/` | Video → planogram (GPU laptop; CPU machines replay committed output) |
| `web/src/` | Store, capture, spectator, what-if, dashboard |
| `predictions/` | One lock file per session — these are evidence, they are committed |
| `docs/` | `PLAN.md` (what and when), `SPEC.md` (how), `prompts.md` (session prompts) |

## Documents

- **`CLAUDE.md`** — read automatically by Claude Code every session. Architecture facts and working rules.
- **`docs/PLAN.md`** — three phases, 25 numbered sessions, five parallel tracks, drop order, risks. §13 lists where it overrides the spec.
- **`docs/SPEC.md`** — data contracts, algorithms with parameters, acceptance tests.
- **`docs/prompts.md`** — the exact prompt to paste into Claude Code for each session.
- **`docs/flow-diagram.mermaid`**, **`docs/working-diagram.mermaid`** — paste into a ```mermaid block.

## Seed data

`make seed` builds a 3-bay aisle: 5 shelves per bay, 24 SKUs across 4 brands and 4 categories, 3 ad slots (shelf talker, floor decal, endcap header). Every bay keeps one eye-level position free so a "move to eye level" patch always has a target.

| Variant | What changes | Why |
|---|---|---|
| A | Nothing — baseline | The only variant calibration is fitted on |
| B | SKU_008 moves from bottom shelf to eye level in bay 1 | Known effect both panels must recover |
| C | Ad creative moves from the endcap to the bay 1 shelf talker | Ad placement holdout |

## Commands

| Command | Does |
|---|---|
| `make setup` | Install, seed, validate |
| `make seed` | Regenerate planogram, variants, personas, textures |
| `make validate` | Check every data file against its schema |
| `make gen-types` | Regenerate TypeScript contracts from `schemas/` |
| `make api` / `make web` | Run the API (:8000) and the web app (:5173) |
| `make test` | pytest + vitest |
| `make eval` | Regenerate `RESULTS.md` from committed sessions |

## Privacy

Gaze is computed in the browser. Only `{x, y, confidence, timestamp}` leaves the device — no video frames are transmitted or stored. Sessions are anonymous and consent is explicit. A shopper never sees their own gaze dot: it would change where they look.
