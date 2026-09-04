import { describe, expect, it } from "vitest";
import { TRAIL_WINDOW_MS, pushGaze, visibleTrail } from "@/spectator/trail";

/**
 * SPEC M9: "a dot with a 1.5 s fading trail". The ageing is pure and takes
 * `now` as an argument, so the fade is a hand-checkable function rather than
 * something that can only be observed by watching a screen.
 */
describe("gaze trail ageing", () => {
  it("uses the 1.5 s window SPEC M9 specifies", () => {
    expect(TRAIL_WINDOW_MS).toBe(1500);
  });

  it("drops points that have aged out and fades the rest linearly", () => {
    // Hand-computed at a fixed `now`: opacity = 1 - age / 1500, and a point
    // that has reached the full window is gone, not drawn at opacity 0.
    const now = 10_000;
    const points = [
      { x: 1, y: 11, t: 8_000 }, //  age 2000 -> dropped
      { x: 2, y: 12, t: 8_500 }, //  age 1500 -> dropped, exactly at the edge
      { x: 3, y: 13, t: 8_875 }, //  age 1125 -> 0.25
      { x: 4, y: 14, t: 9_250 }, //  age  750 -> 0.5
      { x: 5, y: 15, t: 9_625 }, //  age  375 -> 0.75
      { x: 6, y: 16, t: 10_000 }, // age    0 -> 1
    ];

    const visible = visibleTrail(points, now);

    expect(visible.map((p) => p.x)).toEqual([3, 4, 5, 6]);
    expect(visible.map((p) => p.y)).toEqual([13, 14, 15, 16]);
    expect(visible.map((p) => p.ageMs)).toEqual([1125, 750, 375, 0]);
    expect(visible.map((p) => p.opacity)).toEqual([0.25, 0.5, 0.75, 1]);
  });

  it("keeps the oldest-first order so the trail can be drawn as a path", () => {
    const points = [
      { x: 1, y: 1, t: 500 },
      { x: 2, y: 2, t: 900 },
      { x: 3, y: 3, t: 1400 },
    ];
    const visible = visibleTrail(points, 1500);
    expect(visible.map((p) => p.ageMs)).toEqual([1000, 600, 100]);
    // Opacity rises towards the newest point; the head of the trail is brightest.
    for (let i = 1; i < visible.length; i += 1) {
      expect(visible[i].opacity).toBeGreaterThan(visible[i - 1].opacity);
    }
  });

  it("returns nothing once every point has aged past the window", () => {
    const points = [
      { x: 1, y: 1, t: 0 },
      { x: 2, y: 2, t: 100 },
    ];
    expect(visibleTrail(points, 5_000)).toEqual([]);
  });

  it("never reports an opacity above 1 for a sample stamped in the future", () => {
    // Clock skew between the message's arrival stamp and the render tick must
    // not produce an over-bright dot or a negative age.
    const visible = visibleTrail([{ x: 1, y: 1, t: 2_000 }], 1_000);
    expect(visible).toEqual([{ x: 1, y: 1, ageMs: 0, opacity: 1 }]);
  });

  it("honours an explicit window", () => {
    const points = [
      { x: 1, y: 1, t: 0 },
      { x: 2, y: 2, t: 400 },
    ];
    expect(visibleTrail(points, 500, 200).map((p) => p.x)).toEqual([2]);
  });

  it("pushGaze appends the newest sample and prunes what has aged out", () => {
    const start = [
      { x: 1, y: 1, t: 0 },
      { x: 2, y: 2, t: 1_400 },
    ];
    const next = pushGaze(start, { x: 3, y: 3, t: 1_600 });
    expect(next).toEqual([
      { x: 2, y: 2, t: 1_400 },
      { x: 3, y: 3, t: 1_600 },
    ]);
    // The input array is not mutated: the caller holds React state.
    expect(start).toHaveLength(2);
    expect(start[0].t).toBe(0);
  });
});
