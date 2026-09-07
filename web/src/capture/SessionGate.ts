import type { Event as ShopperEvent } from "@/contracts/event.schema";
import type { Session } from "@/contracts/session.schema";

/**
 * Is this session usable evidence? (SPEC M2, "Gate".)
 *
 * The gate runs in the browser and the server persists what it is told:
 * `api/app/routers/sessions.py` accepts `accepted`, `reject_reason` and
 * `quality` on `POST /sessions/{id}/finish` and validates them against
 * schemas/session.schema.json, but computes none of them.
 *
 * Rejecting is not deleting. A rejected session keeps its events and its
 * quality block: S19's noise dashboard plots the reject reasons, the
 * calibration-error histogram and the mode split, and it can only do that if
 * the sessions that failed are still there with a reason attached.
 *
 * ## Why there is no duration floor
 *
 * This gate used to open with `duration_s >= 45`, and the comment on it said
 * "a shorter session has not seen enough shelf to say anything". That was an
 * honest statement of intent and a bad rule, because the thing it stood in for
 * is directly measurable and time is only correlated with it.
 *
 * The first real session collected made the cost concrete. A `mission` shopper
 * with a list, who knew the brand and declared themselves in a hurry, entered
 * three bays, picked up three products, carted all three and checked out - in
 * 28.9 seconds. Everything else about the session passed. The gate discarded
 * it for being sixteen seconds too quick.
 *
 * That is not a threshold in need of loosening. Rejecting on elapsed time
 * systematically rejects the mission archetype, because a shopper with a list
 * who knows the brand *is* finished in half a minute; the slower the rule, the
 * more the surviving panel is made of browsers. The project's central claim is
 * that synthetic personas match real shoppers, benchmarked against the real
 * panel's own repeatability - and one of the four personas is `mission`. A
 * duration floor would have had the synthetic panel validated against a real
 * panel that excluded, by construction, the shoppers it was being asked to
 * predict.
 *
 * So the gate asks the question the floor was proxying for: **did this session
 * produce looking evidence on enough of the shelf to estimate attention?** It
 * counts the distinct slots that produced a looking observation, in the
 * channels `analytics/fusion.py` weights for that mode. Depth is already
 * guaranteed per observation upstream - a `cursor_dwell` requires 300 ms held
 * on one slot (`CursorTracker.ts`) and a `fixation` requires the filter's
 * minimum duration - so spread is what is left to check, and one threshold
 * does it.
 *
 * The trade this makes, stated rather than buried: a fast shopper who covered
 * the shelf is now accepted, and a slow one who stared at a single product is
 * still rejected. Time is reported in `quality.duration_s` as it always was.
 * It is simply no longer a verdict.
 */

/**
 * SPEC M2. Distinct slots that must have produced a looking observation.
 *
 * Six of the seed planogram's 24 slots - a quarter of the shelf, and with
 * `MIN_STATIONS` it cannot be reached without leaving the first bay's eight.
 * The number is absolute rather than a fraction of the variant's slot
 * vocabulary because `summarise` reads an event stream and nothing else; if a
 * much smaller planogram is ever shopped (`vision/pipeline.py` reads bays of
 * eight or fewer), this constant is the thing to revisit, not the rule.
 */
export const MIN_OBSERVED_SLOTS = 6;

/** One station is one bay: no navigation, no comparison, no browsing. */
export const MIN_STATIONS = 2;

/** At least one thing touched, or the person was not shopping. */
export const MIN_INTERACTIONS = 1;

/** Webcam sessions only. Below this the eye tracker was not really tracking. */
export const MIN_FIXATION_COVERAGE = 0.4;

/**
 * The event types that count as an interaction: something the shopper *did*,
 * not something they looked at. Same three types `analytics/fusion.py` weights
 * as interactions, so "had an interaction" and "contributed an interaction to
 * the fused attention" mean the same thing.
 */
export const INTERACTION_EVENT_TYPES: readonly ShopperEvent["type"][] = [
  "hover",
  "pickup",
  "add_to_cart",
];

/**
 * The event types that count as *looking*, per mode, mirroring the channels
 * `analytics/fusion.py` gives non-zero weight in `_MODE_WEIGHTS`:
 *
 *     cursor_only:  0.7 * cursor_dwell + 0.3 * interaction
 *     webcam:       0.5 * fixation + 0.3 * cursor_dwell + 0.2 * interaction
 *
 * Fixations are excluded in cursor_only because fusion weights them zero
 * there. A session must not clear the gate on evidence the attention formula
 * will then throw away: admitting sessions the analysis cannot use is the same
 * class of error as rejecting ones it could.
 *
 * Interactions are deliberately absent from both lists. They have their own
 * rule, and letting a pickup satisfy this one too would collapse two
 * independent criteria into one.
 */
export const LOOKING_EVENT_TYPES: Readonly<
  Record<Session["mode"], readonly ShopperEvent["type"][]>
> = {
  cursor_only: ["cursor_dwell"],
  webcam: ["fixation", "cursor_dwell"],
};

export type RejectReason = NonNullable<Session["reject_reason"]>;

/**
 * The order the rules are applied in, and therefore the reason a session that
 * breaks several of them reports. Fixed and documented on purpose: the reason
 * histogram in the noise dashboard is only readable if one session always
 * yields one answer, whatever order the checks happen to be written in.
 *
 * Consent first, because a session without it is not data at all, whatever else
 * it managed to do.
 *
 * `too_short` is absent and never emitted again. It remains in
 * schemas/session.schema.json so that sessions rejected under the old duration
 * rule stay valid and exportable - `scripts/anonymise_sessions.py` treats a
 * session it cannot validate as a build failure, so dropping the value would
 * make old evidence unreadable rather than merely obsolete.
 */
