# Demo video — script

Narration for the eight shots in [`shotlist.md`](shotlist.md). Target **4:55**.

**Status: not recorded.** There is no MP4, no SRT and no GIF in this repository.

Three rules that shape every line below.

1. **Live segments are one take.** Six of the eight shots are live, and each is recorded
   continuously with the wall clock visible. Retakes are free; internal cuts are not.
2. **Nothing is claimed that has not been measured.** The real panel does not exist. Any sentence
   of the form "the synthetic panel matches real shoppers at X" is unsayable in this cut, and the
   script says so out loud rather than steering around it.
3. **Every number spoken is on screen at the moment it is spoken.** Where this script quotes a
   figure, it was read off a running instance — but read the screen, not this file.

Timings are the outer edge of each shot. Speak under them; the live shots need slack.

> **Before recording, read the two warnings at the top of `shotlist.md`.** The LLM key currently
> returns 401, which changes one line in shot 6; and the clip in shot 3 is a rendering, not
> footage, which changes several.

---

## Shot 1 — the problem · 0:00–0:15 · slides

> Testing a shelf change for real means building the shelf. By the brief's own estimate a physical
> test store runs into six figures and takes weeks — that number is theirs, not ours. A survey is
> cheap, and it measures what people *say* they would do. And the attention vendors who sell you a
> predictive heatmap stop exactly where the question gets interesting: they tell you what gets
> *seen*. They do not tell you what gets *bought*.
>
> So we built a shelf you can shop in a browser, and a panel of AI shoppers that shops the same
> shelf.

## Shot 2 — what exists · 0:15–0:40 · LIVE · `#/home`

*(Operator window. The collection panel is the first thing on the page.)*

> Before anything else, this is the state of the project, live, on the screen that runs it.
>
> *(Point at the figures.)* **Zero accepted.** One person has shopped a recorded session, and it
> was rejected — the reason is right there: **too short**. A session has to run forty-five seconds
> across at least two bays with at least one interaction, and that one was twenty-nine seconds. It
> is not in the committed corpus, so the real panel is empty.
>
> I am going to show you a working synthetic panel, a store two panels can shop identically, and a
> pre-registration mechanism I think is the interesting part. **What I am not going to show you is
> a comparison between real and synthetic shoppers, because that comparison does not exist yet.**
> This screen will keep saying zero until sixty people have sat down, and everywhere a real number
> would go, this project prints "not yet collected" rather than a zero.
>
> *(Scroll the destination cards once.)* Eight screens. Here is where the next four minutes go.

## Shot 3 — a shelf, read from video · 0:40–1:10 · LIVE · `#/vision`

> A store has to come from somewhere. Point a camera at a shelf bay, front-on, and drop the clip
> here.
>
> *(Choose `data/vision/demo_aisle.mp4`. It returns in about two seconds.)*
>
> Eight frames sampled at two per second. Five shelves. Eight product facings. It found the shelf
> edges, segmented each shelf into facings by colour, and agreed them across frames.
>
> And it is right — bay one of our seed planogram has exactly five shelves and exactly eight
> filled slots. **Which is also the caveat, and I would rather give it to you than have you work
> it out:** this clip is a *rendering* of that planogram. We have no footage of a real aisle. Even
> lighting, no perspective, no occlusion, nobody's arm in the way. What this shows is that the
> pipeline works end to end on a real video file. It shows nothing about accuracy on a real shelf.
>
> *(Point at the product names.)* And look at what it will not tell you. Every product comes back
> as **"unidentified product"**. Brand unknown, price zero. This is classical computer vision on a
> CPU — it reads geometry and colour, and it cannot read a label. So rather than filling those
> fields with something plausible, it writes them as unknown. A planogram full of invented brands
> would look exactly like a real one.
>
> *(Point at the confidence figures, then the caution box.)* What it does give you is a confidence
> per facing — how distinct that block was, times how many of the eight frames agreed. And it
> saved nothing. Reading a video is not committing a store.

## Shot 4 — the store · 1:10–1:45 · LIVE, one take

