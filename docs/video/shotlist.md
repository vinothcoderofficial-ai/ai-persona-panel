# Demo video — shot list

Companion to [`script.md`](script.md), which carries the narration. This file is what the person
at the keyboard follows.

**Status: not recorded.** Nothing in `docs/video/` is a recording; there is no MP4, no SRT and no
GIF in this repository. What follows is the plan.

Every number quoted below was read off a running instance while this file was written, not
remembered. Where a value can drift — a wall-clock timing, a cold cache — it is marked as such.

## Technical setup

| | |
|---|---|
| Capture | OBS Studio, 1920 × 1080, 30 fps, H.264 MP4 |
| Audio | Laptop mic or headset, one pass, no music under speech |
| Subtitles | `.srt` alongside the MP4 |
| Browsers | Two windows on two monitors: **shopper** (1920 × 1080, nothing else on screen) and **operator** (`#/home` and everything reached from it) |
| Servers | `make api` and `make web` running before recording starts; `make seed` already run so textures exist |
| Fixture clip | `python scripts/make_vision_fixture.py` has been run, so `data/vision/demo_aisle.mp4` exists (it is gitignored and regenerates in a second) |
| Clock | `ClockOverlay` is visible on the spectator window for every live segment. Do not crop it out. |

**Rules for the live segments.** No internal edits — a live segment is one continuous take from
first frame to last. Retakes are free; cuts inside a take are not. The wall clock runs throughout,
which is what makes "the prediction was locked at 10:32:07 and shopping began at 10:32:41" a
checkable statement rather than a claim.

---

## Read this before setting up: two things will go wrong on camera

### 1. The LLM key is dead, and shot 6 has a button that calls it

`.env` is configured for `LLM_PROVIDER=ollama`, model `deepseek-v4-pro:cloud`. That key now
returns **HTTP 401**. The 80 committed persona traces were generated with it while it worked, so
everything shot 6 *reads* is real and offline — but the **"Ask the model again"** button makes a
live call and will fail.

Decide which before you record, and say the matching line from `script.md`:

| Option | What is on screen | Cost |
|---|---|---|
| **A — refresh the key.** Put a working `LLM_API_KEY` in `.env` and restart the API. | The button works. A fresh policy comes back beside the committed one, with the fields that moved highlighted. | Needs a working account. This is the better shot by some distance. |
| **B — record the failure.** Change nothing. | The button returns `ollama returned HTTP 401 … Check LLM_API_KEY in .env`, rendered on screen. | Honest, and the panel is *designed* to show exactly this — but it is a failure in the middle of the shot that most needs to look solid. |
| **C — go offline deliberately.** Set `LLM_OFFLINE=1` in `.env` and restart the API. | The button is **disabled**, and the panel says why: cached policies and traces are being served, and `LLM_OFFLINE=0` re-enables live calls. | Nothing fails on camera and nothing is hidden. Use this if A is not available. |

Do not record option B by accident. If the key is dead and nobody chose, take option C.

### 2. `#/vision` reads a rendering, not real footage

There is no video of a real aisle in this project and none can be invented. The only clip is
`scripts/make_vision_fixture.py`'s rendering of the seed planogram — even lighting, no
perspective, no occlusion, no motion blur, no shopper's arm.

The pipeline reads it exactly right, which demonstrates that the stages compose end to end on a
real file. It demonstrates **nothing about accuracy on real footage.** Say so in the shot, in the
words `script.md` gives you. Do not say "we scanned an aisle".

---

## What changed from SPEC M8's shot list, and why

SPEC M8 specifies seven shots. One is now recordable that was not, and one is still not.

| SPEC shot | Status | How it is handled |
|---|---|---|
| `0:40–1:40` Phone clip → `VisionProgress`, boxes per frame, store assembling | **Recordable, with a caveat.** The pipeline exists (`vision/`, S30) and runs on a CPU: shelf edges, colour-run facings, IoU agreement, a schema-valid planogram. What does not exist is a clip of a real aisle. | Shot 3, on the fixture clip, narrated as a rendering. Products come back as `unidentified product N` because the pipeline reads geometry and colour and not identities — which is stated in the shot rather than skipped past. |
| `1:40–2:55` Colleague shops with webcam gaze; agreement meter climbs | **Partly.** There is no collected panel and the S9 webcam pilot never ran, so a webcam take cannot be promised. | Shot 5: a real, consented, **cursor-only** session. The agreement meter does turn on — counting cursor dwells rather than fixations — but say on camera that this is agreement against a cursor proxy, not gaze. |
| PLAN §6's optimizer recommendation | **Recordable, and now a screen.** S24 ranked placements from a terminal; S29 put it on `#/optimize`. | Shot 7's second half. Narrated as a search whose order is *not settled*, which the screen says itself. |

