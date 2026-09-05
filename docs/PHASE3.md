# Phase 3 — after the submission

**PLAN does not define a Phase 3.** It has Phase 0 (§4), Phase 1 (§5) and Phase 2 (§6), and stops
at Day 10 with "tag `v1.0`, submit video + repo link." This document is written *after* `v1.1`,
and it is not a retrofit of the original plan: every item below comes from something this
repository had already recorded as a known limit, not from a wishlist.

`docs/PLAN.md` and `docs/SPEC.md` are deliberately untouched since S0. They are the historical
brief. This file is separate for the same reason PLAN §13's overrides are separate.

---

## What made a Phase 3 necessary

Three limits were written down during Phase 1 and Phase 2 and left standing, plus one trap
introduced while closing them:

| # | Item | Where it was already recorded |
|---|---|---|
| P3.1 | No unexposed arm exists, so no Brand Lift is possible | verified during S24; A, B and C all carry `AD_1` |
| P3.2 | `SimResult` exposes shares, not counts, so no synthetic interval is possible | METHODOLOGY §12.7 |
| P3.3 | The optimizer's ranking is not resolved | METHODOLOGY §12.13 |
| P3.4 | `.env` is documented but never read | found while configuring Ollama |

---

## P3.1 — A control arm, and the between-variant Brand Lift

**Acceptance:** a variant exists that carries no creative; it differs from variant A in the ad and
in nothing else; the between-variant lift is computed through the existing lift maths, not a
second formula.

**Done.** `data/variants/D.json` sets all three ad slots to `creative_id: null`. No schema change
was needed. A test asserts resolved-A and resolved-D are deep-equal once both have their creatives
blanked, so D controls the ad alone.

**The result, and it is the interesting part.** The between-variant Brand Lift on this aisle is
**+0.9 %, and it is not resolved at n = 10,000** — its Monte Carlo spread straddles zero. Only the
`browser` persona resolves.

More important, the two estimators disagree by several-fold:

```
within-run split on A (exposed vs unexposed)   +4.5%      <- what we had before D existed
between-variant A vs D                         +0.9%      <- what a client's study measures
```

The ratio moves with seed; the direction does not. Within-run "exposure" is a **selection** —
those shoppers had already walked to the endcap — and not a randomisation. Reporting the
within-run number as a Brand Lift would have overstated the ad's effect several times over. That
gap is the reason D has to exist, and it is the single most useful thing Phase 3 produced.

---

## P3.2 — Per-arm purchase counts, and a synthetic resolution measure

**Acceptance:** the simulator emits the event counts behind each arm; a resolution measure is
derived from them; it is **not** called a confidence interval and does not reuse `ci95`.

**Done.** `sim/simulator.py` emits `n_purchases_exposed` / `n_purchases_unexposed`. The population
row reports the Kish effective sample size rather than the pooled count. `analytics/lift.py`
resamples under a new key, `synth_mc95`; `ci95` still means the real panel's sampling uncertainty
and stays empty until that panel exists.

**The result:** three of the five lift rows straddle zero at n = 10,000 — `population`, `loyalist`
and `mission`. Only `browser` and `switcher` are resolved. That was invisible before, because
normalised shares have nothing to resample.

---

## P3.3 — Is the optimizer's ranking resolvable?

**Acceptance:** an answer either way, measured. Making `top_pick_is_resolved` return `True` by
loosening the criterion is a failure, not a pass.

**Done, and the answer is "no" for the claim we were making and "yes" for a different one.**

`top_pick_is_resolved` is unchanged and `DEFAULT_N_SYNTH` is still 10,000. What was added is
`check_top_pick_stability()`, which re-ranks across run sizes — the check a seed spread
structurally cannot make, because every seed it re-rolls is drawn at the same size.

* The default ranking is a **run-size artefact**. `AD_1@B1_TALKER` goes rank 1 → 10 → 9 → 5 → 4 as
  `n_synth` grows from 10k to 500k.
* **More seeds cannot help.** The reported spread is a min–max range and widens with the number of
  seeds; at K = 2 the top pick looks resolved, which is a false resolution bought by using fewer
  seeds. Only `n_synth` narrows the underlying σ, at 4× compute per halving.
* **Top pick vs runner-up: not separable** at any feasible size (≈ 3.7 M shoppers).
* **Top pick vs the current placement: separable at 250,000** — and the winner is a **SKU move,
  not an ad move**. No ad placement clears the current placement below 500k.

The last point changed what the demo may claim. See METHODOLOGY §12.13.

---

## P3.4 — Make `.env` real

**Acceptance:** `.env` configures the Python side, with no new pinned dependency.

**Done.** `envfile.py` is ~60 lines. A real environment variable always wins over the file, and an
empty value means "not set" rather than "empty string" so the shipped `LLM_API_KEY=` cannot shadow
an exported key.

It also exposed two things worth keeping: a root `conftest.py`, because a working `.env` otherwise
turned four test files red for reasons unrelated to the code they cover; and the discovery that
`scripts/eval.py` had silently started making a live, paid model call per run, which is now
opt-in behind `--llm-headline` (see below).

---

## Delivered alongside, not planned as Phase 3

* **CI** (`.github/workflows/ci.yml`) — SPEC M8 asked for it and it had never been built. Its third
  job re-runs `eval.py` and fails if `RESULTS.md` moves by a byte.
* **S21's collection scripts** — `collect_link.py`, `anonymise_sessions.py`.
* **An Ollama provider**, which unblocked S13's persona traces without an Anthropic key, and with
  it a transport-retry fix after a real run died on a dropped connection.
* **`eval.py` determinism restored.** Loading `.env` gave the process a key, so the headline came
  from a live model — a non-reproducible string in a committed file. It stayed stable only while
  the grounding check kept rejecting the sentence, which is luck, not the guarantee SPEC's
  acceptance line and the CI job depend on.

---

## Explicitly not in Phase 3

* **Real CPS data.** PLAN §8 lists it under *not building*, and nothing here widens that.
  `docs/integration.md` remains a design plus code, with no data behind it.
* **The real panel.** Still the only thing code cannot supply.
* **S20 vision and the S6 GLB shell.** Dropped under PLAN §5's timebox and PLAN §9's drop order
  respectively; both remain dropped.