> This is the store both panels shop. Three bays, five shelves, twenty-four products, three ad
> slots — one planogram document, resolved on the server, served to both panels, so the aisle is
> provably identical.
>
> *(Arrow key.)* The camera moves between fixed shelf stations and then stops. No free roam, and
> that is a measurement decision, not a shortcut. A webcam eye tracker gives you a point on a
> *screen*. Turning that into a point on a *shelf* means knowing where the shelf was on screen at
> that instant — which, with a camera in motion, changes every frame and compounds an error that
> is already several degrees. Fixed camera, one rectangle per slot, gaze that can actually be
> attributed.
>
> *(Hover, pick up, add to cart.)* Hovering logs dwell. Picking up and adding to cart are
> interactions, and they carry real weight in the attention score, because touching a pack is a
> much less ambiguous signal than looking near it.
>
> *(Point at the HUD.)* And the store says what it is measuring you with — right now, "mouse
> position stands in for gaze", because I have no camera on.
>
> *(Point at the empty eye-level slot.)* This position is empty. A real slot object, null SKU,
> zero facings. Every bay has one free at eye level on purpose, so "move this product to eye
> level" is a one-line patch instead of a rebuild. That is the change we make in a moment.
>
> One disclosure: I opened this window with a developer flag that skips consent. It records
> consent as *false*, which is the truth, and the gate rejects it. It is not a shopper. The next
> one is.

## Shot 5 — a person, against a locked prediction · 1:45–2:50 · LIVE, one take

*(Real capture flow: consent → intake → camera check.)*

> Consent first — it is the first question and the first rejection reason. Three intake questions.
> Then the camera check, and here I choose **"Continue without the camera"**, which puts this
> session in cursor-only mode. Real consent, real session, no eye tracker.
>
> *(Store opens. Cut to spectator window.)*
>
> Look at the badge in the corner **before I touch anything**. That is the SHA-256 prefix of the
> synthetic prediction for this variant, and the time it was written. It was written by
> `POST /sessions`, before the session row existed. The events endpoint and the ingest socket both
> refuse a session that has no lock, so there is no path by which a single event could be recorded
> before that commitment. The clock in the corner runs the whole time.
>
> *(Shop for 45+ seconds across two stations, at least one interaction, hovering pack to pack.)*
>
> Forty-five seconds, two bays, one interaction — that is the gate, and you saw what happens when
> a session misses it.
>
> The heatmap on the left is building from what I am doing right now. The one on the right is the
> locked prediction; it has not moved and it cannot.
>
> One thing on this screen is deliberately *not* working, and I would rather point at it than let
> you notice later. The gaze trail is empty, because this is a cursor-only session and there is no
> gaze to draw.
>
> The agreement meter does come on, and I want to be exact about what it counts. It waits for
> fifteen pieces of evidence from whichever channel this session actually has — fixations in a
> webcam session, cursor dwells in this one — and until it has them it refuses to print a number
> it has not earned. So the correlation you are about to see is against a cursor proxy for
> attention, not against gaze. That is a weaker claim than we would make with a webcam panel, and
> it is the honest one here.

*If the webcam variant was recorded instead, replace the last two paragraphs with:*

> The gaze trail is drawing from the webcam. Nothing but x, y, a derived confidence and a
> timestamp ever leaves this laptop — the video element is off, prediction points are off, and
> WebGazer's "remember the face model" setting is explicitly disabled. The HUD is showing the
> calibration error in pixels and as a share of screen width, next to a count of gaze samples
> actually delivered, so if the camera dies mid-session you can see it happen. And the meter is
> counting fixations rather than cursor dwells, so the correlation is against where I actually
> looked — against a prediction that was hashed before I sat down.

## Shot 6 — the model, and a synthetic shopper · 2:50–3:30 · LIVE

### 6a — `#/ai`

> So where is the AI in this?
>
> *(Point at the model name.)* Here. This is the model that is configured, and whether a call can
> be made at all.
>
> *(Prompt panel, then policy.)* This is the exact instruction we send it — not a copy, the string
> the code actually sends, fetched from the server. And this is what it sent back: eleven numbers
> that become a shopper. Goal categories, brand affinity, price sensitivity, how long they will
> stay, how likely they are to buy.

*Option A — the key works:*

> *(Press "Ask the model again".)* And we can ask it again, now. There is the fresh answer beside
> the one being simulated, with the fields that moved highlighted. **Note what it did not do: it
> did not adopt it.** Those cached policies are pre-registered inputs — every prediction lock is
> hashed against the simulation they drive — so a button that quietly rewrote one would invalidate
> the evidence. It writes to a preview file and shows you the difference.

