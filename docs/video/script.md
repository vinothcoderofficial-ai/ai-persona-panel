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
> *(Point at the figures.)* **One accepted, zero rejected.** One person has shopped a recorded
> session and it passed the gate — twenty-nine seconds, three bays, three products carted, checked
> out.
>
> **And I want to tell you why it passed, because it did not at first.** The gate used to require
> forty-five seconds, and this session was thrown out for being sixteen seconds short. Then we
> looked at who that rule removes: a shopper with a list, who knows the brand, is *done* in half a
> minute. A duration floor does not filter noise, it filters out the mission archetype — and
> mission is one of the four personas we are validating against. So the rule now asks whether
> enough shelf was actually seen, and this session cleared it at exactly the threshold: six slots,
> minimum six.
>
> That is a rule changed *after* seeing the data it applied to, which is the most criticisable
> thing in this project, so it is written down in the methodology with the commit that did it
> rather than left for you to find.
>
> One accepted session is not a panel. **What I am not going to show you is a comparison between
> real and synthetic shoppers, because that comparison does not exist yet** — the corpus needs
> sixty, and everywhere a real number would go, this project prints "not yet collected" rather
> than a zero.
>
> *(Scroll the destination cards once.)* Eight screens. Here is where the next four minutes go.

## Shot 3 — a shelf, read from video · 0:40–1:10 · LIVE · `#/vision`

