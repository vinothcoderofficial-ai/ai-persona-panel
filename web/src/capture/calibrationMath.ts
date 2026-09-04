import type { Session } from "@/contracts/session.schema";

/**
 * SPEC M2: a mean validation error above 12% of the screen width means the
 * webcam signal is not good enough to trust, so the session carries on in
 * `cursor_only`. Nobody is turned away.
 */
export const CURSOR_ONLY_ERROR_FRACTION = 0.12;

/** A point on the screen, in CSS pixels — or, for the grids below, a fraction of it. */
export interface Point {
  x: number;
  y: number;
}

export interface ValidationOutcome {
  mode: Session["mode"];
  calibration_error_px: number | null;
}

/**
 * The 9 calibration points and the 4 validation points, as fractions of the
 * viewport. The validation points sit between the calibration ones on purpose:
 * measuring the error at a point the model was trained on would measure
 * nothing.
 */
export const CALIBRATION_POINTS: readonly Point[] = [0.1, 0.5, 0.9].flatMap((y) =>
  [0.1, 0.5, 0.9].map((x) => ({ x, y })),
);

export const VALIDATION_POINTS: readonly Point[] = [0.3, 0.7].flatMap((y) =>
  [0.3, 0.7].map((x) => ({ x, y })),
);

/**
 * Mean Euclidean distance, in pixels, between each predicted point and the
 * validation target it was measured against.
 */
export function meanValidationError(
  samples: readonly Point[],
  targets: readonly Point[],
): number {
  if (targets.length === 0) {
    throw new Error("meanValidationError needs at least one target");
  }
  if (samples.length !== targets.length) {
    throw new Error(
      `meanValidationError got ${samples.length} predictions for ${targets.length} targets`,
    );
  }
  let total = 0;
  for (let i = 0; i < targets.length; i += 1) {
    total += Math.hypot(samples[i].x - targets[i].x, samples[i].y - targets[i].y);
  }
  return total / targets.length;
}

/**
 * Turn the measured error into the session's `mode` and `calibration_error_px`.
 *
 * The comparison is **strictly greater than**: an error of exactly 12.0% of the
 * screen width still counts as webcam. The error itself is carried through
 * unrounded — the noise dashboard plots the distribution of these.
 *
 * A validation that produced no usable number (a face lost for a whole target,
 * a zero-width screen) falls back to `cursor_only` rather than passing a NaN
 * off as a good calibration.
 */
export function modeFromValidation(
  meanErrorPx: number,
  screenWidthPx: number,
): ValidationOutcome {
  const error = Number.isFinite(meanErrorPx) ? meanErrorPx : null;
  if (error === null) return { mode: "cursor_only", calibration_error_px: null };
  if (!Number.isFinite(screenWidthPx) || screenWidthPx <= 0) {
    return { mode: "cursor_only", calibration_error_px: error };
  }
  const threshold = CURSOR_ONLY_ERROR_FRACTION * screenWidthPx;
  return {
    mode: error > threshold ? "cursor_only" : "webcam",
    calibration_error_px: error,
  };
}
