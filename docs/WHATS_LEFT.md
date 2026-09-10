# What is left

> Written after the vision-camera and honest-estimator work landed. Every claim below was checked
> against the repository at the time of writing — the commands that check each one are inline, so
> this file can be re-verified rather than believed. Where a number is quoted it was measured on
> this machine.
>
> Re-verified since, and two things had drifted: the ad-on-a-video-shelf gap was closed in
> section 2 while the re-check list at the bottom still described it as open, and the ladder's
> settled pair appears one rung lower than section 3 said. Both are corrected below.

The recording checklist lives in [`docs/video/shotlist.md`](video/shotlist.md#pre-flight-checklist).
This file is the other half: what is *not* done, ranked by whether it blocks the demo.

---

## 0. The one decision to make before recording

**`make collect` would move the real panel from n = 0 to n = 1 today.** The accepted session is
sitting in the live database and has never been exported:

```
python scripts/anonymise_sessions.py --dry-run
  exported 1 session(s): 1 accepted, 0 rejected, 0 undecided
  events written: 36
```

Running it is one command and no code. **It is not obviously the right thing to do**, which is why
it is a decision rather than a task:

| Run it | Leave it |
|---|---|
| The prediction lock becomes **gradeable** — `Locks verified to predate their session's first event` goes from 0 to 1, which is the project's central claim finally checked end to end | `n = 1` is not a panel. Spearman, KL and purchase-share MAE computed on one shopper are noise with a number printed next to them |
| Several `not yet collected` cells become real | The noise ceiling still cannot be computed — `analytics/noise_ceiling.py: MIN_SESSIONS = 4`, on the fit variant alone — so every accuracy number would be quoted against a ceiling that does not exist, which METHODOLOGY §7 says makes it uninterpretable |
| The demo shows the pipeline producing output rather than placeholders | `RESULTS.md` is byte-checked by CI and is the file the pitch calls incorruptible. Changing it to hold a meaningless number is the one move that would undercut the honesty story |

**Recommendation: leave it until there are at least six sessions on variant A.** The empty panel is
currently an *asset* — "we will not show you an accuracy number we do not have" is a stronger line
than a Spearman computed on one person. If six or more arrive before the deadline, run it.

---

## 1. Blocks the headline claim — needs people, not code

### The real panel is empty

`data/sessions/anon/` holds only `.gitkeep`. Every real-vs-synthetic cell in `RESULTS.md` reads
*not yet collected*.

- **What it needs:** 12–16 people, ~2 minutes each, in `cursor_only` mode via `#/home`. That is
  the floor at which the pipeline produces anything at all, **not** the target: PLAN S21 asks for
  **≥ 60 accepted, aiming at 100**, and `docs/video/script.md` says sixty on camera. Twelve buys a
  noise ceiling and a decision-agreement row; it does not buy a panel worth quoting a Spearman off.
- **Where to concentrate them:** at least **6 on variant A**. `noise_ceiling.py: MIN_SESSIONS = 4`
  is checked against the *fit* variant alone, so twelve spread evenly across A–D yields no ceiling
  at all. Cover at least two variants or decision agreement cannot be computed either
  (`scripts/eval.py` needs `len(real_kpi) >= 2`).
- **Then:** `make collect`.
- **Code required: none.** This is the only item on this page that a commit cannot close.

### Webcam gaze has never actually run

There is not one `fixation` event in the database — every event is `hover`, `cursor_dwell`,
`pickup`, `add_to_cart`, `checkout` or a station transition. Both sessions were `cursor_only`. The whole
capture pipeline in `web/src/capture/` is tested and has never seen a camera.

- **What it needs:** one real webcam session on the demo laptop, before rehearsal, confirming the
  finished session reports `mode: "webcam"` and `fixation_coverage > 0`.
- **If calibration will not converge on that hardware:** cut gaze from the spoken pitch *then*,
  not during the take. The 12 % calibration gate sends a failing shopper to `cursor_only` rather
  than turning them away, so nothing breaks — but the narration must not promise eye tracking the
  recording does not contain.

---

## 2. Real, and visible if a judge pushes on the video track

### Ad-to-lift on a video-read shelf — done

**This section listed the whole thing as structurally impossible, then as half solved. It is now
closed, and the entry is kept so the shape of the gap is on the record.**

Two changes, in order. `schemas/variant.schema.json` gained `add_ad_slot`, which hangs an empty
fixture on a shelf or a bay — the owning bay derived from `attached_to` rather than passed beside
it, so a fixture cannot be scored against a bay it is not on. Then `#/vision` gained the other
half: the operator names the brand being advertised and the shelf its talker hangs on, and the
save writes a creative onto the planogram and the two patches — install, then book — onto the
variant.

Measured end to end on `data/vision/demo_aisle_60s.mp4`, with **nothing hand-edited** — the
planogram and the variant are exactly what the screen posts, and both validate against their
schemas:

```
ad_slot_attention : {'V_TALKER_1': 0.0683}
exposed purchases : 185
within-run lift   : 0.867
between-arm lift  : 0.483
```

Note the within-run figure is again the larger one, on a shelf nobody tuned.

Both halves are operator-supplied and both say so: the variant's name records that a person placed
the ad, and the note above the fields says the camera detected no signage and the pipeline will not
invent any. That is the same division of labour as the labels — a retailer knows where their own
fixtures hang — and it is the only honest way to get an ad onto a shelf nobody filmed a sign on.

Leaving the brand blank saves the shelf exactly as read, with no fixture and no creative. Naming a
shelf without a brand books nothing and the screen says why: a holder with nothing in it is not an
advertisement, and the screen has no way to know which brand was meant.

### The pipeline has still never seen a real shelf

The only clip in the project is `scripts/make_vision_fixture.py`'s rendering of the seed planogram.
What changed is that it could now *survive* one: roll is corrected within ±7° and refused past it,
a moving camera is refused rather than silently multiplying products, and soft edges no longer
collapse a shelf to zero facings.

- **What it needs:** somebody to point a phone at a supermarket shelf, hold still, and run it.
  Thirty minutes.
- **Until then:** say "a rendering", every time. The measured limits are in METHODOLOGY §12.10.

---

## 3. Measured partially — quote with the caveat

### The run-size ladder — done, 10k to 500k

Re-measured on the between-arm default, all four rows:

```
n =  10,000   top AD_1 on B1_TALKER  +2.5%   current 4th of 13   seeds +1.3%..+2.5%
n =  50,000   top AD_1 on B1_TALKER  +2.0%   current 4th of 13   seeds +1.9%..+2.3%
n = 250,000   top AD_1 on B1_TALKER  +2.1%   current 3rd of 13   seeds +1.9%..+2.2%
n = 500,000   top AD_1 on B1_TALKER  +2.0%   current 5th of 13   seeds +1.8%..+2.0%
```

Same leader at every size, with the spread tightening from 1.2 points wide to 0.2. **From 50k up,
one placement clears today's** — the ad move, `AD_1` to the bay-1 shelf talker — and at 500k a
second joins it, a SKU move. At the 10k the screens run, nothing clears, which is what the amber
box on `#/optimize` says. Top pick versus runner-up is still not settled at any size, and
METHODOLOGY §12.13 carries the reading.

**Nothing left to run here.** The only thing this ladder cannot tell you is whether any of it
describes real shoppers, which is section 1.

### `sim/persona_survey.py` has never been run

The largest module in `sim/` at 733 lines — a complete CLI, 26 passing tests — and
`data/cache/surveys/` has never existed. Its only prerequisite, `data/cache/traces/`, is already populated with real model
output.

- **What it needs:** `python -m sim.persona_survey --all --max-shoppers 5` — about 100 model calls,
  twenty minutes, pennies. Note `.env` currently has `LLM_OFFLINE=1`, so this needs a working key.
- **Decide one way or the other.** Either run it and upgrade METHODOLOGY §12.12 from "design plus
  code" to "design, code and a first read-out", or cut it from the pitch. What must not happen is
  storyboarding survey numbers before that command has run.

---

## 4. Known and accepted — do not spend the remaining days here

- **The GLB store shell is procedural.** One CC0 prop is rendered; the shelving is geometry. The
  portal's sample-model requirement is met. README says so.
- **The headline lift's magnitude restates a constant nobody fitted.** Swept and published in
  [`docs/SENSITIVITY.md`](SENSITIVITY.md); quote the lift as a range, and lean on the three things
  that survive the sweep — the sign, the persona ordering, and every attention number.
- **The demo video is not recorded.** `docs/video/` is a plan, and says so at the top of both files.

---

## How to re-check this file

Section 0 — is there anything to collect?

```
python scripts/anonymise_sessions.py --dry-run
```

Section 1 — is the committed panel still empty, and has a camera ever run?

```
python -c "import pathlib; print(sorted(p.name for p in pathlib.Path('data/sessions/anon').iterdir()))"
```

```
python -c "import sqlite3, json; print(sorted({json.loads(d)['type'] for (d,) in sqlite3.connect('shoppertwin.db').execute('select data from events')}))"
```

A list without `fixation` in it means webcam gaze has still never been recorded.

Section 2 — can a variant put an ad on a video-read shelf?

```
python -c "import json; print([b['properties']['op']['const'] for b in json.load(open('schemas/variant.schema.json'))['definitions']['patch']['oneOf']])"
```

Five ops. `add_ad_slot` hangs the fixture and `set_ad_creative` books it. The half that used to
be missing was the creative itself — `vision/planogram.py` detects no signage and will not invent
any, so a video-read planogram carried an empty `creatives` list and there was nothing for
`set_ad_creative` to point at. The operator supplies it now, on `#/vision`:

```
grep -n "OPERATOR_CREATIVE_ID" web/src/vision/VisionView.tsx
```

Three hits — the id itself, the creative written onto the saved planogram, and the
`set_ad_creative` patch that books it — is the closed state described in section 2 above. Fewer
than three means this gap has reopened and section 2's `exposed purchases : 185` cannot be
reproduced.

Section 3 — the ladder, and everything else:

```
python scripts/optimize.py --focal-sku SKU_008
```

```
make validate && make test
```
