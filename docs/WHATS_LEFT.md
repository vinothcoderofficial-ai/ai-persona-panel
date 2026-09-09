# What is left

> Written after the vision-camera and honest-estimator work landed. Every claim below was checked
> against the repository at the time of writing — the commands that check each one are inline, so
> this file can be re-verified rather than believed. Where a number is quoted it was measured on
> this machine.

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

- **What it needs:** 12–16 people, ~2 minutes each, in `cursor_only` mode via `#/home`.
- **Where to concentrate them:** at least **6 on variant A**. `noise_ceiling.py: MIN_SESSIONS = 4`
  is checked against the *fit* variant alone, so twelve spread evenly across A–D yields no ceiling
  at all. Cover at least two variants or decision agreement cannot be computed either
  (`scripts/eval.py` needs `len(real_kpi) >= 2`).
- **Then:** `make collect`.
- **Code required: none.** This is the only item on this page that a commit cannot close.

### Webcam gaze has never actually run

There is not one `fixation` event in the database — every event is `hover`, `cursor_dwell`,
`pickup`, `add_to_cart` or a station transition. Both sessions were `cursor_only`. The whole
capture pipeline in `web/src/capture/` is tested and has never seen a camera.

- **What it needs:** one real webcam session on the demo laptop, before rehearsal, confirming the
  finished session reports `mode: "webcam"` and `fixation_coverage > 0`.
- **If calibration will not converge on that hardware:** cut gaze from the spoken pitch *then*,
  not during the take. The 12 % calibration gate sends a failing shopper to `cursor_only` rather
  than turning them away, so nothing breaks — but the narration must not promise eye tracking the
  recording does not contain.

---

## 2. Real, and visible if a judge pushes on the video track

### Ad-to-lift on a video-read shelf is still structurally impossible

This is the sharpest remaining hole in the "video in, recommendations out" story, and it is worth
stating precisely because the neighbouring problems *were* fixed.

`vision/planogram.py` emits `ad_slots: []` deliberately — no sign detection, so no invented ads.
And nothing downstream can add one: `schemas/variant.schema.json` offers exactly four patch ops —
`move_sku`, `set_ad_creative`, `swap_texture`, `set_price` — none of which creates a fixture, and
`set_ad_creative` requires an `ad_slot_id` that already exists. So a shelf read from video can be shopped, can produce attention, and can produce
purchases once an operator labels it, but it **cannot carry an ad and therefore cannot produce an
ad lift**.

- **The honest framing on stage:** the camera reads geometry; the retailer tells you where their
  signage hangs. That is one thing they never needed a camera for.
- **The fix, if you want it:** an `add_ad_slot` patch op, or an operator step on `#/vision` that
  places a fixture on the read bay — the same shape as the labelling step already built. Half a day,
  and it would make the chain complete end to end rather than complete-except-for-ads.

### The pipeline has still never seen a real shelf

The only clip in the project is `scripts/make_vision_fixture.py`'s rendering of the seed planogram.
What changed is that it could now *survive* one: roll is corrected within ±6° and refused beyond,
a moving camera is refused rather than silently multiplying products, and soft edges no longer
collapse a shelf to zero facings.

- **What it needs:** somebody to point a phone at a supermarket shelf, hold still, and run it.
  Thirty minutes.
- **Until then:** say "a rendering", every time. The measured limits are in METHODOLOGY §12.10.

---

## 3. Measured partially — quote with the caveat

### The run-size ladder is done to 250k, not 500k

Re-measured on the between-arm default:

```
n =  10,000   top AD_1 on B1_TALKER  +2.5%   current 4th of 13
n =  50,000   top AD_1 on B1_TALKER  +2.0%   current 4th of 13
n = 250,000   top AD_1 on B1_TALKER  +2.1%   current 3rd of 13   <- top pick clears current
```

At 250k the optimizer prints *"1 placement(s) clear the current placement's seed spread entirely:
ad:AD_1@B1_TALKER"*. The 500k row has not been re-measured, and **top pick versus runner-up is
still not settled at any size measured** — only the pair against today's placement is.

- **What it needs:** one 500k run (~40 min unattended) if anyone wants to close the ladder.
- **Not a blocker.** METHODOLOGY §12.13 already says which rows exist.

### `sim/persona_survey.py` has never been run

The largest module in `sim/` — a complete CLI, 25 passing tests — and `data/cache/surveys/` has
never existed. Its only prerequisite, `data/cache/traces/`, is already populated with real model
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

Four ops, none of which creates a fixture, means it still cannot.

Section 3 — the ladder, and everything else:

```
python scripts/optimize.py --focal-sku SKU_008
```

```
make validate && make test
```
