import { describe, expect, it } from "vitest";
import type { Event as ShopperEvent } from "@/contracts/event.schema";
import {
  MIN_DURATION_S,
  MIN_FIXATION_COVERAGE,
  MIN_INTERACTIONS,
  MIN_STATIONS,
  REJECT_ORDER,
  evaluate,
  summarise,
  type SessionSummary,
} from "@/capture/SessionGate";

/** A session that passes every rule; each test breaks exactly one of them. */
function passing(overrides: Partial<SessionSummary> = {}): SessionSummary {
  return {
    consent: true,
    mode: "webcam",
    duration_s: 96,
    stations_visited: 3,
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
    expect(MIN_DURATION_S).toBe(45);
    expect(MIN_STATIONS).toBe(2);
    expect(MIN_INTERACTIONS).toBe(1);
    expect(MIN_FIXATION_COVERAGE).toBe(0.4);
  });

  it("enumerates the reject reasons in the order it applies them", () => {
    expect(REJECT_ORDER).toEqual([
      "no_consent",
      "too_short",
      "one_station",
      "no_interaction",
      "low_coverage",
    ]);
  });
});

describe("SessionGate.evaluate", () => {
  it("accepts a full session and reports its quality", () => {
    expect(evaluate(passing())).toEqual({
      accepted: true,
      reject_reason: null,
      quality: { fixation_coverage: 0.71, stations_visited: 3, duration_s: 96 },
    });
  });

  it("rejects 30 seconds as too_short", () => {
    const result = evaluate(passing({ duration_s: 30 }));
    expect(result.accepted).toBe(false);
    expect(result.reject_reason).toBe("too_short");
    // The quality block is still reported: a rejected session is evidence too,
    // and S19's noise dashboard plots the reasons against these numbers.
    expect(result.quality.duration_s).toBe(30);
  });

  it("takes exactly 45 seconds", () => {
    expect(evaluate(passing({ duration_s: 44.999 })).reject_reason).toBe("too_short");
    expect(evaluate(passing({ duration_s: 45 })).accepted).toBe(true);
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
      duration_s: 10,
      stations_visited: 1,
      interactions: 0,
      fixation_coverage: 0,
    });
    expect(evaluate(everything).reject_reason).toBe("no_consent");

    const consented = { ...everything, consent: true };
    expect(evaluate(consented).reject_reason).toBe("too_short");

    const longEnough = { ...consented, duration_s: 96 };
    expect(evaluate(longEnough).reject_reason).toBe("one_station");

    const twoStations = { ...longEnough, stations_visited: 2 };
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

  it("counts a fixation on bare shelf toward coverage", () => {
    // Coverage measures how much of the session produced usable gaze at all,
    // not how much of it landed on a product; a fixation with slot_id null is
    // still a fixation the tracker managed to resolve.
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

    expect(result.reject_reason).toBe("too_short");
    expect(result.quality).toEqual({
      fixation_coverage: 0.4,
      stations_visited: 2,
      duration_s: 2,
    });
  });
});