---

## Shot list

| # | t | Shot | Live? | Screen |
|---|---|---|---|---|
| 1 | 0:00–0:15 | Problem: physical test stores are slow and expensive; surveys measure what people *say*; attention vendors sell heatmaps and stop before purchase | slides | — |
| 2 | 0:15–0:40 | **What exists.** `#/home`: every screen in the product, and the collection panel: 1 accepted, 0 rejected — and the committed corpus still empty, which is why RESULTS.md reads n = 0 | **live** | operator window |
| 3 | 0:40–1:10 | **A shelf, read from video.** `#/vision`, drop the fixture clip, watch it become a planogram with per-slot confidence | **live** | operator window |
| 4 | 1:10–1:45 | **The store.** Arrow-key between the three shelf stations; hover, pick up, add to cart; point at the empty eye-level slot | **live, one take** | shopper window |
| 5 | 1:45–2:50 | **A person, against a locked prediction.** Consent → intake → "Continue without the camera" → shop. Spectator shows the hash *before* the first event, then the heatmap building beside the locked prediction | **live, one take** | both windows |
| 6 | 2:50–3:30 | **The model, and a synthetic shopper.** `#/ai`: the model, the prompt, the policy, the traces. Then `#/panel`: one persona's trip replayed over the same shelf | **live** | operator window |
| 7 | 3:30–4:15 | **What-if, then the recommendation.** `#/whatif`: move the focal SKU to eye level, read `elapsed_ms`. Then `#/optimize`: 13 placements ranked on between-arm lift, today's 4th, and the screen saying the order is not settled | **live** | operator window |
| 8 | 4:15–4:55 | **The honesty panel, and what is missing.** `RESULTS.md` in the terminal; noise-ceiling slide; what is not built; repo URL | terminal + slides | — |