> A store has to come from somewhere. Point a camera at a shelf bay, front-on, and drop the clip
> here.
>
> *(Choose `data/vision/demo_aisle.mp4`. It returns in three or four seconds — time it on the
> recording laptop first, it is CPU-bound and nothing about the read is cached.)*
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
> per facing — how distinct that block was, times how many of the eight frames agreed. And it has
> saved nothing yet. Reading a video is not committing a store.
>
> *(Type into two or three of the label rows — a category, a brand, a price. Do not fill all
> eight on camera; do two and say the rest are the same.)* So here is the division of labour. The
> camera reads the geometry — which shelf, how wide, what colour, how many facings — and that is
> the part a planogram file usually has wrong, because somebody reset the shelf in March and
> nobody updated the spreadsheet. It does **not** read brands, prices or signage, and it refuses
> to guess them. A retailer already knows those. They are eight rows out of their own system.
>
> *(Point at the advertising panel.)* And the same division of labour for the ad. The camera found
> no signage and the pipeline will not invent any — putting a poster on a shelf nobody filmed one
> on would fabricate the exact thing an ad test measures. So you tell it: this brand, on that
> shelf. It is recorded as operator-placed, in the variant's own name.
>
> *(Click **keep this reading**, then follow the link into the store.)* And now it is a shelf you
> can walk into, a shelf four synthetic personas will shop, and a shelf that can carry an ad
> lift — a hundred and eighty-five exposed purchases on ten thousand shoppers, off a clip, with
> nothing hand-edited. *(That figure was measured on the sixty-second version of this same
> rendering, not on the four-second one you are watching. Say "we measured" rather than "you are
> seeing", or drop the number and say "an ad lift".)*
> Before those fields were filled, three of the four personas never moved: every category read
> "unknown", and none of them shop a category called unknown.

**If shot 3 runs long, this is the cut.** The labelling beat is ~15 s and it is the beat that
makes the chain end-to-end, so cut the extended-cut material at the end of the file first. What
must not be cut is the "rendering, not footage" caveat above it.

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
> *(Hover at least six different packs across two stations, with at least one pickup or
> add-to-cart. Cover the shelf rather than watching the clock; there is no time requirement.)*
>
> Six different products, two bays, one interaction — that is the whole gate, and you saw in the
> first minute what happens when a session misses it. Note what is *not* in that list: how long I
> took. The clock in the corner is evidence of ordering, not a threshold.
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
> the code actually sends, fetched from the server. And beside it, the eleven numbers that become
> a shopper: goal categories, brand affinity, price sensitivity, how long they will stay, how
> likely they are to buy. **Those four policy files were written by hand**, and the screen says so
> — the generator is built, it has never authored the ones we simulate, and we are not going to
> imply it did.

*Option A — the key works:*

> *(Press "Ask the model again".)* And we can put that prompt to the model right now. There is its
> answer beside the one being simulated, with the fields that moved highlighted. **Note what it did
> not do: it
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
> from a numpy simulator, not from a language model. The model wrote these traces. It did not write
> the heatmap and it did not write the policies, and we are not going to imply that it did either.

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
> **purchase** lift rather than attention. *(Read the time off the meta line; do not quote a number
> from this file.)*
>
> The winner is the bay-one shelf talker, at **plus two and a half per cent**. Today's planogram
> comes **fourth of thirteen**, at **plus nought point nine**.
>
> Now read this box, because it is the honest half. **"This order is not settled."** It names the
> four placements the top pick is not actually ranked against — their seed spreads overlap the
> leader's —
> and then it says the other half out loud: **no placement clears today's spread either.** So the
> screen is refusing two claims at once. It will not tell you which placement is best, and it will
> not tell you that moving beats where you are.
>
> And I want to be precise about *why*, because there is a wrong version of this sentence. This is
> not the ranking falling apart when you change the run size. The same placement leads at ten
> thousand, fifty thousand, two hundred and fifty thousand and half a million, and the spread
> around it narrows the whole way. What is unsettled is the *separation*, not the order — and more
> seeds cannot fix that, because the range is a minimum and a maximum and can only widen. Only
> more shoppers can.
>
> And they do. Off camera, at five times this run size, one row does clear today's placement: move
> the creative to the bay-one shelf talker. But that is a number I ran, not a number on this
> screen — so what I will stand here and tell you is what the screen supports, which is nothing
> yet.
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
> exists. Its hash is recomputed from the file and matches. And **zero locks are verified to
> predate their session's first event** — because the ordering check needs the session's events,
> and that session has never been exported into the committed corpus. The session was accepted;
> it is simply not in `data/sessions/anon/`, which is why the panel above still reads n equals
> zero. Two different zeroes, and the report will not round either off.
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
| "Our AI personas reasoned about this shelf" *(over a heatmap)* | The heatmaps are numpy. The model wrote the traces and nothing else — shot 6 shows them, so say it there, over the traces, where it is true. |
| "The model wrote these policies" *(over the policy panel in shot 6a)* | The four committed policies were **hand-written in S2**. The generator is built and the button runs it live, but it has never authored the ones being simulated — METHODOLOGY §5 and §12.8, and the panel itself, all say so. |
| "We scanned a real aisle" / "this is a phone video of a shop" | The only clip is `make_vision_fixture.py`'s rendering of the seed planogram. No real footage exists. |
| "The system identified these products from the video" | It did not. Every product reads `unidentified product N`; brand, name, price and promotion are not observable from video and are written as unknown. |
| "And now that shelf is in the system" *(immediately after the upload in shot 3)* | Reading a clip still saves nothing — the screen says so until you act. There is now a deliberate **keep this reading** step, but it comes *after* you have typed in the categories, brands and prices the camera cannot read. Say it in that order, or the shot claims the pipeline identified products it did not. |
| "The camera worked out what these products are" | It read geometry and colour. A **person** typed the identities in, on camera, and the payload marks them operator-supplied. That division of labour is the point of the shot, not an apology for it. |
| "The optimizer recommends moving the creative" | **At the run size on screen the order is explicitly unsettled** — say that, not the recommendation. It does settle off-camera, and it settles *in favour of* the ad move (`AD_1` to the bay-1 shelf talker): the earliest rung that clears today's placement is **50,000**, +2.0 % against the current +1.0 % with the seed ranges not overlapping, and it holds at 250k and 500k. That is 5× the run size on screen at the earliest, so it is a footnote you may answer a question with, never a line you narrate over a 10k screen. |
| "The ranking is a run-size artefact" / "at fifty thousand a different placement wins" | True of the **within-run** estimator, which is not what the screen ranks on. On the between-arm default `AD_1 on B1_TALKER` leads at 10k, 50k, 250k and 500k and the spread narrows the whole way. Say the order is *unseparated*, not that it *changes*. |
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
| "Forty-five seconds, two bays, one interaction — that is the gate" *(shot 5)* | The duration floor was removed, and shot 2 of this same script explains why. The gate is **six distinct slots, two bays, one interaction**, and nothing about elapsed time. Saying it in shot 5 contradicted shot 5's own screen. |
| "Today's planogram comes fifth of thirteen" *(shot 7b)* | Fifth and +12.7 % were the **within-run** estimator's numbers. The screen ranks on the between-arm lift and reads **fourth of thirteen at +0.9 %**, with the leader at +2.5 %. |
| "It is a run-size artefact … at fifty thousand it is a shelf move for the focal product" *(shot 7b)* | Also the old estimator. The leader is the same at all four rungs of the ladder and the spread narrows; what is unresolved is the separation, not the order. |
| "What settles at two hundred and fifty thousand is a SKU move to the top shelf" *(shot 7b)* | Reversed with the estimator. The pair that settles is the **ad move**, and it settles from **50,000** up. |
| "Two seconds" *(the optimizer, shot 7b)* | Never re-measured after the default changed. A cold ranking is 7–11 s on the machine this was written on and a repeat of the identical one is ~50 ms — read the meta line on screen instead of quoting either. |