export const REJECT_ORDER: readonly RejectReason[] = [
  "no_consent",
  "too_few_slots",
  "one_station",
  "no_interaction",
  "low_coverage",
];

/** SPEC 4.3's `quality` block, exactly. */
export interface SessionQuality {
  /**
   * Fraction of the session that produced a fixation: the summed `dur_ms` of
   * every `fixation` event divided by the session duration in milliseconds,
   * clamped to [0, 1].
   *
   * It measures how much of the session the eye tracker was actually resolving
   * gaze for, not how much of it landed on a product - a fixation on bare shelf
   * counts, because the tracker was working. A cursor-only session has no
   * fixations at all and so has a coverage of 0 by construction, which is why
   * the coverage rule applies to webcam sessions only.
   */
  fixation_coverage: number;
  stations_visited: number;
  /**
   * Distinct slots that produced at least one looking observation. The number
   * the gate now decides on, so it is reported whether the session passed or
   * failed: a rejection nobody can diagnose is not much better than a silent
   * one.
   */
  slots_observed: number;
  /**
   * How long the session ran. Descriptive only - the noise dashboard plots it
   * and RESULTS.md reports it - and deliberately not a criterion; see the
   * module docstring.
   */
  duration_s: number;
}

export interface SessionSummary extends SessionQuality {
  consent: boolean;
  mode: Session["mode"];
  interactions: number;
}

export interface GateResult {
  accepted: boolean;
  /** Null exactly when `accepted` is true. */
  reject_reason: RejectReason | null;
  quality: SessionQuality;
}

/**
 * Accept iff: consent given, `slots_observed >= 6`, `stations_visited >= 2`, at
 * least one interaction, and - webcam only - `fixation_coverage >= 0.4`.
 *
 * The numbers are reported exactly as they are given: this decides, it does not
 * launder. `summarise` is what turns an event stream into them.
 */
export function evaluate(summary: SessionSummary): GateResult {
  const reason = firstFailure(summary);
  return {
    accepted: reason === null,
    reject_reason: reason,
    quality: {
      fixation_coverage: summary.fixation_coverage,
      stations_visited: summary.stations_visited,
      slots_observed: summary.slots_observed,
      duration_s: summary.duration_s,
    },
  };
}

function firstFailure(summary: SessionSummary): RejectReason | null {
  // Written in REJECT_ORDER, and the test asserts the two agree.
  if (!summary.consent) return "no_consent";
  if (summary.slots_observed < MIN_OBSERVED_SLOTS) return "too_few_slots";
  if (summary.stations_visited < MIN_STATIONS) return "one_station";
  if (summary.interactions < MIN_INTERACTIONS) return "no_interaction";
  if (summary.mode === "webcam" && summary.fixation_coverage < MIN_FIXATION_COVERAGE) {
    return "low_coverage";
  }
  return null;
}

export interface SummariseOptions {
  consent: boolean;
  mode: Session["mode"];
  /** Defaults to the last event's `t_ms`, which is the checkout in a real session. */
  duration_s?: number;
}

/**
 * Turn a session's own event buffer into the numbers `evaluate` decides on.
 *
 * `stations_visited` counts distinct non-empty `station_id`s over every event,
 * which is exactly what `api/app/live.py` counts, so the browser's number and
 * the spectator screen's number are the same number.
 */
export function summarise(
  events: readonly ShopperEvent[],
  options: SummariseOptions,
): SessionSummary {
  const stations = new Set<string>();
  const observedSlots = new Set<string>();
  const lookingTypes = LOOKING_EVENT_TYPES[options.mode];
  let lastMs = 0;
  let interactions = 0;
  let fixationMs = 0;

  for (const event of events) {
    if (event.t_ms > lastMs) lastMs = event.t_ms;
    if (event.station_id !== null && event.station_id.length > 0) {
      stations.add(event.station_id);
    }
    if (INTERACTION_EVENT_TYPES.includes(event.type)) interactions += 1;

    if (lookingTypes.includes(event.type)) {
      // A null `slot_id` is a look at bare shelf between products. fusion.py
      // skips it - it belongs to no slot and enters no denominator - so it is
      // not evidence that a slot was seen either.
      const slotId = event.payload.slot_id;
      if (typeof slotId === "string" && slotId.length > 0) observedSlots.add(slotId);
    }

    if (event.type === "fixation") {
      const durMs = event.payload.dur_ms;
      if (typeof durMs === "number" && Number.isFinite(durMs) && durMs > 0) {
        fixationMs += durMs;
      }
    }
  }

  const durationS = options.duration_s ?? lastMs / 1000;
  const durationMs = durationS * 1000;

  return {
    consent: options.consent,
    mode: options.mode,
    duration_s: durationS,
    stations_visited: stations.size,
    slots_observed: observedSlots.size,
    interactions,
    // Clamped: schemas/session.schema.json bounds this to [0, 1], and the API
    // refuses the whole finish call if it is out of range. Overlapping
    // fixations, or a duration that disagrees with the event stamps, must not
    // be able to make a session unfinishable.
    fixation_coverage:
      durationMs > 0 ? Math.min(1, Math.max(0, fixationMs / durationMs)) : 0,
  };
}
