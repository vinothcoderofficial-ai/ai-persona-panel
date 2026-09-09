# Sensitivity of the purchase model's constants

> Regenerate every table below with:
>
> ```
> .venv/Scripts/python.exe scripts/sweep_purchase_constants.py
> ```
>
> That command runs 80 simulations of 10,000 shoppers x 4 personas in about 23 seconds and
> prints this document's tables verbatim on stdout. Two runs produce byte-identical output.
> `scripts/sweep_purchase_constants.py` is the only thing that produced them; nothing here was
> typed by hand except the reading in §4 and §5.

---

## 0. What this is, and what it is not

`RESULTS.md` reports one number first: the Ad-to-Purchase Lift, **browser 0.32 on variant `A`**.
It is the number this project exists to produce, because attention heatmaps are a commodity and a
purchase effect is not.

That number comes out of about fifteen lines of `sim/simulator.py` — the `for rank in
range(min(MAX_PURCHASE_CANDIDATES, n_t))` block — and **every constant in those lines was written,
not fitted**. `docs/SPEC.md` M4 mandates most of them, but SPEC M4 is a design document we wrote.
No shopper data was ever regressed to produce a Gumbel scale of 0.1, an ad coefficient of 0.2, or a
top-2 candidate rule. `docs/METHODOLOGY.md` §0 already says the real panel is empty; this file says
what follows from that for the headline specifically.

So this sweep **measures the model's dependence on its own free parameters**. It fits nothing,
recommends no value, and changes no committed constant. Every point estimate in `RESULTS.md`
remains what the committed constants imply — not what any data fitted, because no data has been
collected to fit against.

Read §4 before quoting any single number from this file.

---

## 1. The constants that were swept

