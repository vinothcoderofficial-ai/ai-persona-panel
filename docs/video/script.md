# Demo video — script

Narration for the seven shots in [`shotlist.md`](shotlist.md). Target **4:35**.

**Status: not recorded.** There is no MP4, no SRT and no GIF in this repository.

Two rules that shape every line below.

1. **Live segments are one take.** Shots 3, 4 and 5 are recorded continuously with the wall clock
   visible. Retakes are free; internal cuts are not.
2. **Nothing is claimed that has not been measured.** The real panel does not exist. Any sentence
   of the form "the synthetic panel matches real shoppers at X" is unsayable in this cut, and the
   script says so out loud rather than steering around it.

Timings are the outer edge of each shot. Speak under them; the live shots need slack.

---

## Shot 1 — the problem · 0:00–0:20 · slides

> Testing a shelf change for real means building the shelf. By the brief's own estimate a physical
> test store runs into six figures and takes weeks — that number is theirs, not ours. A survey is
> cheap and fast, and it measures what people *say* they would do. And the attention vendors who
> sell you a predictive heatmap stop exactly where the question gets interesting — they will tell
> you what gets *seen*. They will not tell you what gets *bought*.
>
> So we built a shelf you can shop in a browser, and a panel of AI shoppers that shops the same
> shelf.

## Shot 2 — the pipeline, and what actually exists · 0:20–0:45 · slides

*(Architecture diagram on screen.)*

> One planogram, resolved on the server, served to both panels — so the aisle is provably
> identical. Real people shop it with webcam gaze where the calibration allows and with cursor and
> clicks always. Four AI personas shop it too. Before a real shopper is allowed to record a single
> event, we simulate their variant, hash the prediction, and write it to disk. The prediction is
> fixed before the behaviour exists.
>
> One thing to get straight before anything else. **The synthetic half of this study is built and
> running. The real panel has not been collected.** Nobody has shopped a recorded session yet.
> Everywhere a real number would go, this repository prints "not yet collected" — never a zero.
> I will show you that, and I will show you the code that enforces it.

## Shot 3 — the store · 0:45–1:25 · LIVE, one take

*(Planogram JSON left, rendered aisle right.)*

> This is the planogram: three bays, five shelves, twenty-four products, three ad slots. It is
> plain JSON with metric dimensions, and the renderer is just one consumer of it.
>
> *(Arrow key.)* The camera moves between fixed shelf stations and then stops. There is no free
> roam, and that is a measurement decision, not a shortcut. A webcam eye tracker gives you a
> point on a *screen*. Turning that into a point on a *shelf* means knowing where the shelf was on
> screen at that instant — which, with a camera in motion, changes every frame and compounds an
> error that is already several degrees. Fixed camera, one rectangle per slot, gaze that can
> actually be attributed.
>
> *(Hover, pick up, add to cart.)* Hovering logs dwell. Picking up and adding to cart are
> interactions, and they carry real weight in the attention score, because touching a pack is a
> much less ambiguous signal than looking near it.
>
> *(Point at the empty eye-level slot.)* And this position is empty — a real slot object with a
> null SKU and zero facings. Every bay has one free at eye level, on purpose, so that "move this
> product to eye level" is a one-line patch instead of a rebuild. That is the effect we test in a
> moment.
>
> One disclosure while we are here: I opened this window with a developer flag that skips the
> consent flow. It records consent as *false*, which is the truth, and the session gate rejects
> it. It is not a shopper. The next shot is.

## Shot 4 — a live session against a locked prediction · 1:25–2:35 · LIVE, one take

*(Real capture flow: consent → intake → camera check.)*

