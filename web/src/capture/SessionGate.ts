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
 */

/** SPEC M2. A shorter session has not seen enough shelf to say anything. */
export const MIN_DURATION_S = 45;

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

export type RejectReason = NonNullable<Session["reject_reason"]>;

/**
 * The order the rules are applied in, and therefore the reason a session that
 * breaks several of them reports. Fixed and documented on purpose: the reason
 * histogram in the noise dashboard is only readable if one session always
 * yields one answer, whatever order the checks happen to be written in.
 *
 * Consent first, because a session without it is not data at all, whatever else
 * it managed to do.
 */
export const REJECT_ORDER: readonly RejectReason[] = [
  "no_consent",
  "too_short",
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
 * Accept iff: consent given, `duration_s >= 45`, `stations_visited >= 2`, at
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
      duration_s: summary.duration_s,
    },
  };
}

function firstFailure(summary: SessionSummary): RejectReason | null {
  // Written in REJECT_ORDER, and the test asserts the two agree.
  if (!summary.consent) return "no_consent";
  if (summary.duration_s < MIN_DURATION_S) return "too_short";
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
  let lastMs = 0;
  let interactions = 0;
  let fixationMs = 0;

  for (const event of events) {
    if (event.t_ms > lastMs) lastMs = event.t_ms;
    if (event.station_id !== null && event.station_id.length > 0) {
      stations.add(event.station_id);
    }
    if (INTERACTION_EVENT_TYPES.includes(event.type)) interactions += 1;
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
    interactions,
    // Clamped: schemas/session.schema.json bounds this to [0, 1], and the API
    // refuses the whole finish call if it is out of range. Overlapping
    // fixations, or a duration that disagrees with the event stamps, must not
    // be able to make a session unfinishable.
    fixation_coverage:
      durationMs > 0 ? Math.min(1, Math.max(0, fixationMs / durationMs)) : 0,
  };
}
