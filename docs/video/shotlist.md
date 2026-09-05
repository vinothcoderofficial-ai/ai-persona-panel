# Demo video — shot list

Companion to [`script.md`](script.md), which carries the narration. This file is what the person
at the keyboard follows.

**Status: not recorded.** Nothing in `docs/video/` is a recording; there is no MP4, no SRT and no
GIF in this repository. What follows is the plan.

## Technical setup

| | |
|---|---|
| Capture | OBS Studio, 1920 × 1080, 30 fps, H.264 MP4 |
| Audio | Laptop mic or headset, one pass, no music under speech |
| Subtitles | `.srt` alongside the MP4 |
| Browsers | Two windows on two monitors: **shopper** (1920 × 1080, nothing else on screen) and **spectator** |
| Servers | `make api` and `make web` running before recording starts; `make seed` already run so textures exist |
| Clock | `ClockOverlay` is visible on the spectator window for every live segment. Do not crop it out. |

**Rules for the live segments.** No internal edits — a live segment is one continuous take from
first frame to last. Retakes are free; cuts inside a take are not. The wall clock runs throughout,
which is what makes "the prediction was locked at 10:32:07 and shopping began at 10:32:41" a
checkable statement rather than a claim.

## What changed from SPEC M8's shot list, and why

SPEC M8 specifies seven shots. Two of them cannot be recorded honestly:

| SPEC shot | Status | Replaced with |
|---|---|---|
| `0:40–1:40` Phone clip → `VisionProgress`, boxes per frame, store assembling | **Cannot record.** S20 was dropped under PLAN §5's four-hour CUDA timebox. No aisle clip was shot; `vision/` is a stub and `web/src/vision/` is empty. | Shot 3: the seed planogram becoming the 3D store, live. |
| `1:40–2:55` Colleague shops with webcam gaze; agreement meter climbs | **Partly.** There is no collected panel and the S9 webcam pilot never ran, so a webcam take cannot be promised. | Shot 4 as written below: a real, consented, **cursor-only** session. The agreement meter does turn on — it counts cursor dwells rather than fixations — but say on camera that this is agreement against a cursor-only proxy, not gaze. |
| PLAN §6's optimizer recommendation | **Recordable.** S24 landed; the ranking runs in ~6 s. | Kept, but narrated as a search rather than a recommendation. The default ranking reorders with run size, so the on-screen order is stated as unsettled; the one claim that does settle (at 250k) is a SKU move, not an ad move, and is said aloud rather than shown. |

Everything else is recorded as specified.

## Shot list

| # | t | Shot | Live? | Screen |
|---|---|---|---|---|
| 1 | 0:00–0:20 | Problem: physical test stores are slow and expensive; surveys measure what people *say*; attention vendors sell heatmaps and stop before purchase | slides | — |
| 2 | 0:20–0:45 | Pipeline diagram, and the honesty statement: one panel exists, one does not | slides | `README.md` architecture diagram |
| 3 | 0:45–1:25 | **The store.** Seed planogram JSON on the left, the rendered aisle on the right. Arrow-key between the three shelf stations; hover a pack, pick it up, add to cart. Point at the empty eye-level slot. | **live, one take** | shopper window |
| 4 | 1:25–2:35 | **Live session against a locked prediction.** Consent → intake → "Continue without the camera" → shop. Spectator window shows the prediction badge (hash prefix + `created_at`) *before* the first event, then the heatmap building beside the locked prediction. | **live, one take** | both windows, spectator in picture-in-picture |
| 5 | 2:35–3:15 | **What-if.** `#/whatif`, move `SKU_008` from the bottom shelf to eye level. `elapsed_ms` on screen; per-persona lift bars animate. Change the creative's ad slot and re-run. | **live** | shopper window at `#/whatif` |
| 6 | 3:15–3:45 | **The recommendation.** `python scripts/optimize.py --creative AD_1 --focal-sku SKU_008` in the terminal. The ranking prints; point at the current placement sitting 5th of 13. Then re-run with the commercial flags to show the priced table, and say out loud that the money column is assumed and the lift column is not. | **live** | terminal |
| 7 | 3:45–4:25 | **Honesty panel.** `RESULTS.md` scrolled in the terminal: the `not yet collected` rows, the zero lock count, the figures `eval.py` refused to draw. Then the noise-ceiling explanation and the known-effect table. | terminal + slides | — |
| 8 | 4:25–4:55 | Cost and time comparison (labelled indicative); Brand Lift / CPS roadmap; the list of what is not built; repo URL | slides | — |

