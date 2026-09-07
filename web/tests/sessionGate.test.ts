import { describe, expect, it } from "vitest";
import type { Event as ShopperEvent } from "@/contracts/event.schema";
import {
  MIN_FIXATION_COVERAGE,
  MIN_INTERACTIONS,
  MIN_OBSERVED_SLOTS,
  MIN_STATIONS,
  REJECT_ORDER,
  evaluate,
  summarise,
  type SessionSummary,
} from "@/capture/SessionGate";

/**
 * The gate decides whether a session is usable evidence.
 *
 * It used to open with a 45-second floor. That floor was a proxy - the comment
 * on it said "has not seen enough shelf to say anything" - and it was a bad
 * one, because the thing it proxied for is directly measurable. The first real
 * session collected was a `mission` shopper who had a list, knew the brand,
 * declared themselves in a hurry, entered three bays, picked up three products,
 * carted all three and checked out in 28.9 seconds. The gate threw it away for
 * being 16 seconds too quick.
 *
 * That is not a threshold that needs loosening, it is the wrong quantity.
 * Rejecting on duration systematically rejects the mission archetype, which is
 * one of the four personas the panel exists to validate: the synthetic
 * prediction would then be benchmarked against a real panel that excludes, by
 * construction, the very shoppers it claims to model.
 *
 * So the gate now asks the question the floor was standing in for: did this
 * session produce looking evidence on enough of the shelf to estimate
 * attention? It counts distinct slots that produced a looking observation, in
 * the channels `analytics/fusion.py` actually weights for that mode. Depth per
 * observation is already guaranteed upstream - a `cursor_dwell` needs 300 ms on
 * one slot (CursorTracker.ts) and a `fixation` needs the filter's minimum - so
 * what remains to check is spread.
 */

/** A session that passes every rule; each test breaks exactly one of them. */
function passing(overrides: Partial<SessionSummary> = {}): SessionSummary {
  return {
    consent: true,
    mode: "webcam",
    duration_s: 96,
    stations_visited: 3,
    slots_observed: 9,
    interactions: 4,
    fixation_coverage: 0.71,
    ...overrides,
  };
}

function event(
  type: ShopperEvent["type"],
  t_ms: number,
  station_id: string | null,
  payload: Record<string, unknown> = {},
): ShopperEvent {
  return { t_ms, type, station_id, payload };
}

describe("the gate thresholds are pinned", () => {
  it("holds the SPEC M2 values", () => {
    expect(MIN_OBSERVED_SLOTS).toBe(6);
    expect(MIN_STATIONS).toBe(2);
    expect(MIN_INTERACTIONS).toBe(1);
    expect(MIN_FIXATION_COVERAGE).toBe(0.4);
  });

  it("enumerates the reject reasons in the order it applies them", () => {
    expect(REJECT_ORDER).toEqual([
      "no_consent",
      "too_few_slots",
      "one_station",
      "no_interaction",
      "low_coverage",
    ]);
  });

  it("no longer enumerates too_short", () => {
    // Retained in schemas/session.schema.json so sessions rejected under the
    // old rule stay valid and exportable, but never emitted again. A reason
    // that is still produced under a rule that no longer exists would make the
    // noise dashboard's histogram a record of two different gates.
    expect(REJECT_ORDER).not.toContain("too_short");
  });
});

