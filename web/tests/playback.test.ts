import { describe, expect, it } from "vitest";
import type { AiTraceTurn } from "@/ai/client";
import { stateAt, stationsOf } from "@/panel/playback";

/**
 * Stepping through one synthetic shopper's trip.
 *
 * Pure, and separate from the screen, because the thing worth getting right
 * here is arithmetic rather than layout: what is in the cart at step 7, which
 * bay the shopper is standing at, and whether the trip is over. A player that
 * gets any of those wrong shows a persona buying something it did not buy —
 * on the one screen whose whole job is to be believable evidence that the
 * synthetic panel shops the same store the real one does.
 */

function turn(partial: Partial<AiTraceTurn> & { turn: number }): AiTraceTurn {
  return {
    station_id: "B1",
    action: "look",
    target: null,
    reason: "",
    time_left_s: 30,
    ...partial,
  };
}

const TURNS: AiTraceTurn[] = [
  turn({ turn: 1, action: "look", target: "B1S1P1", reason: "spotted the promo" }),
  turn({ turn: 2, action: "pickup", target: "B1S1P1", reason: "checking the pack" }),
  turn({ turn: 3, action: "add_to_cart", target: "B1S1P1", reason: "taking it" }),
  turn({ turn: 4, action: "next_station", target: null, reason: "moving on" }),
  turn({ turn: 5, station_id: "B2", action: "add_to_cart", target: "B2S1P1", reason: "this too" }),
  turn({ turn: 6, station_id: "B2", action: "checkout", target: null, reason: "done" }),
];

describe("where the shopper is, and what it is doing", () => {
  it("shows the first turn at index 0", () => {
    const state = stateAt(TURNS, 0);

    expect(state.turn?.action).toBe("look");
    expect(state.targetSlotId).toBe("B1S1P1");
    expect(state.stationId).toBe("B1");
    expect(state.reason).toBe("spotted the promo");
  });

  it("follows the shopper to the next bay", () => {
    expect(stateAt(TURNS, 4).stationId).toBe("B2");
  });

  it("reports no target for a turn that has none", () => {
    // `next_station` and `checkout` are untargeted. Highlighting the previous
    // slot through them would show the shopper still staring at a shelf it has
    // walked away from.
    expect(stateAt(TURNS, 3).targetSlotId).toBeNull();
  });
});

describe("the cart is what was actually added", () => {
  it("is empty before anything is added", () => {
    expect(stateAt(TURNS, 0).cartSlotIds).toEqual([]);
  });

  it("does not fill on pickup — picking a pack up is not buying it", () => {
    expect(stateAt(TURNS, 1).cartSlotIds).toEqual([]);
  });

  it("fills on add_to_cart, including the turn being shown", () => {
    expect(stateAt(TURNS, 2).cartSlotIds).toEqual(["B1S1P1"]);
  });

  it("accumulates across stations", () => {
    expect(stateAt(TURNS, 5).cartSlotIds).toEqual(["B1S1P1", "B2S1P1"]);
  });

  it("keeps a repeated add as two lines, not one", () => {
    const repeated = [...TURNS, turn({ turn: 7, station_id: "B2", action: "add_to_cart", target: "B2S1P1" })];

    expect(stateAt(repeated, 6).cartSlotIds).toEqual(["B1S1P1", "B2S1P1", "B2S1P1"]);
  });
});

describe("the ends of the trip", () => {
  it("clamps an index below zero to the first turn", () => {
    expect(stateAt(TURNS, -5).index).toBe(0);
  });

  it("clamps an index past the end to the last turn", () => {
    const state = stateAt(TURNS, 99);

    expect(state.index).toBe(5);
    expect(state.turn?.action).toBe("checkout");
  });

  it("is done only on the last turn", () => {
    expect(stateAt(TURNS, 4).done).toBe(false);
    expect(stateAt(TURNS, 5).done).toBe(true);
  });

  it("handles a trip with no turns at all without inventing one", () => {
    const state = stateAt([], 0);

    expect(state.turn).toBeNull();
    expect(state.targetSlotId).toBeNull();
    expect(state.stationId).toBeNull();
    expect(state.cartSlotIds).toEqual([]);
    expect(state.done).toBe(true);
  });
});

describe("stationsOf", () => {
  it("lists the bays this shopper stood at, in order, without repeats", () => {
    expect(stationsOf(TURNS)).toEqual(["B1", "B2"]);
  });

  it("is empty for a trip with no turns", () => {
    expect(stationsOf([])).toEqual([]);
  });
});