Total **4:55**, inside SPEC's assumed 5-minute limit.

## Per-shot setup

### Shot 3 — the store (0:45–1:25)

```
make api        # terminal 1
make web        # terminal 2
```

Open `http://localhost:5173/?skip_capture=1&variant=A` in the shopper window. Split-screen with
`data/planograms/demo_aisle.json` in an editor.

- `?skip_capture=1` jumps straight to the store. It records `consent: false` — the truth, since
  nobody sat down and agreed to anything — which makes the session self-rejecting at the gate.
  **Say this on camera.** It is a developer shortcut, not a shopper.
- Arrow keys ←/→ move between bays. The camera lerps for 600 ms and then rests. There is no free
  roam anywhere, and shot 4's narration explains why.
- Hover a product for the `cursor_dwell`; click it for the pickup card; add to cart.
- Point at `B1S3P2`: an empty eye-level position, a real slot object with `sku_id: null`. That is
  the target variant B moves `SKU_008` into.

### Shot 4 — live session (1:25–2:35)

Restart the shopper window at `http://localhost:5173/?variant=A` — **no** `skip_capture`, so the
real capture flow runs and the session is consented. Variant A keeps the aisle in the state shot 3
showed and shot 5 will change, so the three live shots tell one story: this is the shelf, this is
a person shopping it, this is what happens if we move something.

1. Consent screen → accept.
2. Intake, three questions.
3. Camera check → **"Continue without the camera"**. This sets `mode: "cursor_only"` with
   `consent: true`: a real, gate-eligible session.
4. The store opens. `POST /sessions` has already written `predictions/{session_id}.json`.
5. Get the session id and open the spectator window on the second monitor at
   `http://localhost:5173/#/spectator?session=<id>`. The id is generated in the browser and is
   **not** in the URL and not logged to the console, so read it from one of these — decide which
   before you start recording, and have the command ready:
   - the newest file in `predictions/` (`ls -t predictions | head -1` → `{session_id}.json`), or
   - the `POST /sessions` request in the browser's DevTools Network tab.

   A spectator joining mid-session is sent the current snapshot on its first frame, so the badge
   and the heatmap populate immediately — you do not have to have it open from the first event.
6. Shop for at least 45 seconds across at least two stations with at least one interaction, or the
   session gate will reject it — and say so as you do it.
7. **Move the cursor from pack to pack, deliberately.** The agreement meter needs fifteen cursor
   dwells, and a dwell is not elapsed time on the shelf. `CursorTracker` opens one when the pointer
   enters a *product* rectangle and emits it only when the pointer **leaves** that rectangle having
   been inside it for 300 ms or more. Fifteen dwells is therefore fifteen separate packs touched by
   the cursor; parking on one pack for the whole take produces exactly one, and the meter would stay
   grey through a session that otherwise passes the gate. Shelf space between packs, the ad panels
   and the HUD carry no `slot_id`, so time over them counts for nothing.

**What the spectator window will and will not show, in a cursor-only session:**

| Element | Behaviour |
|---|---|
| `PredictionBadge` | Hash prefix and `created_at`, populated on the first frame. This is the shot's whole point. |
| `ClockOverlay` | Running wall clock. |
| `LiveHeatmap` | Builds from cursor dwell and interactions. Works. |
| `GazeTrail` | **Stays empty.** Only `gaze` and `fixation` events carry a screen position and a cursor-only session emits neither. |
| `AgreementMeter` | **Turns on mid-shot.** `meaningful` counts 15 events of the session's own evidence channel, which in a cursor-only session is cursor dwells, not fixations. The label counts up ("9 of 15 cursor dwells") and ρ appears once it reaches 15 — so cross it on camera, which is a matter of how many packs you hover, not how long you shop (step 7). |