> Consent first — it is the first question and the first rejection reason. Three intake questions.
> Then the camera check, and here I am going to choose **"Continue without the camera"**, which
> puts this session in cursor-only mode. Real consent, real session, no eye tracker.
>
> *(Store opens. Cut to spectator window.)*
>
> Look at the badge in the corner **before I touch anything**. That is the SHA-256 prefix of the
> synthetic prediction for this variant, and the timestamp it was written. It was written by
> `POST /sessions`, before the session row existed. The events endpoint and the ingest socket both
> refuse a session that has no lock, so there is no path by which a single event could be recorded
> before that commitment. The clock in the corner is running the whole time.
>
> *(Shop for 45+ seconds across two stations, at least one interaction.)*
>
> The heatmap on the left is building from what I am doing right now. The one on the right is the
> locked prediction; it has not moved and it cannot.
>
> One thing on this screen is deliberately *not* working, and I would rather point at it than let
> you notice later. The gaze trail is empty, because this is a cursor-only session and there is no
> gaze to draw.
>
> The agreement meter does come on, and I want to be exact about what it is counting. It waits for
> fifteen pieces of evidence from whichever channel this session actually has — fixations in a
> webcam session, cursor dwells in this one — and until it has them it refuses to print a number it
> has not earned. So the correlation you are about to see is against a cursor proxy for attention,
> not against gaze. That is a weaker claim than the one we would make with a webcam panel, and it
> is the honest one to make here.

*(End of the one-take segment, and the end of shot 4 — there is no cutaway. The `?fake=1` beat
that used to follow existed only because the meter stayed grey for the whole of a cursor-only
take; it comes on during the take itself now, so the beat has nothing left to show. `shotlist.md`
carries the reasoning, and the rule that still governs that stream.)*

*If the webcam variant was recorded instead, replace the last two paragraphs above with:*

> The gaze trail is drawing from the webcam. Nothing but x, y, a derived confidence and a
> timestamp ever leaves this laptop — the video element is off, prediction points are off, and
> WebGazer's "remember the face model" setting is explicitly disabled. And the meter is counting
> fixations rather than cursor dwells — fifteen of them, the same threshold — so the correlation
> it is showing is against where I actually looked, and against a prediction that was hashed
> before I sat down.

## Shot 5 — what-if · 2:35–3:15 · LIVE

> Now the part a planner would use. *(Open `#/whatif`.)* Take the focal product off the bottom
> shelf and put it at eye level.
>
> *(Change the control. Read the on-screen value.)* That is ten thousand synthetic shoppers per
> persona, four personas, re-simulated — in the number on the screen. Milliseconds, not weeks. The
> label says server compute, because that is what it measures.
>
> The heatmap redraws and the lift bars break it out by persona — which matters, because they do
> not react the same way. The mission shopper walks a short path to a category and mostly does not
> care. The browser explores and does.
>
> *(Second change: move the creative to the bay-1 shelf talker.)* And the same for ad placement:
> move the creative, re-run, read the lift.
>
> This effect is the one we chose deliberately, because a product moving from the bottom shelf to
> eye level is one of the few things in shopper research nobody argues about. If our pipeline
> could not recover it, nothing else it said would be worth reading.

## Shot 6 — the recommendation · 3:15–3:45 · LIVE

*(Terminal. Run `python scripts/optimize.py --creative AD_1 --focal-sku SKU_008`.)*

> A what-if answers a question you already thought to ask. This searches. Thirteen
> configurations — every ad slot against every creative, plus the focal product at every shelf
> level — each one a full ten-thousand-shopper simulation, scored on purchase lift rather than
> attention. Six seconds.
>
> Today's planogram comes **fifth of thirteen**.
>
> Now read these two lines, because they are the honest half. *The order is not resolved* — and
> *more seeds will not settle it.* That second line matters more than it looks. It is not saying
> the answer is noisy. It is saying the ranking depends on how many shoppers you simulate: at ten
> thousand the winner is the shelf talker, at fifty thousand it is a shelf move for the focal
> product, and today's placement has climbed from fifth to second.
>
> So I am not going to stand here and tell you to move the creative. What settles, when we run it
> two hundred and fifty thousand shoppers deep, is something else entirely — move the focal
> product to the top shelf. No ad placement beats where it is now at any size we can afford to run.
>
> That is the recommendation engine working. A tool that printed the ten-thousand-shopper answer
> as a recommendation would be easier to sell and worse to trust.

*(Re-run with the commercial flags.)*

> Now the same ranking with money on it. And notice what the command made me do: it refused to
> print a single rand until I supplied all six commercial inputs — baseline volume, margin, stores,
> weeks, currency, and a basis line saying where they came from. Mine says ILLUSTRATIVE ONLY,
> because this project has no client's margin and no store traffic, and there is nowhere in this
> repository those numbers could have come from.
>
> So the lift column is measured. The money column is that lift multiplied by numbers I typed in.
> The footer says exactly that, every time it prints. Swap in a real planner's numbers and the
> ranking does not move — only the scale does.

