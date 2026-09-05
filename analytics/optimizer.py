"""Placement optimizer -- score every placement, rank them, name the best (PLAN S24).

PLAN section 1 calls this the third and most valuable of the project's three
outputs: attention says what gets seen, Ad-to-Purchase Lift says what the
exposure was worth, and this says **what to do about it**. It sits entirely on
top of the existing simulator and needs no new data.

Two search spaces, kept separate so a caller can search either or both:

  * `ad_placement_candidates` -- ad slots x creatives, plus the option of
    taking a creative down entirely. Each candidate places ONE creative
    exclusively on ONE slot, so "move AD_1 from the endcap to the bay 1 shelf
    talker" is a single candidate and the creative never ends up hanging in
    two places at once (which is why data/variants/C.json is two patches).
  * `sku_level_candidates` -- focal SKU x shelf level, using the destination
    rule `web/src/whatif/patches.ts` already implements: the SKU's own bay,
    the first empty slot on that bay's shelf at that level, otherwise the
    first slot on that shelf (which `resolve()` turns into a swap).

`CandidateSet.__add__` composes them. Nothing in either generator simulates
anything; `rank_candidates` does that.

"Greedy" here means exhaustive, and that is a feature
-----------------------------------------------------
PLAN section 6 says "greedy search". These spaces are single digits by single
digits -- three ad slots by two creatives, five shelf levels -- and one
`api.app.simcache.population` call at 10,000 shoppers costs about 170 ms on
the machine this was written on. So `rank_candidates` scores EVERY candidate
and sorts them. There is no heuristic, no beam, no hill climb and therefore
no local optimum to miss. A reader should not assume an approximation here:
the top pick is the best member of the space it was given, exactly.

What is approximate is the *space*, not the search. A candidate moves one
creative or one SKU; the joint space of "AD_1 here AND AD_2 there AND SKU_008
at eye level" is not enumerated, because it is the product of these spaces and
the run time grows with it. `Ranking.n_candidates` reports how many
configurations were actually simulated, so a caller who composes spaces can
see what they asked for.

The objective is a purchase metric
----------------------------------
This is the whole argument of PLAN section 6: "no attention vendor does it,
because they don't model purchase." The default objective is the advertised
brand's Ad-to-Purchase Lift, computed by `analytics.lift.synth_lift` -- the
project's one lift formula, not a second copy of it -- over the population
SimResult from `api.app.simcache.population`, which is the same call that
produces a prediction lock. An optimizer score and a locked prediction
therefore cannot disagree about what the simulator said.

`sku_purchase_share_objective` optimises a focal SKU's population purchase
share instead. Both are purchase metrics. **A ranking is meaningless without
knowing which one it was produced against**, so `Ranking.objective_name`
carries it, `summary()` prints it, and no function here returns a bare number.

The uncertainty field is NOT a confidence interval
--------------------------------------------------
S18 settled this and `docs/METHODOLOGY.md` section 12.7 records it: a committed
`SimResult` carries the two arms as normalised *shares*, and `n_runs` counts
shoppers rather than purchase events, so there is nothing per-shopper to
resample. A bootstrap built from those fields would return an interval
NARROWER than the truth, which is worse than no interval at all.

What this module reports instead is `SeedSpread`: the same candidate re-scored
at several `np.random.default_rng` seeds, and the min and max of what came
back. That is Monte Carlo run-to-run variability -- how much the answer moves
when you re-roll the dice at a fixed panel size. It is a different quantity
from sampling error and it is deliberately named, documented and printed as
what it is. PLAN section 6's example sentence prints "(CI 8-14)"; this module
prints a seed spread instead, and says so, rather than fabricating the
interval the sentence asks for.

When two candidates' seed ranges overlap, the ranking between them is not
resolved at that number of seeds. `Scored.unresolved_against` lists the
candidates a row overlaps, `Ranking.top_pick_is_resolved` answers it for the
recommendation, and `summary()` says so in words. Presenting rank 1 and rank 2
as a settled result when the spreads overlap would be exactly the false
precision the interval decision above is trying to avoid.

Two claims, and only one of them is usually available
------------------------------------------------------
"Rank 1 beats rank 2" and "this placement beats the one we are running" are
different claims with different evidence, and the second is the one a
recommendation rests on. A space whose leaders sit within noise of each other
can still contain a move that clears today's planogram outright.
`Ranking.beats_current` answers the second question by name, against the
current placement's own seed range, and `summary()` prints both answers --
including when the second one is "nothing clears it", which is a finding.

On the committed aisle the two come apart exactly this way: at
n_synth=250,000 no pair of leaders is separated, and `sku:SKU_008@top` at
+9.3% is nonetheless clear of the current placement's +7.8% at every seed.

Seeds are the wrong lever, and n_synth is the right one
-------------------------------------------------------
The obvious response to "not resolved over five seeds" is to run more seeds.
It is backwards. `low` and `high` are a min and a max, so the range can only
GROW as draws are added: two seeds separate almost any pair of candidates and
twenty separate almost none, from the same simulator and the same planogram.
Extra seeds buy a better estimate of the run-to-run variability, not less of
it, which is why `SeedSpread.n_seeds` is part of the measurement and why a
spread quoted without it says nothing.

What narrows the spread is `n_synth`. The simulator draws that many
independent shoppers, so the standard deviation of the objective across seeds
falls as 1/sqrt(n_synth) -- four times the shoppers, half the spread. It is an
expensive lever: the run time is linear in `n_synth` while the noise falls
with its square root, so halving the noise costs four times the compute.

Neither lever helps with the failure underneath both of them: a top pick that
is an artefact of the run size. Every seed a spread re-rolls is drawn at the
same `n_synth`, so a candidate that leads only because the panel is too small
to tell it from its neighbours leads at those seeds too, and its spread looks
no worse than anybody else's. `check_top_pick_stability` is the check for
that -- it re-ranks the same space at a ladder of run sizes and reports
whether one candidate won them all. `Stability.top_pick_is_stable` and
`Ranking.top_pick_is_resolved` are different questions and are answered
separately; see `Stability`.

Determinism
-----------
Same planogram, same candidates, same seeds -> identical ranking, ties
included. Ties break on `candidate_id` ascending, which is a stable documented
string (`ad:AD_1@B3_ENDCAP`, `sku:SKU_008@eye`) and not a dict order or a
generation order. Candidates whose objective is undefined sort last, after
every defined one, and are never reported as 0.

Pure: no HTTP, no file I/O of its own, no globals, no wall-clock randomness.
The one dependency that touches disk is `api.app.simcache.population`, which
loads and caches the persona documents and their policies; it is injectable as
`simulate` so a caller can score a space against anything with the same shape.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

from analytics.lift import creative_brand, sku_brands, synth_lift
from api.app import simcache
from api.app.resolve import resolve

# Shelf levels, top first -- the same order and the same vocabulary as
# schemas/planogram.schema.json and web/src/whatif/patches.ts.
LEVELS: tuple[str, ...] = ("top", "above_eye", "eye", "below_eye", "bottom")

KIND_AD_PLACEMENT = "ad_placement"
KIND_SKU_LEVEL = "sku_shelf_level"

# The candidate_id suffix for "this creative hangs nowhere". Not None and not
# the empty string: candidate ids are sorted and printed.
UNPLACED = "none"

# PLAN section 6: "scoring each with 10,000 shoppers". The seed matches the
# what-if endpoint's default so an optimizer row and a what-if of the same
# patches are the same simulation, not two draws of it.
DEFAULT_N_SYNTH = 10_000
DEFAULT_SEED = 42

# The extra seeds `SeedSpread` re-scores the top candidates at, and how many
# of them get that treatment. Four extra draws on the top five candidates is
# twenty extra simulations -- a few seconds -- which is what makes it
# affordable to report run-to-run spread on the rows anybody will read.
DEFAULT_SPREAD_SEEDS: tuple[int, ...] = (43, 44, 45, 46)
DEFAULT_SPREAD_TOP_N = 5

# The run sizes `check_top_pick_stability` re-ranks at. The first rung is
# DEFAULT_N_SYNTH so the check answers "does the number we actually print
# survive a bigger panel", and not some other question about some other size.
#
# The rungs above it are set by what Monte Carlo error costs to remove. The
# objective is a ratio whose numerator comes from the ad-EXPOSED arm, and on
# the committed aisle that arm holds roughly one purchase event in forty; the
# seed-to-seed standard deviation of the lift is about 3 points at 10,000
# shoppers and falls as 1/sqrt(n_synth), so 50,000 buys about 1.4 and 250,000
# about 0.6. Rungs closer together than a factor of five cannot show a
# reordering that is not itself noise.
#
# The top rung is 250,000 because that is the smallest size measured at which
# ANY candidate on the committed aisle clears the current placement's seed
# spread: `sku:SKU_008@top` at +9.3% against +7.8%, winning at all five seeds.
# At 10,000 through 100,000 nothing does. That one rung is what turns
# `beats_current` from an empty tuple into a claim.
#
# **This is deliberately not the default of `rank_candidates` and must not
# become one.** A 250,000-shopper rung costs about twenty-five times a
# 10,000-shopper one; the ladder is a check you run once against a
# recommendation, not the way you produce one.
DEFAULT_STABILITY_LADDER: tuple[int, ...] = (DEFAULT_N_SYNTH, 50_000, 250_000)


# ---------------------------------------------------------------------------
# The candidate space
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Candidate:
    """One configuration to score: a patch set, plus what to call it.

    `patches` are variant patches in the shape `api.app.resolve.resolve` and
    schemas/variant.schema.json both expect, so a recommendation can be pasted
    straight into POST /whatif or committed as a variant.

    A candidate that reproduces the planogram it was generated from still
    carries its patches -- "AD_1 on B3_ENDCAP" writes back the creative
    already there. That keeps a candidate self-describing rather than
    context-dependent, and it is why `rank_candidates` identifies the current
    placement by resolved *content* and not by an empty patch list.
    """

    candidate_id: str
    kind: str
    label: str
    patches: tuple[dict, ...]
    detail: dict


@dataclass(frozen=True)
class Skipped:
    """A candidate the planogram cannot express, and why.

    The focal-SKU space produces these: a bay with no shelf at some level, or
    a shelf carrying no slots, has nowhere to send the SKU. Reporting it is the
    point -- a level silently missing from a ranking reads as "we tried it and
    it was bad", which is a different and much more useful-sounding claim than
    "there was no move to try".
    """

    candidate_id: str
    kind: str
    reason: str
    detail: dict


@dataclass(frozen=True)
class CandidateSet:
    """Candidates plus the skips that came with them. `+` composes two spaces."""

    candidates: tuple[Candidate, ...] = ()
    skipped: tuple[Skipped, ...] = ()

    def __add__(self, other: "CandidateSet") -> "CandidateSet":
        if not isinstance(other, CandidateSet):
            return NotImplemented
        return CandidateSet(
            candidates=self.candidates + other.candidates,
            skipped=self.skipped + other.skipped,
        )

    def __len__(self) -> int:
        return len(self.candidates)

    def __iter__(self):
        return iter(self.candidates)


# ---------------------------------------------------------------------------
# The objective
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Objective:
    """What "best" means for one ranking.

    `score(population, resolved)` takes the population SimResult and the
    resolved planogram it came from, and returns a number or None. None means
    *undefined* -- the metric has no answer for this configuration -- and is
    never to be substituted with 0, which is a measured value.

    `name` is carried through onto `Ranking.objective_name` and printed by
    `summary()`, because a ranking without its metric is not a result.
    """

    name: str
    score: Callable[[Mapping[str, Any], Mapping[str, Any]], Optional[float]]
    format_value: Callable[[float], str]


def ad_purchase_lift_objective(creative_id: str) -> Objective:
    """Maximise the Ad-to-Purchase Lift of the brand `creative_id` advertises.

    The metric is `analytics.lift.synth_lift` -- trip-level ad exposure, the
    advertised brand's share of each arm's purchases, `(exposed - unexposed) /
    unexposed`. This module does not re-derive any of it.

    Returns None for a configuration where the lift is undefined: no exposed
    shoppers (nothing carries a creative anywhere), no unexposed shoppers, or
    an unexposed arm that bought none of the advertised brand. Those rank last
    rather than being read as a lift of zero.
    """

    def score(population: Mapping[str, Any], resolved: Mapping[str, Any]) -> Optional[float]:
        return synth_lift(
            population,
            brand_of_sku=sku_brands(resolved),
            brand=creative_brand(resolved, creative_id),
        )

    return Objective(
        name=f"ad-to-purchase lift for creative {creative_id}",
        score=score,
        format_value=lambda value: f"{value:+.1%}",
    )


def sku_purchase_share_objective(sku_id: str) -> Objective:
    """Maximise one SKU's share of the population's purchases.

    The other purchase objective: what to optimise when the question is "where
    should this product sit" rather than "where should this ad hang". Reads
    `purchase_share` off the population SimResult, which `sim.simulator`
    normalises across every SKU in the planogram.

    Raises ValueError if the SKU is not in the planogram at all. An absent key
    in `purchase_share` means "this SKU sold nothing", which is a measurement
    and reads as 0.0; an unknown sku id means the caller asked the wrong
    question and would otherwise get 0.0 for every candidate.
    """

    def score(population: Mapping[str, Any], resolved: Mapping[str, Any]) -> Optional[float]:
        if not any(sku["sku_id"] == sku_id for sku in resolved["skus"]):
            raise ValueError(f"planogram has no sku {sku_id!r}")
        return float(population["purchase_share"].get(sku_id, 0.0))

    return Objective(
        name=f"population purchase share of {sku_id}",
        score=score,
        format_value=lambda value: f"{value:.3%}",
    )


# ---------------------------------------------------------------------------
# Candidate generators
# ---------------------------------------------------------------------------


def ad_placement_candidates(
    planogram: Mapping[str, Any],
    *,
    creative_ids: Optional[Sequence[str]] = None,
    ad_slot_ids: Optional[Sequence[str]] = None,
    include_unplaced: bool = True,
) -> CandidateSet:
    """Every (ad slot, creative) pair, plus "take this creative down".

    One candidate = one creative hanging on exactly one slot. The patches
    therefore clear the creative from every OTHER slot currently carrying it
    before hanging it on the destination, so the resolved planogram never shows
    the same campaign twice.

    Hanging a creative on a slot that already carries a different one takes
    that one down -- an ad slot's `creative_id` is a single value. The
    incumbent is recorded as `detail["displaced_creative_id"]` and named in the
    label, because "best placement for AD_2" that quietly deletes AD_1 is not
    a recommendation anybody can act on.

    `creative_ids` and `ad_slot_ids` default to everything the planogram has,
    in planogram order, and the caller's order is honoured when they are given.
    An unknown id raises ValueError rather than being skipped: a typo that
    silently shrinks the search space would make the top pick a claim about a
    space nobody chose.

    Nothing here simulates anything, and the candidates say nothing about which
    is better -- `rank_candidates` and an `Objective` decide that.
    """
    slots = _ad_slot_index(planogram)
    creatives = {c["creative_id"]: c for c in planogram.get("creatives", [])}

    chosen_creatives = tuple(creatives) if creative_ids is None else tuple(creative_ids)
    for creative_id in chosen_creatives:
        if creative_id not in creatives:
            raise ValueError(f"planogram has no creative {creative_id!r}")

    chosen_slots = tuple(slots) if ad_slot_ids is None else tuple(ad_slot_ids)
    for ad_slot_id in chosen_slots:
        if ad_slot_id not in slots:
            raise ValueError(f"planogram has no ad slot {ad_slot_id!r}")

    candidates: list[Candidate] = []
    for creative_id in chosen_creatives:
        hanging_on = tuple(
            ad_slot_id for ad_slot_id, (_, ad) in slots.items()
            if ad["creative_id"] == creative_id
        )

        for ad_slot_id in chosen_slots:
            bay_id, ad = slots[ad_slot_id]
            patches = [_clear(other) for other in hanging_on if other != ad_slot_id]
            patches.append({"op": "set_ad_creative", "ad_slot_id": ad_slot_id,
                            "creative_id": creative_id})
            incumbent = ad["creative_id"]
            displaced = incumbent if incumbent not in (None, creative_id) else None
            label = f"{creative_id} on {ad_slot_id} ({ad['type']}, bay {bay_id})"
            if displaced is not None:
                label += f" - displaces {displaced}"
            candidates.append(Candidate(
                candidate_id=f"ad:{creative_id}@{ad_slot_id}",
                kind=KIND_AD_PLACEMENT,
                label=label,
                patches=tuple(patches),
                detail={
                    "creative_id": creative_id,
                    "ad_slot_id": ad_slot_id,
                    "ad_slot_type": ad["type"],
                    "bay_id": bay_id,
                    "displaced_creative_id": displaced,
                    "was_hanging_on": hanging_on,
                },
            ))

        if include_unplaced:
            candidates.append(Candidate(
                candidate_id=f"ad:{creative_id}@{UNPLACED}",
                kind=KIND_AD_PLACEMENT,
                label=f"{creative_id} unplaced (no ad slot carries it)",
                patches=tuple(_clear(ad_slot_id) for ad_slot_id in hanging_on),
                detail={
                    "creative_id": creative_id,
                    "ad_slot_id": None,
                    "ad_slot_type": None,
                    "bay_id": None,
                    "displaced_creative_id": None,
                    "was_hanging_on": hanging_on,
                },
            ))

    return CandidateSet(candidates=tuple(candidates))


def sku_level_candidates(
    planogram: Mapping[str, Any],
    sku_id: str,
    *,
    levels: Sequence[str] = LEVELS,
) -> CandidateSet:
    """The focal SKU at each shelf level, using the what-if UI's destination rule.

    `web/src/whatif/patches.ts:destinationSlotId` decides where "put this SKU
    at eye level" actually sends it, and this follows it exactly so a
    recommendation and the control a person would use to try it agree:

      1. **The SKU's own bay.** A level change that also walked the product
         across the aisle would change more than the one thing named.
      2. **The first empty slot on that bay's shelf at that level**, if there
         is one. `resolve()` moves the SKU and its facings in and leaves the
         old position empty. The seed planogram keeps one free slot per bay at
         eye and at bottom level for precisely this.
      3. **Otherwise the first slot on that shelf**, which `resolve()` turns
         into a swap: the two SKUs exchange slot and facings. A full shelf can
         only take a new product by giving one up, and that is the honest model
         of it. The demoted SKU is recorded as `detail["displaced_sku_id"]` and
         named in the label, because a swap changes two placements and a reader
         must not mistake it for one.

    The SKU's *current* level yields a `move_sku` onto its own slot -- a no-op
    `resolve()` already handles -- so "leave it where it is" is a candidate in
    the ranking rather than a missing row. Emitting a move to a different slot
    of the same shelf would quietly change its horizontal position too, which
    is the same reason the UI declines to.

    A level the bay has no shelf for, or a shelf with no slots at all, is
    returned in `CandidateSet.skipped` with a reason. Neither is an error and
    neither is a crash; both are configurations this planogram cannot express.

    Raises ValueError if the SKU is on no shelf: an unplaced product has no bay,
    so there is no space to search, and an empty result would read as "no move
    helps".
    """
    placement = _placement_of(planogram, sku_id)
    if placement is None:
        raise ValueError(f"sku {sku_id!r} is on no shelf of this planogram; there is no "
                         "bay to search for shelf levels")
    bay, shelf_now, slot_now = placement
    bay_id = bay["bay_id"]

    candidates: list[Candidate] = []
    skipped: list[Skipped] = []

    for level in levels:
        candidate_id = f"sku:{sku_id}@{level}"
        base_detail = {"sku_id": sku_id, "level": level, "bay_id": bay_id,
                       "from_slot_id": slot_now["slot_id"]}

        shelf = next((s for s in bay["shelves"] if s["level"] == level), None)
        if shelf is None:
            skipped.append(Skipped(
                candidate_id=candidate_id, kind=KIND_SKU_LEVEL,
                reason=f"bay {bay_id!r} has no shelf at level {level!r}",
                detail=dict(base_detail),
            ))
            continue

        if shelf_now["level"] == level:
            to_slot_id, displaced = slot_now["slot_id"], None
            label = f"{sku_id} stays at {level} ({to_slot_id}) in bay {bay_id}"
        else:
            target = next((s for s in shelf["slots"] if s["sku_id"] is None), None)
            if target is None:
                target = shelf["slots"][0] if shelf["slots"] else None
            if target is None:
                skipped.append(Skipped(
                    candidate_id=candidate_id, kind=KIND_SKU_LEVEL,
                    reason=f"shelf {shelf['shelf_id']!r} at level {level!r} has no slots",
                    detail=dict(base_detail),
                ))
                continue
            to_slot_id, displaced = target["slot_id"], target["sku_id"]
            label = f"{sku_id} to {level} ({to_slot_id}) in bay {bay_id}"
            if displaced is not None:
                label += f" - swap with {displaced}"

        candidates.append(Candidate(
            candidate_id=candidate_id,
            kind=KIND_SKU_LEVEL,
            label=label,
            patches=({"op": "move_sku", "sku_id": sku_id, "to_slot_id": to_slot_id},),
            detail={**base_detail, "to_slot_id": to_slot_id, "displaced_sku_id": displaced,
                    "is_current_level": shelf_now["level"] == level},
        ))

    return CandidateSet(candidates=tuple(candidates), skipped=tuple(skipped))


# ---------------------------------------------------------------------------
# The ranking
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SeedSpread:
    """The range of one candidate's objective across several simulation seeds.

    **This is not a confidence interval and must never be reported as one.**
    It measures Monte Carlo run-to-run variability: the same 10,000-shopper
    simulation re-rolled at different `np.random.default_rng` seeds. A
    confidence interval would measure sampling error -- how much the answer
    would move if a different panel of shoppers had been drawn -- and
    `analytics/lift.py` explains why a committed `SimResult` cannot support
    one: its arms are normalised shares, and `n_runs` counts shoppers rather
    than purchase events, so a bootstrap of them comes out narrower than the
    truth. See docs/METHODOLOGY.md section 12.7.

    `seeds` and `values` are parallel and in run order, primary seed first, so
    a reader can see exactly which draws produced the range. `low` and `high`
    are their min and max -- not percentiles, because three to five draws have
    no percentiles worth the name.

    **Adding seeds will not narrow this, and that is not a defect.** `low` and
    `high` are a min and a max, so the range is a statistic that can only grow
    as draws are added; its expected width grows roughly like
    sqrt(2*log(n_seeds)). Two seeds will "resolve" almost any pair of
    candidates and twenty will resolve almost none, both from the same
    simulator. `n_seeds` is therefore part of the measurement, and a spread
    quoted without it says nothing. What extra seeds buy is a better estimate
    of the same underlying variability -- not less of it.

    The lever that does narrow it is `n_synth`. `sim/simulator.py` draws that
    many independent shoppers, so the standard deviation of the objective
    across seeds falls as 1/sqrt(n_synth): four times the shoppers, half the
    spread. `check_top_pick_stability` climbs run sizes for exactly that
    reason, and `Ranking.top_pick_is_resolved` should always be read next to
    the `n_synth` and the `n_seeds` it was computed at.
    """

    seeds: tuple[int, ...]
    values: tuple[float, ...]
    low: float
    high: float

    @property
    def n_seeds(self) -> int:
        """How many seeds the range was taken over.

        Part of the measurement, not bookkeeping: a range over three seeds and
        a range over twenty are different statistics of the same variability,
        and only the second is hard to overlap by luck.
        """
        return len(self.seeds)

    @property
    def width(self) -> float:
        """`high - low`, comparable to another spread only at the same
        `n_seeds` and the same `n_synth`."""
        return self.high - self.low

    def overlaps(self, other: "SeedSpread") -> bool:
        """Do the two ranges intersect? If so the ranking between the two
        candidates is not resolved at this number of seeds."""
        return self.low <= other.high and other.low <= self.high


@dataclass(frozen=True)
class Scored:
    """One candidate, simulated and placed.

    `objective` is None when the metric is undefined for this configuration --
    never 0.0, which is a measured value. `seed_spread` is Monte Carlo spread,
    NOT a confidence interval (see `SeedSpread`), and is None when the spread
    was not requested or when the objective is undefined at any seed.
    `unresolved_against` names the other candidates whose seed spread overlaps
    this one's, i.e. the rows this row is not actually ranked against.
    """

    rank: int
    candidate: Candidate
    objective: Optional[float]
    is_current: bool
    variant_id: str
    sim_run_id: str
    seed_spread: Optional[SeedSpread] = None
    unresolved_against: tuple[str, ...] = ()


@dataclass(frozen=True)
class Ranking:
    """Every candidate scored and sorted, best first.

    `objective_name` is not decoration: the same space ranked on ad lift and on
    a focal SKU's purchase share gives different orders, and a ranking quoted
    without its metric is not a result. `summary()` always prints it.

    `n_synth` and `seed` are the simulation the ranking was produced at.
    `skipped` carries the configurations the planogram could not express.
    """

    objective_name: str
    entries: tuple[Scored, ...]
    skipped: tuple[Skipped, ...]
    n_synth: int
    seed: int
    spread_seeds: tuple[int, ...]
    format_value: Callable[[float], str] = repr

    @property
    def n_candidates(self) -> int:
        """How many configurations were actually simulated."""
        return len(self.entries)

    @property
    def best(self) -> Optional[Scored]:
        return self.entries[0] if self.entries else None

    @property
    def current(self) -> Optional[Scored]:
        """The best-placed candidate that reproduces the planogram as passed in.

        Identified by comparing each candidate's RESOLVED content hash against
        the input planogram's, not by looking for an empty patch list: "AD_1 on
        B3_ENDCAP" writes the creative already hanging there, so its patch list
        is non-empty and it is still the placement being run today.

        A space can contain more than one such candidate -- "AD_1 on the slot
        it already occupies" and "AD_2 unplaced, which it already is" are both
        today's planogram seen through a different creative. They necessarily
        score identically, so "the current placement ranks Nth" is unambiguous:
        N is where the unchanged planogram first appears. Every one of them is
        flagged by `Scored.is_current`.

        None when no candidate reproduces the input, which happens when the
        caller restricts the space away from it.
        """
        return next((entry for entry in self.entries if entry.is_current), None)

    @property
    def current_rank(self) -> Optional[int]:
        current = self.current
        return None if current is None else current.rank

    @property
    def top_pick_is_resolved(self) -> Optional[bool]:
        """Is the recommendation actually separated from the rest?

        False when the top pick's seed spread overlaps another candidate's --
        the two are not ranked against each other at this number of seeds.
        None when no spread was computed, because "resolved" is then unknown
        rather than true.
        """
        best = self.best
        if best is None or best.seed_spread is None:
            return None
        return not best.unresolved_against

    @property
    def beats_current(self) -> Optional[tuple[str, ...]]:
        """Candidates whose whole seed range sits above the current placement's.

        This is a DIFFERENT question from `top_pick_is_resolved`, and it is the
        one a recommendation actually rests on. "Rank 1 beats rank 2" needs the
        two leaders separated from each other; "moving beats where it is now"
        needs one candidate separated from ONE named row, and a space whose
        leaders are all within noise of each other can still contain a move
        that clearly beats today's planogram. Reporting only the first claim
        throws the second away.

        A candidate qualifies on the same criterion `top_pick_is_resolved`
        uses -- `SeedSpread.overlaps` -- plus a direction: its `low` must be
        above the current placement's `high`, not merely disjoint from it. Ids
        come back sorted, and the rows that ARE the current placement are never
        in the list; they reproduce the same planogram and score identically to
        it by construction.

        None when the question was not answered: no candidate reproduces the
        input planogram, or the current placement fell outside `spread_top_n`
        and so has no range to clear. An empty tuple is the other answer --
        every candidate was compared and none cleared it.
        """
        current = self.current
        if current is None or current.seed_spread is None:
            return None
        bar = current.seed_spread
        return tuple(sorted(
            entry.candidate.candidate_id for entry in self.entries
            if not entry.is_current and entry.seed_spread is not None
            and entry.seed_spread.low > bar.high
        ))


def variant_id_for(base: Mapping[str, Any], patches: Sequence[Mapping[str, Any]]) -> str:
    """A stable id for `base` + `patches`, using the project's one canonical-JSON
    recipe (`api.app.simcache`).

    It keys the simulation cache and is folded into `sim_run_id` by
    `sim.simulator.combine`, so the same configuration always reports the same
    run id and a row of a ranking can be reproduced. The `opt_` prefix says
    the configuration came from a search; the numbers are identical to a
    what-if of the same patches, only the run id differs.
    """
    payload = f"{simcache.document_hash(base)}|{simcache.canonical(list(patches))}"
    return f"opt_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:12]}"


def rank_candidates(
    base: Mapping[str, Any],
    candidates: "CandidateSet | Iterable[Candidate]",
    objective: Objective,
    *,
    n_synth: int = DEFAULT_N_SYNTH,
    seed: int = DEFAULT_SEED,
    spread_seeds: Sequence[int] = DEFAULT_SPREAD_SEEDS,
    spread_top_n: int = DEFAULT_SPREAD_TOP_N,
    simulate: Callable[..., Any] = simcache.population,
) -> Ranking:
    """Score every candidate against `objective` and sort them, best first.

    Exhaustive, not heuristic: see the module docstring. Every candidate is
    resolved through `api.app.resolve.resolve` and simulated through
    `simulate` -- by default `api.app.simcache.population`, the cached,
    deterministic call a prediction lock is built from -- so a ranking cannot
    disagree with a lock about what the simulator said.

    **The ranking is only meaningful together with `objective.name`**, which is
    carried onto `Ranking.objective_name` and printed by `summary()`.

    Sorting: defined objectives descending, then undefined ones (which are
    never converted to 0), and ties broken on `candidate_id` ascending. That
    makes the order a function of the inputs alone -- `scripts/eval.py` has to
    be able to regenerate it.

    `spread_seeds` re-scores the first `spread_top_n` entries at those extra
    seeds and reports the range as `Scored.seed_spread`. Read `SeedSpread`
    before quoting it: it is Monte Carlo run-to-run spread, not a confidence
    interval. Pass `spread_seeds=()` to skip it entirely.

    Raises ValueError on a duplicate `candidate_id` -- two spaces composed with
    an overlap would double-count a configuration and shift every rank below
    it.
    """
    space = candidates if isinstance(candidates, CandidateSet) else CandidateSet(tuple(candidates))

    seen: set[str] = set()
    for candidate in space.candidates:
        if candidate.candidate_id in seen:
            raise ValueError(f"duplicate candidate id {candidate.candidate_id!r}")
        seen.add(candidate.candidate_id)

    base_hash = simcache.document_hash(base)
    resolved_by_id: dict[str, Any] = {}
    variant_by_id: dict[str, str] = {}
    rows: list[tuple[Candidate, Optional[float], bool, str, str]] = []

    for candidate in space.candidates:
        resolved = resolve(base, _variant_document(base, candidate))
        variant_id = variant_id_for(base, candidate.patches)
        bundle = simulate(resolved, variant_id, n_synth=n_synth, seed=seed)
        value = objective.score(bundle.population, resolved)
        rows.append((
            candidate,
            None if value is None else float(value),
            simcache.document_hash(resolved) == base_hash,
            variant_id,
            str(bundle.population["sim_run_id"]),
        ))
        resolved_by_id[candidate.candidate_id] = resolved
        variant_by_id[candidate.candidate_id] = variant_id

    # Defined objectives descending, undefined ones after all of them, ties on
    # candidate_id ascending. Every component is a function of the inputs, so
    # the order is reproducible run to run and machine to machine.
    rows.sort(key=lambda row: (row[1] is None,
                               -row[1] if row[1] is not None else 0.0,
                               row[0].candidate_id))

    entries = [
        Scored(rank=i + 1, candidate=candidate, objective=value, is_current=is_current,
               variant_id=variant_id, sim_run_id=sim_run_id)
        for i, (candidate, value, is_current, variant_id, sim_run_id) in enumerate(rows)
    ]

    entries = _attach_seed_spread(
        entries, objective=objective, seed=seed, spread_seeds=spread_seeds,
        spread_top_n=spread_top_n, n_synth=n_synth, simulate=simulate,
        resolved_by_id=resolved_by_id, variant_by_id=variant_by_id,
    )

    return Ranking(
        objective_name=objective.name,
        entries=tuple(entries),
        skipped=space.skipped,
        n_synth=int(n_synth),
        seed=int(seed),
        spread_seeds=tuple(spread_seeds),
        format_value=objective.format_value,
    )


def summary(ranking: Ranking) -> str:
    """The recommendation in words, for RESULTS.md and the demo.

    PLAN section 6 asks for *"Best placement for AD_1: endcap header bay 3,
    +11% (CI 8-14). Current placement ranks 6th of 12."* This prints the same
    sentence with one deliberate change: the parenthesis is a **seed spread**,
    not a CI, and says so. See `SeedSpread` for why there is no honest
    confidence interval to put there instead.
    """
    lines: list[str] = []
    n = ranking.n_candidates
    best = ranking.best

    if best is None:
        return f"No candidates were scored on {ranking.objective_name}."

    value = "undefined" if best.objective is None else ranking.format_value(best.objective)
    lines.append(
        f"Best of {n} placements on {ranking.objective_name} "
        f"({ranking.n_synth} shoppers, seed {ranking.seed}): "
        f"{best.candidate.label} at {value}."
    )

    if best.seed_spread is not None:
        spread = best.seed_spread
        lines.append(
            f"Seed spread {ranking.format_value(spread.low)} to "
            f"{ranking.format_value(spread.high)} over seeds "
            f"{', '.join(str(s) for s in spread.seeds)} -- Monte Carlo run-to-run "
            "variability, not a confidence interval."
        )
    if ranking.top_pick_is_resolved is False:
        lines.append(
            "The order is not resolved against "
            f"{', '.join(best.unresolved_against)}: the seed spreads overlap."
        )
        lines.append(
            "More seeds will not settle it: the spread is a min-max range over "
            f"{best.seed_spread.n_seeds} seeds and can only widen as seeds are added. "
            "Only a larger n_synth narrows it -- check_top_pick_stability() re-ranks the "
            "same space at a ladder of run sizes and reports whether this top pick survives one."
        )

    current = ranking.current
    if current is None:
        lines.append("The planogram as given is not among the candidates scored.")
    else:
        current_value = ("undefined" if current.objective is None
                         else ranking.format_value(current.objective))
        lines.append(
            f"Current placement ({current.candidate.label}) ranks "
            f"{_ordinal(current.rank)} of {n} at {current_value}."
        )

    beats = ranking.beats_current
    if beats:
        lines.append(
            f"{len(beats)} placement(s) clear the current placement's seed spread entirely: "
            f"{', '.join(beats)}. That pair is settled at this n_synth even where the order "
            "among the leaders is not."
        )
    elif beats == ():
        lines.append(
            "No placement clears the current placement's seed spread, so \"moving beats where "
            f"it is now\" is not settled at {ranking.n_synth} shoppers."
        )

    if ranking.skipped:
        lines.append(
            f"{len(ranking.skipped)} configuration(s) not scored: "
            + "; ".join(f"{s.candidate_id} ({s.reason})" for s in ranking.skipped)
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Is the top pick a property of the planogram, or of n_synth?
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Stability:
    """One ranking per run size, and whether the same candidate won each time.

    `SeedSpread` re-rolls the dice at a FIXED panel size and asks whether the
    answer moves. This asks the other question -- whether the answer moves when
    the panel GROWS -- and a spread cannot answer it, because every seed a
    spread re-rolls is drawn at the same `n_synth`. A candidate that leads only
    because 10,000 shoppers are too few to tell it apart from its neighbours
    will lead at most of those seeds too, and its spread will look no worse
    than anybody else's.

    `rankings` are in ladder order, one per rung, produced by `rank_candidates`
    with everything except `n_synth` held fixed, so any difference between two
    rungs is the run size and nothing else.

    A stable top pick is NOT the same claim as a resolved one.
    `Ranking.top_pick_is_resolved` asks whether the lead is bigger than the
    run-to-run noise; this asks whether the winner is the same candidate at
    all. A ranking can be stable and unresolved (the same candidate wins every
    rung, by a margin smaller than the noise) and it can be resolved and
    unstable (a comfortable lead at one size, a different comfortable lead at
    another). Both are reported, neither is inferred from the other.
    """

    objective_name: str
    rankings: tuple[Ranking, ...]
    n_synth_ladder: tuple[int, ...]
    seed: int

    @property
    def top_pick_ids(self) -> tuple[Optional[str], ...]:
        """The winning `candidate_id` at each rung, in ladder order.

        None at a rung whose space was empty -- never a candidate id borrowed
        from a neighbouring rung.
        """
        return tuple(None if ranking.best is None else ranking.best.candidate.candidate_id
                     for ranking in self.rankings)

    @property
    def top_pick_is_stable(self) -> Optional[bool]:
        """Did one candidate win every rung?

        None -- not True -- for a ladder of fewer than two rungs. "It won every
        rung it was tried at" is vacuous when there was one, and this module's
        word for a question it has not answered is None, as it is for
        `Ranking.top_pick_is_resolved`.
        """
        if len(self.rankings) < 2:
            return None
        ids = self.top_pick_ids
        return all(candidate_id is not None and candidate_id == ids[0] for candidate_id in ids)

    @property
    def settled_top_pick(self) -> Optional[str]:
        """The candidate that won every rung, or None when none did.

        None is also the answer for a one-rung ladder: there is a winner there,
        but nothing was settled about it.
        """
        return self.top_pick_ids[0] if self.top_pick_is_stable else None

    @property
    def reordered_at(self) -> tuple[int, ...]:
        """The rungs whose winner differs from the first rung's.

        Empty when the top pick held, which is why it is safe to print
        unconditionally: it names the run sizes at which the recommendation
        changed, and those are the evidence.
        """
        ids = self.top_pick_ids
        return tuple(n_synth for n_synth, candidate_id
                     in zip(self.n_synth_ladder[1:], ids[1:]) if candidate_id != ids[0])


def check_top_pick_stability(
    base: Mapping[str, Any],
    candidates: "CandidateSet | Iterable[Candidate]",
    objective: Objective,
    *,
    n_synth_ladder: Sequence[int] = DEFAULT_STABILITY_LADDER,
    seed: int = DEFAULT_SEED,
    spread_seeds: Sequence[int] = (),
    spread_top_n: int = DEFAULT_SPREAD_TOP_N,
    simulate: Callable[..., Any] = simcache.population,
) -> Stability:
    """Rank the same space at each rung of `n_synth_ladder` and compare winners.

    This is the check that catches the one failure `SeedSpread` structurally
    cannot: a recommendation that is an artefact of the run size. Everything
    but `n_synth` is held fixed -- same base, same candidates, same objective,
    same primary seed -- so a winner that changes between rungs changed because
    the panel grew.

    **Cost.** One rung costs one full ranking, and a ranking is linear in
    `n_synth`, so the default ladder costs about 1 + 5 + 25 = 31 times a single
    default ranking. That is minutes, not milliseconds, which is why this is a
    separate call and not something `rank_candidates` does for you.

    `spread_seeds` defaults to `()` -- no seed spreads at all. The ladder is
    about run size; paying for K seeds at every rung multiplies an already
    expensive check by K, and the spread at the bottom rung is what a plain
    `rank_candidates` already reports. Pass seeds explicitly if you want both.

    Raises ValueError on an empty ladder or one that is not strictly
    increasing. A repeated rung would compare a ranking with itself and report
    stability that was never tested, and a ladder that goes down reads, in
    `reordered_at`, as though the bigger panel came first.
    """
    ladder = tuple(int(n) for n in n_synth_ladder)
    if not ladder:
        raise ValueError("n_synth_ladder must name at least one run size")
    if any(later <= earlier for earlier, later in zip(ladder, ladder[1:])):
        raise ValueError(f"n_synth_ladder must be strictly increasing, got {ladder!r}")

    space = candidates if isinstance(candidates, CandidateSet) else CandidateSet(tuple(candidates))
    rankings = tuple(
        rank_candidates(base, space, objective, n_synth=n_synth, seed=seed,
                        spread_seeds=spread_seeds, spread_top_n=spread_top_n,
                        simulate=simulate)
        for n_synth in ladder
    )

    return Stability(objective_name=objective.name, rankings=rankings,
                     n_synth_ladder=ladder, seed=int(seed))


def stability_summary(stability: Stability) -> str:
    """The ladder in words, for RESULTS.md and the demo.

    Prints every rung's winner whichever way the check came out, because the
    row that disagrees is the finding and summarising it away would leave the
    reader with exactly the impression the check exists to prevent.
    """
    lines = [
        f"Top pick on {stability.objective_name} across "
        f"{len(stability.n_synth_ladder)} run size(s), seed {stability.seed}:"
    ]
    for ranking, candidate_id in zip(stability.rankings, stability.top_pick_ids):
        best = ranking.best
        if best is None:
            lines.append(f"  n_synth={ranking.n_synth:,}: no candidates scored")
            continue
        value = "undefined" if best.objective is None else ranking.format_value(best.objective)
        lines.append(f"  n_synth={ranking.n_synth:,}: {candidate_id} at {value}")

    stable = stability.top_pick_is_stable
    if stable is None:
        lines.append(
            "One rung only: whether the top pick survives a bigger synthetic panel is "
            "not established. Add rungs to n_synth_ladder to find out."
        )
    elif stable:
        lines.append(
            f"The top pick is stable in n_synth: {stability.settled_top_pick} won every rung "
            f"from {stability.n_synth_ladder[0]:,} to {stability.n_synth_ladder[-1]:,} shoppers. "
            "That says the winner is the same candidate at every run size; it does NOT say the "
            "lead is bigger than the run-to-run noise -- Ranking.top_pick_is_resolved answers "
            "that, separately."
        )
    else:
        changed = ", ".join(f"{n:,}" for n in stability.reordered_at)
        lines.append(
            f"The top pick is not stable in n_synth: it changed at {changed} shoppers. "
            f"The {stability.n_synth_ladder[0]:,}-shopper winner was not measuring the best "
            "placement, so there is no settled recommendation to quote from this space."
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _attach_seed_spread(
    entries: Sequence[Scored], *, objective: Objective, seed: int,
    spread_seeds: Sequence[int], spread_top_n: int, n_synth: int,
    simulate: Callable[..., Any], resolved_by_id: Mapping[str, Any],
    variant_by_id: Mapping[str, str],
) -> list[Scored]:
    """Re-score the top entries at the extra seeds and record the range.

    The seed list is the primary seed first, then `spread_seeds` with the
    primary and any repeats removed, so the reported range always contains the
    number the ranking was made on. An entry whose objective is undefined at
    any of the seeds gets no spread at all: a range over the seeds that
    happened to be defined would be a different quantity from a range over all
    of them.
    """
    seeds = [int(seed)]
    for extra in spread_seeds:
        if int(extra) not in seeds:
            seeds.append(int(extra))
    if len(seeds) < 2 or spread_top_n < 1:
        return list(entries)

    spreads: dict[str, SeedSpread] = {}
    for entry in entries[:spread_top_n]:
        if entry.objective is None:
            continue
        candidate_id = entry.candidate.candidate_id
        resolved = resolved_by_id[candidate_id]
        variant_id = variant_by_id[candidate_id]
        values: list[float] = [entry.objective]
        defined_at_every_seed = True
        for extra in seeds[1:]:
            bundle = simulate(resolved, variant_id, n_synth=n_synth, seed=extra)
            value = objective.score(bundle.population, resolved)
            if value is None:
                defined_at_every_seed = False
                break
            values.append(float(value))
        if not defined_at_every_seed:
            continue
        spreads[candidate_id] = SeedSpread(
            seeds=tuple(seeds), values=tuple(values),
            low=min(values), high=max(values),
        )

    updated: list[Scored] = []
    for entry in entries:
        spread = spreads.get(entry.candidate.candidate_id)
        if spread is None:
            updated.append(entry)
            continue
        unresolved = tuple(sorted(
            other_id for other_id, other in spreads.items()
            if other_id != entry.candidate.candidate_id and spread.overlaps(other)
        ))
        updated.append(Scored(
            rank=entry.rank, candidate=entry.candidate, objective=entry.objective,
            is_current=entry.is_current, variant_id=entry.variant_id,
            sim_run_id=entry.sim_run_id, seed_spread=spread, unresolved_against=unresolved,
        ))
    return updated


def _ad_slot_index(planogram: Mapping[str, Any]) -> dict[str, tuple[str, Mapping[str, Any]]]:
    """`ad_slot_id -> (bay_id, ad slot)` in planogram order."""
    return {
        ad["ad_slot_id"]: (bay["bay_id"], ad)
        for bay in planogram["bays"]
        for ad in bay["ad_slots"]
    }


def _placement_of(planogram: Mapping[str, Any], sku_id: str):
    """`(bay, shelf, slot)` for the SKU, or None when it is on no shelf."""
    for bay in planogram["bays"]:
        for shelf in bay["shelves"]:
            for slot in shelf["slots"]:
                if slot["sku_id"] == sku_id:
                    return bay, shelf, slot
    return None


def _clear(ad_slot_id: str) -> dict:
    return {"op": "set_ad_creative", "ad_slot_id": ad_slot_id, "creative_id": None}


def _variant_document(base: Mapping[str, Any], candidate: Candidate) -> dict:
    """The variant shape `resolve()` and variant.schema.json both expect."""
    return {
        "variant_id": candidate.candidate_id,
        "base_planogram_id": base["planogram_id"],
        "name": candidate.label,
        "patches": list(candidate.patches),
    }


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"