Total **4:55**, inside SPEC's assumed five-minute limit. SPEC §1 says "assume ≤ 5 min; **confirm
the portal limit**" — that confirmation has not happened. If the limit turns out to be longer, the
[extended cut](#extended-cut-if-the-portal-allows-more-than-five-minutes) at the end of this file
says what to restore first.

Six of the eight shots are live. That is deliberate — nearly everything in this project is a
running screen rather than a slide — but it is a lot to hold together, so rehearse the order once
before recording anything.

---

## Per-shot setup

### Shot 2 — what exists (0:15–0:40)

Operator window at `http://localhost:5173/#/home`.

This replaces what used to be a slide reading "one panel exists, one does not". It is a stronger
beat as a live screen, because the number is on screen rather than asserted.

**The collection panel is the first thing on the page.** Read from a running instance while this
was written:

| Figure | Value | Say |
|---|---|---|
| accepted | **1** | One person has shopped a recorded session, and it passed the gate. |
| rejected | **0** | — |
| unfinished | 0 | — |
| declined consent | 1 | A rehearsal run — `consent: false`, self-rejecting. |
| committed | **0** | And it is *not* in the corpus. This is the honest part of the shot. |
| locks | **1** | The prediction for it was locked before it started. |

Below the figures, the acceptance rule — 6 or more slots looked at, across 2 or more bays, with
at least 1 interaction, and no minimum length.

**The line to land is accepted 1, committed 0.** The session passed the gate and still did not
become evidence, because `scripts/eval.py` could not verify that its prediction lock was written
before its first event. It was collected before the server stamped `first_event_at`, so eval has
to reconstruct the arrival time as `started_at + t_ms`, which is biased early by the
`POST /sessions` round trip. The ordering was almost certainly fine. Almost certainly is not the
standard, and the check was not weakened to let it through.

Say that plainly. A demo that shows a system refusing its own only datapoint is worth more than
one that shows a full dashboard, and this is the single strongest thirty seconds in the video for
proving the pre-registration is real rather than decorative.

Two things to be honest about if asked, both documented in METHODOLOGY.md 2.3:

* That accepted **1** was a re-gate. The session was originally rejected by a 45-second duration
  floor, which was removed because it preferentially discarded shoppers with a list — the
  `mission` archetype, one of the four the panel exists to validate. The rule was changed because
  of this session and then applied to it, which a sceptical viewer is right to flag. It cleared
  the new rule at exactly the threshold, 6 slots of 6.
* The session carries a `regated` block recording the verdict it replaced, and RESULTS.md prints
  the re-gated count beside the panel size, so the caveat cannot be read separately from the
  number.

Then scroll once through the destination cards so the audience sees the product has eight screens
and where the next few minutes are going. Do not click into any of them yet.

**Never put `#/home` on the shopper's monitor.** That screen stays clean for shots 4 and 5.

### Shot 3 — a shelf, read from video (0:40–1:10)

Operator window at `http://localhost:5173/#/vision`.

Pre-flight: `python scripts/make_vision_fixture.py` has been run. The clip is
`data/vision/demo_aisle.mp4`, about **204 KB**, 40 frames at 960 × 720.

1. Read the intro line on screen aloud or paraphrase it — it states the pipeline's limits before
   anything is uploaded, which is the right order.
2. Choose the clip.
3. It returns in about **2 seconds** on a warm API.

What comes back, verified:

| | |
|---|---|
| frames sampled | **8** (40 frames at 10 fps, sampled at 2 fps) |
| shelves | **5** |
| facings | **8** |
| levels | top, above eye, eye, below eye, bottom |
| confidences | **0.85 – 1.00** per slot |
| saved | **false**, and the screen says so in a caution box |

**Bay 1 of the seed planogram has exactly 5 shelves and 8 filled slots** (`B1S3P2` and `B1S5P2`
are empty). The reading is correct. Say that — it is the check that makes the shot worth
anything — and say in the same breath that the clip is a rendering of that planogram, so this is
a round trip rather than a field test.

Point at three things and no more:

* every product reads **`unidentified product N`**, because the pipeline reads geometry and
  colour and cannot read a brand, a name or a price;
* the **confidence** on each facing, which is per-frame distinctness multiplied by how many of the
  8 frames agreed;
* the **"this reading was not saved"** box. Reading a video is not committing a store.

### Shot 4 — the store (1:10–1:45)

Open `http://localhost:5173/?variant=A&skip_capture=1` in the shopper window.

- `?skip_capture=1` jumps straight to the store. It records `consent: false` — the truth, since
  nobody sat down and agreed to anything — which makes the session self-rejecting at the gate.
  **Say this on camera.** It is a developer shortcut, not a shopper.
- Arrow keys ←/→ move between bays. The camera lerps for 600 ms and then rests. There is no free
  roam anywhere, and the narration explains why.
- Hover a product for the `cursor_dwell`; click it for the pickup card; add to cart.
- Point at `B1S3P2`: an empty eye-level position, a real slot object with `sku_id: null`. That is
  the target variant B moves `SKU_008` into, and the thing shot 7 changes.
- The HUD now carries a **calibration line** and a **tracker line**. In this rehearsal session
  they read `Calibration: not measured` and `Mouse position stands in for gaze`, which is correct
  and worth a sentence: the store says what it is measuring you with.

The split-screen with `data/planograms/demo_aisle.json` that this shot used to open on is
**dropped** — shot 3 has just shown a planogram document being produced, so a second look at one
costs 10 seconds and adds nothing.

### Shot 5 — a person, against a locked prediction (1:45–2:50)

Restart the shopper window at `http://localhost:5173/?variant=A` — **no** `skip_capture`, so the
real capture flow runs and the session is consented. Variant A keeps the aisle in the state shot 4
showed and shot 7 will change, so the live shots tell one story.

1. Consent screen → accept.
2. Intake, three questions.
3. Camera check → **"Continue without the camera"**. This sets `mode: "cursor_only"` with
   `consent: true`: a real, gate-eligible session.
4. The store opens. `POST /sessions` has already written `predictions/{session_id}.json`.
   The HUD's calibration line now reads `Calibration: not measured` for a *consented* session,
   which is the honest state — no validation ran because no camera was used.
5. On the second monitor, open `http://localhost:5173/#/spectator`. With no `?session=` it follows
   the last session started in this browser. Prefer `#/home` if you want the id visible on camera:
   its **last session** box shows the uuid and links straight through.

   Fallbacks if the second window is a different browser or profile (`localStorage` is per-origin
   per-profile) — decide which before you start recording: the newest file in `predictions/`
   (`ls -t predictions | head -1`), or the `POST /sessions` request in DevTools.

   A spectator joining mid-session is sent the current snapshot on its first frame, so the badge
   and the heatmap populate immediately.
6. Dwell on at least six different products across at least two stations, with at least one
   interaction, or the session gate will reject it — and say so as you do it. There is no time
   requirement: shop briskly if you like, but cover the shelf. Shot 2 has already shown the
   audience an empty panel and the gate that caused it, so this lands.
7. **Move the cursor from pack to pack, deliberately.** The agreement meter needs fifteen cursor
   dwells, and a dwell is not elapsed time on the shelf. `CursorTracker` opens one when the pointer
   enters a *product* rectangle and emits it only when the pointer **leaves** that rectangle having
   been inside it for 300 ms or more. Fifteen dwells is fifteen separate packs touched by the
   cursor; parking on one pack produces exactly one.
8. Watch the HUD's **gaze-sample counter** stay at "Mouse position stands in for gaze" throughout.
   In a webcam session it would climb. It is one more thing on screen that refuses to imply a
   measurement that was not taken.

**What the spectator window will and will not show, in a cursor-only session:**

| Element | Behaviour |
|---|---|
| `PredictionBadge` | Hash prefix and `created_at`, populated on the first frame. This is the shot's whole point. |
| `ClockOverlay` | Running wall clock. |
| `LiveHeatmap` | Builds from cursor dwell and interactions. Works. |
| `GazeTrail` | **Stays empty.** Only `gaze` and `fixation` events carry a screen position and a cursor-only session emits neither. |
| `AgreementMeter` | **Turns on mid-shot** once 15 cursor dwells have arrived. The label counts up ("9 of 15 cursor dwells") and ρ appears at 15. |

Do not hide the empty gaze trail, and do not let the meter coming on pass without saying what it
counted.

**The `?fake=1` cutaway is cut.** It existed because the meter stayed grey through a cursor-only
take; the meter comes on during the take now, so the beat has nothing to show. If a take finishes
short of fifteen dwells, retake it — splicing a frame labelled "fake" into the one shot whose
argument is *this is real and it was locked first* costs more than it pays.

**If a laptop's webcam calibration passes on the day**, prefer the webcam variant: at the camera
check choose "Turn the camera on", complete the 9-point calibration and the 4-point validation,
and if the error is at or under 12 % of screen width the session runs in `webcam` mode. Three
things change: the gaze trail draws, the meter counts fixations instead of dwells, and **the store
HUD shows the calibration error in pixels and as a percentage of screen width, with a live count
of gaze samples delivered**. That last one is worth five extra seconds if you get it. Do not plan
on it: the S9 pilot was never run and no webcam session has ever been recorded.

### Shot 6 — the model, and a synthetic shopper (2:50–3:30)

Two screens, back to back, in the operator window. **Read the LLM-key warning at the top of this
file before recording this shot.**

#### 6a — `#/ai` (about 25 s)

Verified against a running instance:

| | |
|---|---|
| provider | `ollama` |
| model | `deepseek-v4-pro:cloud` |
| live calls | `configured` — see the warning; this says a credential is present, not that it works |
| personas | 4, each with a cached policy and 20 traced shoppers |
| traces | **80 shopping trips, 973 turns total** — browser 435, loyalist 245, switcher 194, mission 99 |

1. Point at the **model name**, large, top of screen.
2. Point at the **prompt** panel — the actual string `sim/policy.py` sends, fetched from the
   server, not a copy.
3. Point at the **policy** beside it — the eleven numbers the simulator is running. Say that they
   were **written by hand in S2**, which is what the line under them says. The prompt is what the
   button sends; it did not produce these.
4. **The button**, per your chosen option A / B / C above.
5. Scroll to **the trace**. Pick `mission`, shopper 1, and read one turn aloud. The first is:
   *"Need chips; this one is cheap and on promotion."* — a real sentence from a real model call,
   committed in `data/cache/traces/`.

Say the distinction plainly, because it is the one an audience will otherwise get wrong: the
heatmaps everywhere else in this video come from a **numpy simulator**, not from a language model.
The model wrote these traces. It did not write the heatmap, and it did not write the policies.

#### 6b — `#/panel` (about 15 s)

Same persona. Press **Play** and let it run four or five turns.

- The bay the shopper is standing at is outlined and says *shopper is here*.
- The slot it is looking at is highlighted, named as a **product**, with the model's reason in
  quotes beneath.
- The cart fills — with product names and a running total, not SKU ids.

This is the synthetic half of shot 5: the same shelf, shopped by something that is not a person.
Stop it before it finishes; the point is made in five turns.

### Shot 7 — what-if, then the recommendation (3:30–4:15)

#### 7a — `#/whatif` (about 20 s)

1. Focal SKU `SKU_008`, shelf level → **eye**. Read `elapsed_ms` aloud from the screen.
2. Let `HeatmapDiff` finish its 600 ms animation and let `LiftBars` settle.

Reference values at seed 42, verified: the eye-level move reports about **+0.78 focal attention**
and **+1.15 focal purchase share** relative to baseline. `elapsed_ms` is **1 ms warm**; the first
call after a cold start was 410 ms, so fire one warm-up before recording.

The heatmap rows are now **named as products** rather than as slot ids. Worth one clause — it is
the difference between a demo a stranger can read and one only the authors can.

The second what-if change (moving the creative to the bay-1 shelf talker) is **dropped for time**
and folded into 7b, which searches every ad placement rather than trying one.

#### 7b — `#/optimize` (about 25 s)

Type **`SKU_008`** into the focal-SKU box before running, so the space matches the narration
below. Without it the screen ranks 8 ad placements; with it, 13.

Verified, with `SKU_008`:

| | |
|---|---|
| objective | **between-arm brand lift** — the screen names it |
| placements scored | **13** |
| top pick | `AD_1 on B1_TALKER (shelf_talker, bay B1)` at **+2.5 %** |
| today's placement | **4th of 13**, at +0.9 % |
| order settled? | **no** — the panel says so, and names the rows it is not ranked against |
| placements clearing today's spread | **none** |
| wall time | ~2 s warm, ~8 s cold |

Re-measured after the optimizer was moved onto the randomised estimator. **The old numbers in
this table were +12.7 % and 5th of 13**, from the within-run exposed-versus-unexposed split. If
you have rehearsed those, unlearn them: the screen now says +2.5 % and 4th, and the gap between
those two pairs is the entire point of shot 7c.

Point at exactly two things:

* today's placement sitting **4th of 13**, and
* the amber box that begins **"This order is not settled."**

Say the second one on camera. It is the difference between a recommendation engine and a slot
machine: the panel names the four rows whose seed spreads overlap the leader's, so it is telling
you what it has *not* established. And no placement clears today's spread either, so "moving beats
where it is now" is not a claim this run size supports in any direction.

**Do not say the ordering is a run-size artefact.** That was true of the old estimator and is not
true of this one — measured on the committed aisle, `AD_1 on B1_TALKER` leads at both 10,000
(+2.5 %) and 50,000 (+2.0 %), today's placement holds 4th at both, and the leader's seed spread
*narrows* from +1.3…+2.5 to +1.9…+2.3. The within-run split moved its leader around because its
numerator came from the ad-exposed arm, roughly one purchase event in 42; the between-arm
comparison divides by two whole populations and does not have that problem. Stable is not the same
as resolved, and the amber box is still the honest thing to point at.

**If a judge asks whether it ever settles:** yes, at 250,000 shoppers, off-camera — and it settles
*for* the ad move, `AD_1` to the bay-1 shelf talker at +2.1 % against today's +1.0 %, seed ranges not
overlapping. Under the old within-run estimator no ad move cleared today's placement below 500k and
the only settled claim was a SKU move, so this reversed when the estimator did. One aisle, one
creative, 25× the run size on screen. Answer the question with it; do not narrate it over a 10k screen.

Say "beats where it is now", not "is the best placement". Even at 250k the order *among the
leaders* is still unsettled — the only pair the code calls settled is the top pick against today's
placement, and it says so in those words.

So do not say "so we should move the creative to the shelf talker".

**The priced re-run is dropped from the cut for time.** `analytics/slot_value.py` and its
`--basis` requirement are real and worth a sentence in shot 8's roadmap slide, but the money table
needs its whole "the command refused to print a number until I supplied six commercial inputs"
explanation or it is worse than useless, and that does not fit in 4:55. It is the first thing to
restore in an extended cut.

### Shot 8 — the honesty panel, and what is missing (4:15–4:55)

```
make eval
```

Then scroll `RESULTS.md` on camera and stop on, verified:

- **Panel** — `Real panel: n = 0 accepted`, 0 rejected.
- **Pre-registration** — `Prediction locks found: 1`, `sha256 recomputed and matched: 1`,
  **`Locks verified to predate their session's first event: 0`**.
- **Real vs synthetic** — every cell reads `not yet collected`.
- **Figures** — three PNGs *not drawn*, each with its reason.
- **Known effect** — the synthetic row is filled (`0.0267 → 0.0497`, uplift `0.86`), the real row
  is not, so `same_direction` is undefined.

That third pre-registration line is new and is the most interesting thing in the file. One lock
exists, its hash verifies, and it is **not** verified to predate its session's first event —
because that session is not in the committed corpus, so there is nothing to check it against. Shot
2 showed why it is not in the corpus. The file and the panel agree, and neither of them rounds the
awkward part off.

Then two slides: the noise-ceiling explanation (see `script.md`) and the limitations in
[`METHODOLOGY.md §12`](../METHODOLOGY.md#12-limitations).

Close on **what is not built**, which is now a much shorter list than it was:

- **the real panel** — no accepted sessions;
- **product identities from video** — the pipeline reads geometry and colour, not brands, names,
  prices or signs;
- **a video of a real aisle** — the only clip is a rendering of the seed planogram;
- **the GLB store shell** — one CC0 prop is rendered, not a modelled interior.

Then the repo URL and QR.

---

## Pre-flight checklist

Every threshold below was read out of the code while this was written, not remembered. Where a
number is a gate the recording can fail, the file it lives in is named.

**State of this machine, checked:** `LLM_OFFLINE=1` is already set in `.env`, which is **option C** —
shot 6's button is disabled and the panel says why. `data/vision/demo_aisle.mp4` exists, 32 textures
are built, and `docs/figures/` holds four heatmaps and the what-if GIF.

### Build

- [ ] Decided **A, B or C** for the LLM key (top of this file). Currently **C** — change `.env` only
      if you have a working key and want option A
- [ ] `python scripts/make_vision_fixture.py` has run — `data/vision/demo_aisle.mp4` exists
- [ ] `make seed` has run — `web/public/textures/*.png` exist (gitignored, 32 files)
- [ ] `make validate` → **13 files, 0 errors**
- [ ] `make test` → green: **999 python, 681 web**
- [ ] `make eval` → `RESULTS.md` and `docs/figures/heatmap_*.png` regenerated today
- [ ] `make api` and `make web` both up

### Warm up before rolling

- [ ] **One warm-up what-if and one warm-up optimize already fired.** Cold calls are ~410 ms and
      ~8 s; warm are ~1 ms and ~2 s, and the difference is visible on camera
- [ ] **Type `SKU_008` into the optimize focal-SKU box** before the take, or the space is 8
      placements and the narration says 13

### The two gates a live take can fail

- [ ] **Shot 5 needs six distinct slots across two bays, plus one interaction.** That is the whole
      gate — `web/src/capture/SessionGate.ts`: `MIN_OBSERVED_SLOTS = 6`, `MIN_STATIONS = 2`,
      `MIN_INTERACTIONS = 1`. **There is no duration floor any more.** An earlier version of this
      checklist said "a session over 45 seconds"; that rule was removed because it discarded the
      mission archetype preferentially. Hover six *different* packs and use two bays — a long
      session that lingers on four packs is rejected and a brisk one that sees six is not
- [ ] **The agreement meter needs 15 pieces of evidence before it shows a number** —
      `api/app/live.py: MEANINGFUL_MIN_EVIDENCE = 15`. Below that it renders "warming up" rather
      than a greyed-out figure. In cursor-only mode the evidence is cursor dwells, and a dwell
      needs 300 ms held on one slot, so budget roughly fifteen deliberate hovers

### Room

- [ ] Two browser windows placed; spectator on the second monitor with the clock visible
- [ ] Shopper window shows no dashboard, no gaze dot, no metrics, no `#/home`
- [ ] Notifications, badges and any personal browser profile off screen
- [ ] Team names on the closing slide

## Things that will ruin a take

- **Saying "we scanned a real aisle".** The clip in shot 3 is a rendering of the seed planogram.
- **Letting shot 6's button fail without having chosen to.** See the warning at the top.
- **Implying the heatmap was reasoned by the language model.** It is numpy. The model wrote the
  traces, and shot 6 shows them.
- **Implying the model wrote the policies.** It did not — they were hand-written in S2. The panel
  heading now reads "The policy being simulated"; the sentence that goes with it is "written by
  hand", never "what it answered".
- **Quoting the old optimizer numbers.** +12.7 % and 5th of 13 came from the within-run split.
  The screen now reads +2.5 % and 4th of 13 on the between-arm lift.
- **Saying "the best placement".** Even at 250k only the pair against today's placement is
  settled. Say "beats where it is now".
- **Implying `#/vision` saved anything before you clicked.** Reading a clip saves nothing and the
  screen says so; there is now a deliberate **keep this reading** step, and it comes *after* the
  labelling. Claim the save only once it is on screen.
- **Implying the camera identified the products.** It read geometry and colour. A person typed
  the brands, prices and categories in, and the payload marks them operator-supplied.
- **Implying the camera found the ad.** It detected no signage and emits none. The operator
  names the brand and the shelf, and the variant's name records that a person placed it.
- Recording the shopper window with the spectator overlay visible on it. The shopper must never
  see their own gaze dot, on camera or off.
- Cutting inside a live take. The clock and the badge are continuous or the shot is worth nothing.
- Letting `?fake=1` into the cut. No shot calls for it. If it reaches the screen, all three of its
  labels stay in frame — the yellow border, the banner, and the `fake-session` / `fake-prediction`
  ids, which match no lock on disk — and the word "fake" is said out loud.
- Reading a number off the screen that is not on the screen. If `elapsed_ms` shows 1, say one.
- Saying "the personas match real shoppers". They have not been compared to any.

---

## Extended cut, if the portal allows more than five minutes

In this order, because this is the order of how much each one adds:

1. **+40 s — the priced ranking** (shot 7). `python scripts/optimize.py --creative AD_1` with all
   six commercial flags and a `--basis` line reading `ILLUSTRATIVE ONLY`. The point is not the
   money column: it is that the command **refuses to print a number** until the presenter supplies
   every commercial input, and the footer says which column was measured and which was assumed.
   This is one of the strongest honesty beats in the project and it is cut purely for time.
2. **+30 s — the second what-if** (shot 7a). Move the creative from the bay-3 endcap to the bay-1
   shelf talker, variant C's actual patch, and re-run. Shows ad placement as well as shelf
   placement.
3. **+25 s — the dashboard** (`#/dashboard`). Per-slot real-versus-synthetic bars for one session,
   now named as products, and the exported HTML report. It is cut because with no accepted panel
   the real series is absent and the screen's own message says so — which is honest but is a
   second telling of shot 8's point.
4. **+20 s — the webcam variant of shot 5**, if a calibration passes on the day: the gaze trail
   drawing, and the store HUD showing the calibration error in pixels and as a percentage of
   screen width beside a climbing gaze-sample count.
5. **+15 s — `#/panel`'s shopper picker** (shot 6b). Step through two more of the twenty traced
   shoppers to show they differ — the browser persona takes 435 turns across 20 trips where the
   mission persona takes 99.