describe("SessionGate.evaluate", () => {
  it("accepts a full session and reports its quality", () => {
    expect(evaluate(passing())).toEqual({
      accepted: true,
      reject_reason: null,
      quality: {
        fixation_coverage: 0.71,
        stations_visited: 3,
        slots_observed: 9,
        duration_s: 96,
      },
    });
  });

  it("rejects a session that looked at five slots as too_few_slots", () => {
    const result = evaluate(passing({ slots_observed: 5 }));
    expect(result.accepted).toBe(false);
    expect(result.reject_reason).toBe("too_few_slots");
    // The quality block is still reported: a rejected session is evidence too,
    // and S19's noise dashboard plots the reasons against these numbers. The
    // number it was rejected on has to be one of them or the rejection is not
    // diagnosable.
    expect(result.quality.slots_observed).toBe(5);
  });

  it("takes exactly six slots", () => {
    expect(evaluate(passing({ slots_observed: 5 })).reject_reason).toBe("too_few_slots");
    expect(evaluate(passing({ slots_observed: 6 })).accepted).toBe(true);
  });

  it("accepts the 29-second mission shopper the duration floor threw away", () => {
    // The case that prompted this rule. Real session da18f055: three bays,
    // three pickups, three add-to-carts, a checkout, 28.9 seconds. Under the
    // old gate this was `too_short` and never reached the panel.
    const mission = passing({
      mode: "cursor_only",
      duration_s: 28.904,
      stations_visited: 3,
      slots_observed: 7,
      interactions: 6,
      fixation_coverage: 0,
    });
    expect(evaluate(mission).accepted).toBe(true);
    expect(evaluate(mission).reject_reason).toBeNull();
  });

  it("still rejects a long session that barely looked at the shelf", () => {
    // The converse, and the reason this is a replacement rather than a
    // removal: five minutes spent staring at one product is not five minutes
    // of evidence about a shelf. Dropping the floor to a smaller number would
    // have accepted this; asking the right question does not.
    const staring = passing({ duration_s: 300, slots_observed: 2 });
    expect(evaluate(staring).accepted).toBe(false);
    expect(evaluate(staring).reject_reason).toBe("too_few_slots");
  });

  it("never rejects for duration, however short", () => {
    expect(evaluate(passing({ duration_s: 0.5 })).accepted).toBe(true);
  });

  it("rejects a single station as one_station", () => {
    expect(evaluate(passing({ stations_visited: 1 })).reject_reason).toBe("one_station");
    expect(evaluate(passing({ stations_visited: 2 })).accepted).toBe(true);
  });

  it("rejects a session with nothing touched as no_interaction", () => {
    expect(evaluate(passing({ interactions: 0 })).reject_reason).toBe("no_interaction");
    expect(evaluate(passing({ interactions: 1 })).accepted).toBe(true);
  });

  it("rejects a webcam session below 0.4 coverage and takes 0.41", () => {
    expect(evaluate(passing({ fixation_coverage: 0.39 })).reject_reason).toBe(
      "low_coverage",
    );
    expect(evaluate(passing({ fixation_coverage: 0.41 })).accepted).toBe(true);
    expect(evaluate(passing({ fixation_coverage: 0.4 })).accepted).toBe(true);
  });

  it("never rejects a cursor_only session for coverage", () => {
    // A cursor-only session has no eye tracking at all, so its coverage is 0 by
    // definition. Rejecting it for that would throw away every session that
    // fell back - which is most of the panel on laptops with bad webcams.
    const result = evaluate(passing({ mode: "cursor_only", fixation_coverage: 0 }));
    expect(result.accepted).toBe(true);
    expect(result.reject_reason).toBeNull();
  });

  it("rejects a session without consent whatever else it did", () => {
    // main.tsx's ?skip_capture=1 sets consent false deliberately: a developer
    // session must never be able to walk into the real panel.
    const result = evaluate(passing({ consent: false }));
    expect(result.accepted).toBe(false);
    expect(result.reject_reason).toBe("no_consent");
  });

  it("reports the first reason in the documented order when several apply", () => {
    // One session, every rule broken. The answer must not depend on which check
    // happened to run first, or the noise dashboard's reason histogram is noise.
    const everything = passing({
      consent: false,
      slots_observed: 0,
      stations_visited: 1,
      interactions: 0,
      fixation_coverage: 0,
    });
    expect(evaluate(everything).reject_reason).toBe("no_consent");

    const consented = { ...everything, consent: true };
    expect(evaluate(consented).reject_reason).toBe("too_few_slots");

    const sawShelf = { ...consented, slots_observed: 9 };
    expect(evaluate(sawShelf).reject_reason).toBe("one_station");

    const twoStations = { ...sawShelf, stations_visited: 2 };
    expect(evaluate(twoStations).reject_reason).toBe("no_interaction");

    const touched = { ...twoStations, interactions: 1 };
    expect(evaluate(touched).reject_reason).toBe("low_coverage");
  });
});