---

## Shot 7 — the honesty panel · 3:45–4:25 · terminal + slides

*(Run `make eval`, scroll `RESULTS.md`.)*

> This file is regenerated from committed evidence and it cannot be edited by hand. Here is the
> panel section: **real panel, n equals zero accepted**. Prediction locks found: zero. Every
> real-versus-synthetic cell reads "not yet collected".
>
> That phrase is not decoration. It is what the evaluation script prints instead of a zero,
> because a table of zeroes reads as a measurement. Same reason these three figures were **not
> drawn** — an axis of zero-height bars would read as a measured zero, so the script names them
> and the reason instead of rendering them.
>
> *(Known effect table.)* Here is the effect from the last shot, in the report: the synthetic panel
> moves the focal product's attention from 0.0267 to 0.0497 — an uplift of 0.86 — when it goes to
> eye level. The real row is empty, so the "same direction" flag is undefined. That is a check
> with one side. It is not a validation.
>
> *(Noise-ceiling slide.)*
>
> And this is the number we would report against, once the panel exists. Split the real panel in
> half two hundred times, and measure how well it agrees with *itself*. If real shoppers only
> agree with each other at 0.65, then a synthetic correlation of 0.6 is close to everything the
> data can support. If they agree at 0.95, the same 0.6 is poor. Without that denominator an
> accuracy number is not interpretable — which is also why we will never claim to be "more
> accurate than humans". There is no third thing to be accurate about. In this study the real
> panel *is* the target, so the ratio is capped at one, and a value above one would be a warning
> that something is over-fitted, not a result.
>
> *(Limitations slide.)*
>
> Everything else is written down: twelve limitations in the methodology document, including the
> ones that are inconvenient. Our lift metric is not monotonic in ad receptivity for two of the
> four personas on this aisle, and we say why. The webcam's confidence signal is derived and
> effectively binary. The persona policies in this repository were written by hand, not by a
> language model — the generator exists, the key does not.

## Shot 8 — value, roadmap, and what is missing · 4:25–4:55 · slides

*(Cost/time slide, marked indicative.)*

> These comparisons are indicative — they are from the brief, not measured by us, and the slide
> says so. What we *did* measure is the compute: a full population of ten thousand shoppers per
> persona in about a sixth of a second, and a what-if answer in single-digit milliseconds warm. A
> planner can ask twenty questions in the time it takes to book a meeting about asking one.
>
> *(Roadmap slide.)*
>
> The integration path is written, not gestured at: a document and working code for how census
> demographics would seed the persona population shares, and how Brand Lift questions become a
> post-shop questionnaire the personas answer. Designed and built — but no census data has been
> obtained, no Brand Lift study has been run, and the personas have not answered it yet.
>
> *(What is not built slide.)*
>
> And here is what we did not finish. There is no collected panel. There are no LLM persona traces,
> because there is no key. Video-to-planogram was dropped inside its own four-hour timebox. The
> store shell is procedural, which means the sample-3D-model requirement is not met. There is no CI.
> And the optimizer you just watched ranks placements it cannot yet separate — the seed spreads
> overlap, so the ordering is not settled.
>
> What is built is a pre-registered experimental design with the evidence checks wired into the
> build, a synthetic panel that recovers the effect it should, and a store two panels can shop
> identically. The missing piece is sixty people and an afternoon.
>
> *(Repo URL and QR.)*

---

## Lines that must not be said

Kept here so a retake does not drift into them.

| Do not say | Because |
|---|---|
| "The synthetic panel matches real shoppers" / "…is 87 % accurate" | Nothing has been compared. There is no real panel. |
| "More accurate than a human panel" | Not a coherent claim — see shot 7 and METHODOLOGY §8. |
| "Our AI personas reasoned about this shelf" *(over the simulator)* | The simulator is numpy. The LLM agents are built but have produced no traces. |
| "The personas were designed by an LLM" | The committed policies were hand-written. The generator has never run against a real model. |
| "We scanned a real aisle with a phone" | S20 was dropped. No clip was recorded. |
| "Validated" / "proven" | Reserve both words for after the panel is collected. |
| Any number not visible on screen at the moment it is spoken | The whole point of the live takes. |
