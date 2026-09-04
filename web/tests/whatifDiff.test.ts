import { describe, expect, it } from "vitest";
import { diffRows, interpolate } from "@/whatif/diff";

/**
 * `HeatmapDiff` animates from the previous attention vector to the new one over
 * 600 ms, and PLAN section 9 lists that animation as the first thing to cut
 * ("what-if animation (keep the number)"). So the interpolation is a pure
 * function tested on its own: whatever drives it - a frame loop, a single jump
 * to t = 1, or nothing at all - the numbers it produces are these.
 */

describe("interpolate", () => {
  const from = { B1S3P1: 0.2, B1S5P1: 0.4 };
  const to = { B1S3P1: 0.6, B1S5P1: 0.1 };

  it("is the previous vector at t = 0", () => {
    expect(interpolate(from, to, 0)).toEqual(from);
  });

  it("is the new vector at t = 1", () => {
    expect(interpolate(from, to, 1)).toEqual(to);
  });

  it("is the midpoint at t = 0.5", () => {
    const half = interpolate(from, to, 0.5);
    expect(Object.keys(half).sort()).toEqual(["B1S3P1", "B1S5P1"]);
    expect(half.B1S3P1).toBeCloseTo(0.4, 12);
    expect(half.B1S5P1).toBeCloseTo(0.25, 12);
  });

  it("treats a slot missing from the new vector as 0, never undefined or NaN", () => {
    const result = interpolate({ B1S3P2: 0.5 }, {}, 0.5);
    expect(result).toEqual({ B1S3P2: 0.25 });
    expect(Number.isNaN(result.B1S3P2)).toBe(false);
  });

  it("treats a slot missing from the previous vector as 0, never undefined or NaN", () => {
    const result = interpolate({}, { B1S3P2: 0.5 }, 0.5);
    expect(result).toEqual({ B1S3P2: 0.25 });
    expect(Number.isNaN(result.B1S3P2)).toBe(false);
  });

  it("carries both sides' slots at every t, so no bar appears or vanishes mid-sweep", () => {
    expect(Object.keys(interpolate({ A: 1 }, { B: 1 }, 0)).sort()).toEqual(["A", "B"]);
    expect(interpolate({ A: 1 }, { B: 1 }, 0)).toEqual({ A: 1, B: 0 });
    expect(interpolate({ A: 1 }, { B: 1 }, 1)).toEqual({ A: 0, B: 1 });
  });

  it("clamps t outside [0, 1] rather than extrapolating past the answer", () => {
    expect(interpolate(from, to, -3)).toEqual(from);
    expect(interpolate(from, to, 4)).toEqual(to);
  });

  it("never mutates either input", () => {
    const a = { B1S3P1: 0.2 };
    const b = { B1S3P1: 0.9 };
    interpolate(a, b, 0.5);
    expect(a).toEqual({ B1S3P1: 0.2 });
    expect(b).toEqual({ B1S3P1: 0.9 });
  });
});

describe("diffRows", () => {
  it("reports the final value and the change, whatever the animation is doing", () => {
    const rows = diffRows({ B1S3P1: 0.2 }, { B1S3P1: 0.5 }, 0.25);
    expect(rows).toHaveLength(1);
    expect(rows[0].slotId).toBe("B1S3P1");
    expect(rows[0].previous).toBe(0.2);
    expect(rows[0].value).toBe(0.5);
    expect(rows[0].delta).toBeCloseTo(0.3, 12);
    expect(rows[0].frame).toBeCloseTo(0.275, 12);
  });

  it("orders rows by slot id, so a re-run cannot reshuffle the chart", () => {
    const rows = diffRows({ B2S1P1: 0.1 }, { B1S3P1: 0.2, B2S1P1: 0.3 }, 1);
    expect(rows.map((row) => row.slotId)).toEqual(["B1S3P1", "B2S1P1"]);
  });

  it("marks a slot the previous run never reported, instead of claiming a rise from 0", () => {
    const rows = diffRows({}, { B1S3P2: 0.4 }, 1);
    expect(rows).toEqual([
      { slotId: "B1S3P2", previous: null, value: 0.4, delta: null, frame: 0.4 },
    ]);
  });
});