| constant                                                 | where                                  | committed | swept over                        | status in SPEC M4                                                                   |
|----------------------------------------------------------|----------------------------------------|-----------|-----------------------------------|-------------------------------------------------------------------------------------|
| PURCHASE_GUMBEL_SCALE                                    | sim/simulator.py, module level         | 0.10      | 0.05, 0.10, 0.15, 0.25, 0.40      | mandated: SPEC M4 writes the utility's noise term as Gumbel(0, 0.1)                 |
| MAX_PURCHASE_CANDIDATES                                  | sim/simulator.py, module level         | 2         | 1, 2, 3, 4                        | mandated: SPEC M4 says 'for top-2 fixated SKUs matching a goal category'            |
| the 0.2 in `ad_pull = 0.2 * ad_receptivity * brand_seen` | sim/simulator.py, inside run().visit() | 0.20      | 0.00, 0.10, 0.20, 0.30, 0.40      | mandated: SPEC M4 writes `+ 0.2*ad_exposure*ad_receptivity*(ad.brand == sku.brand)` |
| purchase_threshold (per-persona policy field)            | data/cache/policies/*.json             | +0.00     | -0.10, -0.05, +0.00, +0.05, +0.10 | free: SPEC M4 asks the LLM for it; no value is mandated and none was fitted         |

"Mandated by SPEC M4" is not the same as "supported by evidence". Three of these four are in the
spec and none of the four is in any dataset. `purchase_threshold` is the most obviously free: SPEC
M4 asks an LLM at temperature 0 to "set purchase_threshold so a neutral shopper converts near
{baseline_conv}", and the four numbers now in `data/cache/policies/` (0.45, 0.35, 0.25, 0.42) are
one model's answer to that prompt on one day.

The ad coefficient is swept through the policy field it multiplies. `ad_receptivity` appears
exactly once in `sim/simulator.py`, in the `ad_pull` line, so scaling the field by *m* is
arithmetically identical to scaling that `0.2` by *m*; the sweep reports the product as one
effective coefficient because the model cannot distinguish the two factors anyway.
`scripts/tests/test_sweep.py::test_ad_receptivity_touches_the_purchase_model_and_nothing_else`
pins the invariance that argument rests on.

**Constants deliberately not swept** — see §5.

---

## 2. The Monte Carlo yardstick

Nothing in §3 means anything unless it is wider than this table. Same constants, same planogram,
same personas; only the random seed changes.

| metric         | mean over seeds | seed 42 | min    | max    | spread |
|----------------|-----------------|---------|--------|--------|--------|
| browser        | 0.287           | 0.315   | 0.227  | 0.355  | 0.129  |
| loyalist       | 0.034           | 0.024   | 0.022  | 0.051  | 0.029  |
| mission        | -0.094          | -0.076  | -0.179 | 0.094  | 0.273  |
| switcher       | 0.104           | 0.112   | 0.092  | 0.112  | 0.020  |
| population     | 0.043           | 0.045   | 0.013  | 0.089  | 0.076  |
| SKU_008 share  | 0.0210          | 0.0211  | 0.0201 | 0.0215 | 0.0014 |
| AD_1 attention | 0.0832          | 0.0816  | 0.0816 | 0.0848 | 0.0033 |

The `seed 42` column is the one `RESULTS.md` prints: 42 is `api/app/prediction.py`'s seed, the seed
every committed prediction lock and every synthetic number in the repository was run at.

**This table is a finding on its own.** Before any constant moves, the browser lift is a draw from
something that lands between 0.227 and 0.355 over five seeds, and `RESULTS.md`'s 0.32 is the high
end of it. The synthetic MC spread `RESULTS.md` already reports for that row (0.19 to 0.46, from
resampling seed 42's own purchase events) is the same message by a different route, and the two
agree.

---

## 3. The tables

Every cell is the mean over seeds 42–46 at 10,000 shoppers per persona on variant `A`.

### PURCHASE_GUMBEL_SCALE

| scale              | browser | loyalist | mission | switcher | population | SKU_008 share | AD_1 attention |
|--------------------|---------|----------|---------|----------|------------|---------------|----------------|
| 0.05               | 0.548   | 0.005    | -0.081  | 0.176    | 0.073      | 0.0172        | 0.0839         |
| 0.10  <- committed | 0.287   | 0.034    | -0.094  | 0.104    | 0.043      | 0.0210        | 0.0832         |
| 0.15               | 0.184   | 0.045    | -0.043  | 0.097    | 0.047      | 0.0252        | 0.0827         |
| 0.25               | 0.095   | 0.056    | 0.006   | 0.058    | 0.048      | 0.0281        | 0.0819         |
| 0.40               | 0.053   | 0.038    | 0.026   | 0.049    | 0.039      | 0.0283        | 0.0813         |

### MAX_PURCHASE_CANDIDATES

| candidates      | browser | loyalist | mission | switcher | population | SKU_008 share | AD_1 attention |
|-----------------|---------|----------|---------|----------|------------|---------------|----------------|
| 1               | 0.302   | 0.026    | -0.030  | 0.079    | 0.051      | 0.0149        | 0.0819         |
| 2  <- committed | 0.287   | 0.034    | -0.094  | 0.104    | 0.043      | 0.0210        | 0.0832         |
| 3               | 0.278   | 0.035    | 0.005   | 0.087    | 0.067      | 0.0218        | 0.0848         |
| 4               | 0.253   | 0.042    | 0.063   | 0.111    | 0.085      | 0.0228        | 0.0838         |

### the 0.2 in `ad_pull = 0.2 * ad_receptivity * brand_seen`

| 0.2 x mult         | browser | loyalist | mission | switcher | population | SKU_008 share | AD_1 attention |
|--------------------|---------|----------|---------|----------|------------|---------------|----------------|
| 0.00               | -0.014  | 0.018    | -0.097  | 0.008    | -0.017     | 0.0210        | 0.0831         |
| 0.10               | 0.115   | 0.020    | -0.097  | 0.053    | 0.007      | 0.0211        | 0.0833         |
| 0.20  <- committed | 0.287   | 0.034    | -0.094  | 0.104    | 0.043      | 0.0210        | 0.0832         |
| 0.30               | 0.498   | 0.025    | -0.088  | 0.160    | 0.074      | 0.0210        | 0.0834         |
| 0.40               | 0.748   | 0.025    | -0.075  | 0.183    | 0.113      | 0.0210        | 0.0834         |

### purchase_threshold (per-persona policy field)

| offset              | browser | loyalist | mission | switcher | population | SKU_008 share | AD_1 attention |
|---------------------|---------|----------|---------|----------|------------|---------------|----------------|
| -0.10               | 0.241   | 0.033    | -0.099  | 0.093    | 0.036      | 0.0261        | 0.0816         |
| -0.05               | 0.286   | 0.039    | -0.108  | 0.096    | 0.042      | 0.0238        | 0.0823         |
| +0.00  <- committed | 0.287   | 0.034    | -0.094  | 0.104    | 0.043      | 0.0210        | 0.0832         |
| +0.05               | 0.340   | 0.026    | -0.047  | 0.112    | 0.056      | 0.0189        | 0.0828         |
| +0.10               | 0.341   | 0.032    | 0.095   | 0.107    | 0.094      | 0.0170        | 0.0823         |

### Range summary

| metric         | at committed constants | sweep low                    | sweep high                   | width  |
|----------------|------------------------|------------------------------|------------------------------|--------|
| browser        | 0.287                  | -0.014 (ad-pull = 0.00)      | 0.748 (ad-pull = 0.40)       | 0.762  |
| loyalist       | 0.034                  | 0.005 (gumbel-scale = 0.05)  | 0.056 (gumbel-scale = 0.25)  | 0.051  |
| mission        | -0.094                 | -0.108 (threshold = -0.05)   | 0.095 (threshold = +0.10)    | 0.203  |
| switcher       | 0.104                  | 0.008 (ad-pull = 0.00)       | 0.183 (ad-pull = 0.40)       | 0.176  |
| population     | 0.043                  | -0.017 (ad-pull = 0.00)      | 0.113 (ad-pull = 0.40)       | 0.129  |
| SKU_008 share  | 0.0210                 | 0.0149 (max-candidates = 1)  | 0.0283 (gumbel-scale = 0.40) | 0.0134 |
| AD_1 attention | 0.0832                 | 0.0813 (gumbel-scale = 0.40) | 0.0848 (max-candidates = 3)  | 0.0035 |

**Headline.** The `browser` ad-to-purchase lift is 0.287 at the committed constants (and 0.227 to
0.355 across seeds with those constants held still). Across this sweep it runs -0.014 to 0.748. The
widest single driver is the 0.2 in `ad_pull = 0.2 * ad_receptivity * brand_seen`, which alone moves
it -0.014 to 0.748.

---

## 4. The reading

### The headline lift is a range, and the range is wide

**Quote it as: browser ad-to-purchase lift ≈ 0.05 to 0.75 across the swept range, set almost
entirely by the ad coefficient `0.2` in `sim/simulator.py` and by `PURCHASE_GUMBEL_SCALE`** — and
exactly 0, by construction, if that ad coefficient is itself set to 0. The committed 0.29
(0.32 at seed 42, which is the figure in `RESULTS.md`) is one point inside that, and it is the
point our own two written constants pick out.

The two drivers behave differently and both matter:

* **The ad coefficient is close to linear in the lift.** 0.00 → 0.10 → 0.20 → 0.30 → 0.40 gives
  -0.014 → 0.115 → 0.287 → 0.498 → 0.748. There is no plateau anywhere in the defensible range, so
  the headline's magnitude is, to a first approximation, *a restatement of that constant*.
* **`PURCHASE_GUMBEL_SCALE` moves it by a factor of ten in the other direction**: 0.548 at 0.05
  down to 0.053 at 0.40, with the largest single step between 0.05 and 0.10 — the committed value
  sits on the steepest part of the curve. At seed 42 alone the endpoints are 0.619 and 0.039.

A larger Gumbel scale is more choice noise, which dilutes any systematic utility term including the
ad's; a larger ad coefficient is a louder ad. Both readings are mechanical consequences of the
formula, not discoveries.

### What *is* robust

* **The sign, given a non-zero ad coefficient.** In all 15 swept configurations with the ad
  coefficient above zero, the browser lift is positive (lowest: 0.053). The direction of the
  headline claim survives every constant we could move.
* **The ranking of personas by ad responsiveness.** browser is the most ad-responsive persona in
  every one of those 15 configurations, and switcher is second in every one of them (narrowly at
  `gumbel-scale = 0.25` and `threshold = +0.10`). loyalist and mission trade third and fourth
  place freely. So "target the browser segment, then the switcher segment" is a recommendation the
  constants do not overturn, even though "expect +32 %" is not a forecast they support.
* **The attention layer.** `AD_1` slot attention spans 0.0813 to 0.0848 across the entire sweep — a
  width of 0.0035 against a seed-to-seed spread of 0.0033 at fixed constants. The purchase
  constants do not move attention by more than run-to-run noise, which is what you would expect
  from a model where purchase reads attention and never writes it. **Every attention-side number in
  `RESULTS.md` — the heatmaps, the Ad Slot Index, the known-effect uplift — is untouched by
  anything in this file.**

### What is *not* robust

* **Any magnitude.** See above. Nothing in the purchase model has been calibrated, so no purchase
  magnitude should be quoted without its constant named alongside it.
* **`SKU_008`'s purchase share**, the KPI `scripts/eval.py` takes decision agreement on. It runs
  0.0149 to 0.0283 around a committed 0.0210 — roughly ±60 % — driven by
  `MAX_PURCHASE_CANDIDATES` (more candidates, more purchases) and `purchase_threshold` (a higher
  bar, fewer). The seed-to-seed spread at fixed constants is only 0.0014, so this movement is real
  and about ten times the noise.
* **The mission persona's lift, at any setting.** Its seed-to-seed spread at the committed
  constants is 0.273 and it changes sign between seeds (-0.179 to +0.094) — wider than its movement
  across three of the four constants. mission fixates an ad slot on few trips, so its exposed arm
  holds about 150 purchase events out of 18,000. **The mission row of the Ad-to-Purchase Lift table
  in `RESULTS.md` should be read as "not resolved at this run size", not as a negative lift.** That
  is a run-size problem, not a constants problem, and it is fixable by raising `n_synth` for that
  row.
* **The population lift's sign.** -0.017 to 0.113 across the sweep; it is negative only where the
  ad coefficient is 0.

### One thing the sweep tells us that is not about constants at all

At an ad coefficient of exactly 0 — the model with the ad's purchase effect switched off entirely —
the browser lift is **-0.014**, against a seed spread of 0.129. In other words it is
indistinguishable from zero. The exposed/unexposed split is a *selection* and not a randomisation
(`analytics/lift.py` says so at length), so a large lift could have come from who walks to the
endcap rather than from the ad. On this planogram, at this run size, it does not: with the ad term
off, the split by itself produces no lift worth reporting. The number the model reports is the ad
term, whatever we have set that term to.

---

## 5. What this sweep did not test

Stated plainly, because a table like the one above invites the reader to assume full coverage.

1. **The three utility weights** — `0.4 * brand_affinity`, `0.25 * (1 - price_norm) *
   price_sensitivity`, `0.15 * promo * promo_sensitivity` — are literals inside `run()` and cannot
   be reached without editing `sim/simulator.py`. The policy fields they multiply also feed the
   attention layer, so scaling those instead would be a different and confounded experiment. These
   are as unfitted as everything above and their sensitivity is **unmeasured**.
2. **The attention layer's own constants** — `DEFAULT_WEIGHTS` in `sim/saliency.py`, the softmax
   temperature 0.15, the relevance blend `0.5 / 0.3 / 0.1 / 0.1` — are out of scope here. This file
   is about the purchase model only.
3. **Only variant `A`, and only within-run lift.** Every number above is one variant split into its
   exposed and unexposed shoppers. The sweep says nothing about whether the *ranking* of variants —
   which is what `decision_agreement` and `analytics/optimizer.py` actually recommend on — is
   robust to these constants. A level shift that moves both arms equally would leave a ranking
   intact; this sweep cannot tell you whether that is what happens.
4. **`between_variant_lift`** (variant `A` against the ad-free control `D`) was not swept.
5. **Nothing is validated against a real panel**, here or anywhere else in the repository. A
   sensitivity analysis says how much a model's output depends on its inputs. It cannot say whether
   the model is right. `docs/METHODOLOGY.md` §0 is still the governing statement.