*Option B — the key is dead and you are recording it:*

> *(Press "Ask the model again".)* And it fails — the key on this machine has expired, and the
> panel says exactly that: HTTP 401, check `LLM_API_KEY`. That is the screen doing its job. The
> single most useless thing this page could say is "AI unavailable" with no cause.

*Option C — deliberately offline:*

> *(Point at the disabled button and the reason.)* The button is off, and the panel says why: this
> instance is running offline, serving the cached policies and traces, and it says which setting
> turns live calls back on. It will not pretend it could call a model it cannot reach.

> *(Scroll to the traces. Pick `mission`, shopper 1.)*
>
> And this is the model actually shopping. Eighty trips, four personas, nearly a thousand turns —
> one model call each — committed to disk. *(Read one aloud.)* "Need chips; this one is cheap and
> on promotion." That is a real sentence from a real call.
>
> **One distinction, because it is the one that gets misheard.** Every heatmap in this video comes
> from a numpy simulator, not from a language model. The model wrote the policies and it wrote
> these traces. It did not write the heatmap, and we are not going to imply that it did.

### 6b — `#/panel`

> *(Press Play.)*
>
> And here is that trip on the shelf you just watched me shop. There is the bay it is standing at.
> There is the product it is looking at — named, because a shelf position called `B1S3P2` tells
> nobody anything. There is the reason it gave. And the cart fills.
>
> That is the synthetic half of the shot before this one. Same shelf, same slots, no person.

## Shot 7 — what-if, then the recommendation · 3:30–4:15 · LIVE

### 7a — `#/whatif`

> Now the part a planner would use. Take the focal product off the bottom shelf and put it at eye
> level.
>
> *(Change the control. Read the on-screen value.)* Ten thousand synthetic shoppers per persona,
> four personas, re-simulated — in the number on the screen. Milliseconds, not weeks.
>
> The heatmap redraws and the lift bars break it out by persona, which matters, because they do
> not react the same way. The mission shopper walks a short path to a category and mostly does not
> care. The browser explores, and does.
>
> This effect is the one we chose deliberately: a product moving from the bottom shelf to eye
> level is one of the few things in shopper research nobody argues about. If our pipeline could
> not recover it, nothing else it said would be worth reading.

### 7b — `#/optimize`

> A what-if answers a question you already thought to ask. This one searches.
>
> *(Enter `SKU_008`, run.)* Thirteen configurations — every ad slot against every creative, plus
> the focal product at every shelf level — each a full ten-thousand-shopper simulation, scored on
> **purchase** lift rather than attention. Two seconds.
>
> Today's planogram comes **fifth of thirteen**.
>
> Now read this box, because it is the honest half. **"This order is not settled."** And it names
> the placements it is not actually ranked against.
>
> That is a stronger statement than "the ranking is noisy". It is saying the order depends on how
> many shoppers you simulate: at ten thousand the winner is the shelf talker; at fifty thousand it
> is a shelf move for the focal product, and today's placement has climbed to second. It is a
> run-size artefact, not a close call — and adding seeds cannot fix it, because the range is a
> minimum and a maximum and can only widen.
>
> So I am not going to stand here and tell you to move the creative. What settles, two hundred and
> fifty thousand shoppers deep, is something else entirely: move the focal product to the top
> shelf. No ad placement beats where it is now at any size we can afford to run.
>
> A tool that printed the ten-thousand-shopper answer as a recommendation would be easier to sell
> and worse to trust.

## Shot 8 — the honesty panel, and what is missing · 4:15–4:55 · terminal + slides

*(Run `make eval`, scroll `RESULTS.md`.)*