describe("SessionGate.summarise", () => {
  const events: ShopperEvent[] = [
    event("station_enter", 0, "B1"),
    event("gaze", 100, "B1", { x: 10, y: 20, conf: 0.8 }),
    event("fixation", 400, "B1", { x: 10, y: 20, dur_ms: 300, slot_id: "B1S3P1", shelf_id: "B1S3" }),
    event("hover", 500, "B1", { sku_id: "SKU_005", slot_id: "B1S3P1" }),
    event("station_exit", 600, "B1"),
    event("station_enter", 700, "B2"),
    event("fixation", 1200, "B2", { x: 40, y: 20, dur_ms: 500, slot_id: null, shelf_id: "B2S2" }),
    event("pickup", 1500, "B2", { sku_id: "SKU_009", slot_id: "B2S2P1" }),
    event("add_to_cart", 1600, "B2", { sku_id: "SKU_009", slot_id: "B2S2P1" }),
    event("checkout", 2000, "B2"),
  ];

  it("derives the SPEC 4.3 quality block from the event stream", () => {
    const summary = summarise(events, { consent: true, mode: "webcam" });

    expect(summary.duration_s).toBe(2);
    expect(summary.stations_visited).toBe(2);
    // hover + pickup + add_to_cart. gaze, fixation and navigation are not
    // interactions: they are what the shopper looked at, not what they did.
    expect(summary.interactions).toBe(3);
    // 300 + 500 ms of fixation over 2000 ms of session.
    expect(summary.fixation_coverage).toBe(0.4);
  });

  it("counts distinct slots that produced a looking observation", () => {
    // One fixation on B1S3P1; the other names no slot. The hover, pickup and
    // add_to_cart on B1S3P1/B2S2P1 are interactions, not looking, and are
    // already counted by their own rule - letting them count here too would
    // mean one action satisfying two independent criteria.
    const summary = summarise(events, { consent: true, mode: "webcam" });
    expect(summary.slots_observed).toBe(1);
  });

  it("skips a fixation on bare shelf, exactly as fusion.py does", () => {
    // fusion.py: "A fixation with slot_id null landed on a shelf rather than on
    // a product slot, so it belongs to no slot and is skipped". A slot the
    // formula will never credit must not be evidence that the shelf was seen.
    const bareShelfOnly: ShopperEvent[] = [
      event("fixation", 100, "B1", { x: 0, y: 0, dur_ms: 400, slot_id: null, shelf_id: "B1S1" }),
      event("fixation", 600, "B1", { x: 0, y: 0, dur_ms: 400, slot_id: null, shelf_id: "B1S2" }),
    ];
    expect(summarise(bareShelfOnly, { consent: true, mode: "webcam" }).slots_observed).toBe(0);
  });

  it("counts a slot once however many times it was looked at", () => {
    // Spread, not volume. Re-entering a slot opens a new dwell (CursorTracker),
    // so a shopper who kept returning to one product would otherwise clear the
    // bar without ever seeing the rest of the shelf.
    const repeated: ShopperEvent[] = [
      event("cursor_dwell", 400, "B1", { slot_id: "B1S1P1", dur_ms: 300 }),
      event("cursor_dwell", 900, "B1", { slot_id: "B1S1P1", dur_ms: 900 }),
      event("cursor_dwell", 1900, "B1", { slot_id: "B1S1P1", dur_ms: 500 }),
    ];
    expect(summarise(repeated, { consent: true, mode: "cursor_only" }).slots_observed).toBe(1);
  });

  it("counts cursor dwells in a cursor_only session", () => {
    const dwells: ShopperEvent[] = [
      event("cursor_dwell", 400, "B1", { slot_id: "B1S1P1", dur_ms: 300 }),
      event("cursor_dwell", 800, "B1", { slot_id: "B1S2P1", dur_ms: 450 }),
      event("cursor_dwell", 1300, "B2", { slot_id: "B2S1P1", dur_ms: 320 }),
    ];
    expect(summarise(dwells, { consent: true, mode: "cursor_only" }).slots_observed).toBe(3);
  });

  it("ignores fixations in a cursor_only session, because fusion weights them 0", () => {
    // fusion.py's _MODE_WEIGHTS gives fixation weight 0 in cursor_only. A
    // session must not clear this gate on evidence the attention formula will
    // then discard: the gate would be admitting sessions the analysis cannot
    // use, which is the same failure as rejecting ones it could.
    const strays: ShopperEvent[] = [
      event("cursor_dwell", 400, "B1", { slot_id: "B1S1P1", dur_ms: 300 }),
      event("fixation", 800, "B1", { x: 0, y: 0, dur_ms: 400, slot_id: "B1S2P1", shelf_id: "B1S2" }),
      event("fixation", 1200, "B1", { x: 0, y: 0, dur_ms: 400, slot_id: "B1S3P1", shelf_id: "B1S3" }),
    ];
    expect(summarise(strays, { consent: true, mode: "cursor_only" }).slots_observed).toBe(1);
    // The same stream in webcam mode uses both channels, as fusion does there.
    expect(summarise(strays, { consent: true, mode: "webcam" }).slots_observed).toBe(3);
  });

  it("counts a fixation on bare shelf toward coverage", () => {
    // Coverage measures how much of the session produced usable gaze at all,
    // not how much of it landed on a product; a fixation with slot_id null is
    // still a fixation the tracker managed to resolve. This is deliberately
    // the opposite of the slots_observed rule above, and the two measure
    // different things: the tracker working, and the shelf being seen.
    const summary = summarise(events, { consent: true, mode: "webcam" });
    expect(summary.fixation_coverage).toBeGreaterThan(300 / 2000);
  });

  it("takes an explicit duration over the last event's timestamp", () => {
    const summary = summarise(events, { consent: true, mode: "webcam", duration_s: 8 });
    expect(summary.duration_s).toBe(8);
    expect(summary.fixation_coverage).toBe(0.1);
  });

  it("reports zero coverage for a session with no duration at all", () => {
    const summary = summarise([], { consent: true, mode: "cursor_only" });
    expect(summary).toEqual({
      consent: true,
      mode: "cursor_only",
      duration_s: 0,
      stations_visited: 0,
      slots_observed: 0,
      interactions: 0,
      fixation_coverage: 0,
    });
  });

  it("clamps coverage into the [0, 1] the session schema allows", () => {
    // A tracker that reports overlapping fixations, or a session whose clock
    // and event stamps disagree, must not produce a document the API refuses.
    const overlapping: ShopperEvent[] = [
      event("fixation", 100, "B1", { x: 0, y: 0, dur_ms: 5000, slot_id: null, shelf_id: null }),
      event("checkout", 1000, "B1"),
    ];
    expect(summarise(overlapping, { consent: true, mode: "webcam" }).fixation_coverage).toBe(1);
  });

  it("feeds evaluate directly", () => {
    const summary = summarise(events, { consent: true, mode: "webcam" });
    const result = evaluate(summary);

    expect(result.reject_reason).toBe("too_few_slots");
    expect(result.quality).toEqual({
      fixation_coverage: 0.4,
      stations_visited: 2,
      slots_observed: 1,
      duration_s: 2,
    });
  });
});
