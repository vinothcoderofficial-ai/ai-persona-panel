import { describe, expect, it } from "vitest";
import {
  CALIBRATION_POINTS,
  CURSOR_ONLY_ERROR_FRACTION,
  VALIDATION_POINTS,
  meanValidationError,
  modeFromValidation,
} from "@/capture/calibrationMath";

describe("meanValidationError", () => {
  it("is the mean Euclidean distance from each prediction to its target", () => {
    const targets = [
      { x: 100, y: 100 },
      { x: 200, y: 100 },
      { x: 100, y: 200 },
      { x: 200, y: 200 },
    ];
    const samples = [
      { x: 103, y: 104 }, // (3,4)   -> 5
      { x: 206, y: 108 }, // (6,8)   -> 10
      { x: 100, y: 215 }, // (0,15)  -> 15
      { x: 220, y: 200 }, // (20,0)  -> 20
    ];
    // (5 + 10 + 15 + 20) / 4 = 12.5
    expect(meanValidationError(samples, targets)).toBe(12.5);
  });

  it("is 0 when every prediction lands on its target", () => {
    const targets = [
      { x: 0, y: 0 },
      { x: 10, y: 10 },
    ];
    expect(meanValidationError(targets, targets)).toBe(0);
  });

  it("refuses a sample list that does not line up with the targets", () => {
    expect(() => meanValidationError([{ x: 1, y: 1 }], [])).toThrow();
    expect(() => meanValidationError([], [])).toThrow();
  });
});

describe("modeFromValidation", () => {
  it("keeps webcam mode below the threshold", () => {
    // 12% of 1000 px is 120 px.
    expect(modeFromValidation(119.9, 1000)).toEqual({
      mode: "webcam",
      calibration_error_px: 119.9,
    });
  });

  it("switches to cursor_only above the threshold, and does not reject anyone", () => {
    expect(modeFromValidation(120.1, 1000)).toEqual({
      mode: "cursor_only",
      calibration_error_px: 120.1,
    });
  });

  it("keeps webcam at exactly 12.0% — the boundary is strictly greater than", () => {
    expect(modeFromValidation(120, 1000)).toEqual({
      mode: "webcam",
      calibration_error_px: 120,
    });
    expect(CURSOR_ONLY_ERROR_FRACTION).toBe(0.12);
  });

  it("carries the error through unrounded", () => {
    expect(modeFromValidation(84.23456789, 1440).calibration_error_px).toBe(84.23456789);
  });

  it("falls back to cursor_only when validation produced no usable number", () => {
    // A face lost through the whole of one target leaves no mean to compare.
    expect(modeFromValidation(Number.NaN, 1440)).toEqual({
      mode: "cursor_only",
      calibration_error_px: null,
    });
    expect(modeFromValidation(50, 0)).toEqual({
      mode: "cursor_only",
      calibration_error_px: 50,
    });
  });
});

describe("the point grids", () => {
  it("calibrates on 9 points and validates on 4 different ones", () => {
    expect(CALIBRATION_POINTS).toHaveLength(9);
    expect(VALIDATION_POINTS).toHaveLength(4);

    const calibration = new Set(CALIBRATION_POINTS.map((p) => `${p.x},${p.y}`));
    expect(calibration.size).toBe(9);
    for (const point of VALIDATION_POINTS) {
      expect(calibration.has(`${point.x},${point.y}`)).toBe(false);
    }
  });

  it("keeps every point on screen", () => {
    for (const point of [...CALIBRATION_POINTS, ...VALIDATION_POINTS]) {
      expect(point.x).toBeGreaterThan(0);
      expect(point.x).toBeLessThan(1);
      expect(point.y).toBeGreaterThan(0);
      expect(point.y).toBeLessThan(1);
    }
  });
});