> This file is regenerated from committed evidence and cannot be edited by hand. **Real panel, n
> equals zero accepted.** Every real-versus-synthetic cell reads "not yet collected" — which is
> what the evaluation script prints instead of a zero, because a table of zeroes reads as a
> measurement. Same reason these three figures were **not drawn**: an axis of zero-height bars
> would read as a measured zero, so the script names them and the reason instead.
>
> *(Pre-registration block.)* And this is my favourite line in the file. One prediction lock
> exists. Its hash recomputes and matches. And **zero locks are verified to predate their
> session's first event** — because that session was rejected and is not in the corpus, so there
> is nothing to check it against. The report will not round that off.
>
> There is a story behind that line worth thirty seconds. When we first tried to file a real
> session, the check failed on a session whose ordering was *correct*. The evaluation script was
> reconstructing when the first event happened from the browser's clock, and the browser's clock
> starts before the round trip to the server. Every honest session would have failed. The server
> now stamps that moment itself, so both timestamps come from the same clock. **That bug is why
> this file has said "not yet collected" since the beginning**, and it is fixed.
>
> *(Known effect table.)* Here is the effect from the last shot, in the report: the synthetic panel
> moves the focal product's attention from 0.0267 to 0.0497 — an uplift of 0.86 — at eye level.
> The real row is empty, so the "same direction" flag is undefined. That is a check with one side.
> It is not a validation.
>
> *(Noise-ceiling slide.)*
>
> And this is the number we would report against, once the panel exists. Split the real panel in
> half two hundred times and measure how well it agrees with *itself*. If real shoppers only agree
> with each other at 0.65, a synthetic correlation of 0.6 is close to everything the data can
> support. If they agree at 0.95, the same 0.6 is poor. Without that denominator an accuracy
> number is not interpretable — which is also why we will never claim to be "more accurate than
> humans". There is no third thing to be accurate about.
>
> *(What is not built slide.)*
>
> And here is what is missing. There is **no collected panel** — that is sixty people and an
> afternoon, and it is the only thing left that code cannot supply. The video pipeline reads
> geometry and colour but **not product identities**, which needs a detection model and a GPU. We
> have **no footage of a real aisle** — the clip you saw was a rendering. And the store shell is
> procedural: one CC0 prop is rendered in the aisle, not a modelled interior.
>
> What is built is a pre-registered experimental design with the evidence checks wired into the
> build, a synthetic panel that recovers the effect it should, a language model whose work you can
> read on screen, and a store two panels can shop identically.
>
> *(Repo URL and QR.)*

---

## Lines that must not be said

Kept here so a retake does not drift into them.

| Do not say | Because |
|---|---|
| "The synthetic panel matches real shoppers" / "…is 87 % accurate" | Nothing has been compared. There is no real panel. |
| "More accurate than a human panel" | Not a coherent claim — see shot 8 and METHODOLOGY §8. |
| "Our AI personas reasoned about this shelf" *(over a heatmap)* | The heatmaps are numpy. The model wrote the policies and the traces, and shot 6 shows both — say it there, over the traces, where it is true. |
| "We scanned a real aisle" / "this is a phone video of a shop" | The only clip is `make_vision_fixture.py`'s rendering of the seed planogram. No real footage exists. |
| "The system identified these products from the video" | It did not. Every product reads `unidentified product N`; brand, name, price and promotion are not observable from video and are written as unknown. |
| "And now that shelf is in the system" *(after shot 3)* | `#/vision` saves nothing, and says so on screen. |
| "The optimizer recommends moving the creative" | At the run size on screen the order is explicitly unsettled. The claim that settles is a SKU move at 250k. |
| "Validated" / "proven" | Reserve both words for after the panel is collected. |
| Any number not visible on screen at the moment it is spoken | The whole point of the live takes. |

## Lines that used to be here and are now false

Removed from the previous cut. Listed so an older take is not spliced in by mistake.

| Old line | Why it is gone |
|---|---|
| "There are no LLM persona traces, because there is no key" | There are **80 trips and 973 turns** in `data/cache/traces/`, from `deepseek-v4-pro:cloud`. Shot 6 reads one aloud. |
| "Video-to-planogram was dropped inside its own four-hour timebox" | The pipeline is built and shot 3 runs it. What was dropped is the *detection model*, and with it product identities. |
| "There is no CI" | `.github/workflows/ci.yml` runs both suites and fails if `RESULTS.md` moves a byte. |
| "The sample-3D-model requirement is not met" | `data/models/WaterBottle.glb`, CC0, is committed and rendered in the aisle. |
| "Prediction locks found: zero" | It reads **1** now. The interesting line is the one below it — zero verified to predate. |
| "The personas were designed by an LLM" *(as a forbidden line)* | Still forbidden for the **committed policies**, which were hand-written — but the generator now runs from `#/ai`, so say "the generator is live and you can watch it" rather than implying the policies came from it. |