Do not hide the empty gaze trail, and do not let the meter coming on pass without saying what it
counted. Narrate both (see `script.md`).

**The `?fake=1` cutaway is cut from this shot.** It existed for one reason — the meter stayed grey
for the whole of a cursor-only take, so a stream built to fake one was the only way to show a live
meter — and that reason is gone. Shot 4 now ends when the live take ends. If a take finishes short
of fifteen dwells, retake it: retakes are free, an internal edit is not, and splicing a frame
labelled "fake" into the one shot whose whole argument is *this is real and it was locked first*
costs more than it pays.

Shot 4 keeps its 1:25–2:35 budget. The ten seconds the beat used to take returns to the live take,
which needs them: consent, three intake questions, the camera check and 45 seconds of shopping do
not sit comfortably in seventy.

The fake stream still exists as a route, and the rule governing it is unchanged — see
[Things that will ruin a take](#things-that-will-ruin-a-take).

**If a laptop's webcam calibration passes on the day**, prefer the webcam variant of this shot: at
the camera check choose "Turn the camera on", complete the 9-point calibration and the 4-point
validation, and if the validation error is at or under 12 % of screen width the session runs in
`webcam` mode. Two things change, and whether the meter turns on is not one of them — it turns on
in either mode. The gaze trail draws instead of staying empty, and the evidence channel becomes
fixations, so the meter reads "9 of 15 fixations" and the cursor-proxy caveat drops out of the
narration. A fixation needs 100 ms against a dwell's 300 ms, so the threshold arrives sooner and
step 7's deliberate hovering stops mattering. Do not plan on it: the S9 pilot was never run and no
webcam session has ever been recorded.

### Shot 5 — what-if (2:35–3:15)

`http://localhost:5173/#/whatif`.

1. Focal SKU `SKU_008`, shelf level → **eye**. Read `elapsed_ms` aloud from the screen.
2. Let `HeatmapDiff` finish its 600 ms animation and let `LiftBars` settle.
3. Second change: move the creative from the bay-3 endcap to the bay-1 shelf talker (variant C's
   patch) and re-run.

Reference values, so a wrong number is noticeable during the take: at seed 42 the eye-level move
reports about **+0.78 focal attention** and **+1.15 focal purchase share** relative to baseline,
and the same move sits between +0.75 and +0.81 across seeds 7 / 8 / 42 / 99 / 2024. `elapsed_ms`
should be single- to low-double-digit milliseconds warm; the first call after startup is slower.

### Shot 6 — the recommendation (3:15–3:45)

The one shot SPEC M8 asked for that could not be recorded until S24 and S25 landed. Two commands,
both in the terminal, both fast enough to run live (~6 s each).

```
python scripts/optimize.py --creative AD_1 --focal-sku SKU_008
```

Thirteen configurations ranked by ad-to-purchase lift, in about six seconds. Point at two things
and nothing else:

* the current placement sits **5th of 13** at this run size, and
* the line that begins `The order is not resolved against`, followed by the line saying **more
  seeds will not settle it**.

Say the second one on camera. It is the difference between a recommendation engine and a slot
machine, and it is a stronger claim than "the ranking is noisy": at 10,000 shoppers the leader is
`AD_1 on B1_TALKER`; at 50,000 it is a `SKU_008` move and the current placement has climbed to
2nd. The ordering is a **run-size artefact**, not a close call. The one claim that does settle —
at 250,000 shoppers, off-camera — is that moving `SKU_008` to the top shelf beats today's
placement. **No ad move clears it below 500k.**

So do not say "so we should move the creative to the shelf talker". The honest line is: *this is
the search, this is what it returns at a run size you can watch live, and this is the module
telling you not to trust the order yet.*

Then price it:

```
python scripts/optimize.py --creative AD_1     --baseline-units 120 --margin-per-unit 7.5 --stores 4 --weeks 13     --currency INR --basis "ILLUSTRATIVE ONLY -- no client volume or margin data exists"
```

The table gains a money column. **The `basis` line is the point of this half of the shot**, not the
number: margin and store traffic exist nowhere in this repository, the command refuses to run
unless the presenter supplies all six commercial inputs, and the printed footer says which column
was measured and which was assumed. Read that footer aloud. Do not round the money up, and do not
call it a forecast.

### Shot 7 — honesty panel (3:45–4:25)

No dashboard page carries this. The M7 "Noise Dashboard" was never built — `web/src/dashboard/`
holds one page, the per-session experiment view — so this shot is the terminal plus slides.

```
make eval
```

Then scroll `RESULTS.md` on camera and stop on:

- **Panel** — `Real panel: n = 0 accepted`.
- **Pre-registration** — `Prediction locks found: 0`.
- **Real vs synthetic** — every cell reads `not yet collected`.
- **Figures** — `agreement_vs_ceiling.png` *not drawn: there is no real panel to measure a noise
  ceiling on*; `calibration_fit_vs_holdout.png` *not drawn*; `reject_reasons.png` *not drawn*.
- **Known effect** — the synthetic row is filled (`0.0267 → 0.0497`, uplift `0.86`), the real row
  is not, so `same_direction` is undefined.

Then two slides: the noise-ceiling explanation (see `script.md`) and the limitations that are
already written down in [`METHODOLOGY.md §12`](../METHODOLOGY.md#12-limitations).

### Shot 8 — close (4:25–4:55)

Slides only.

- Cost and time comparison from `SPEC.md §8`. **Label it indicative on the slide itself.** Those
  figures come from the proposal and have not been recomputed from this project's data, which
  PLAN §6 required on Day 8. The one honest measured cost claim is compute: a full 10,000-shopper
  population per persona in roughly 150 ms, a what-if answer in single-digit milliseconds warm.
- Brand Lift / CPS roadmap: [`docs/integration.md`](../integration.md) and
  `sim/persona_survey.py` (S22). The instrument and the roll-up are built; **no CPS data has been
  obtained and no survey answer has been produced**, so describe it as a designed integration with
  working code, not as a result.
- "What is not built": real panel, persona traces, video ingest, GLB shell, optimizer, CI.
  One slide, plainly.
- Repo URL and QR.

## Pre-flight checklist

- [ ] `make seed` has run — `web/public/textures/*.png` exist (they are gitignored)
- [ ] `make validate` → 12 files, 0 errors
- [ ] `make test` → green, both suites
- [ ] `make eval` → `RESULTS.md` and `docs/figures/heatmap_*.png` regenerated today
- [ ] `make api` and `make web` both up; one warm-up what-if already fired
- [ ] Two browser windows placed; spectator on the second monitor with the clock visible
- [ ] Shopper window shows no dashboard, no gaze dot, no metrics
- [ ] Notifications, badges and any personal browser profile off screen
- [ ] A stopwatch on the desk — shot 4 needs a session over 45 seconds, and fifteen separate pack
      hovers before the agreement meter comes off grey
- [ ] Team names on the closing slide

## Things that will ruin a take

- Recording the shopper window with the spectator overlay visible on it. The shopper must never
  see their own gaze dot, on camera or off.
- Cutting inside a live take. A live segment with an internal edit is worth nothing here; the
  whole point is that the clock and the badge are continuous.
- Letting `?fake=1` into the cut. No shot calls for it any more. If it reaches the screen for any
  reason, all three of its labels stay in frame — the yellow border, the banner, and the
  `fake-session` / `fake-prediction` ids, which match no lock on disk — and the word "fake" is
  said out loud.
- Reading a number off the screen that is not on the screen. If `elapsed_ms` shows 9, say nine.
- Saying "the personas match real shoppers". They have not been compared to any.
