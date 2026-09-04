/**
 * The gaze trail's ageing, as a pure function.
 *
 * SPEC M9 asks for "the latest gaze as a dot with a 1.5 s fading trail". The
 * fade is the whole of the logic, so it lives here rather than inside the
 * component: `visibleTrail` is given the points and the current time and
 * returns exactly what should be drawn and at what opacity. Nothing in this
 * file reads a clock, which is what makes the fade checkable by hand in a test
 * instead of only by watching a screen.
 *
 * These are the *spectator's* copy of the shopper's gaze, rebuilt from the
 * `latest_gaze` field of SPEC 4.7 messages. It is never on the shopper's own
 * screen (CLAUDE.md: people stare at their own dot and corrupt the data).
 */

/** SPEC M9: the trail is 1.5 seconds long. */
export const TRAIL_WINDOW_MS = 1500;

/** One gaze position, stamped when the spectator received it. */
export interface GazePoint {
  x: number;
  y: number;
  /** Arrival time on the spectator's clock, in milliseconds. */
  t: number;
}

/** A point that should be drawn, with the opacity its age has earned it. */
export interface TrailPoint {
  x: number;
  y: number;
  ageMs: number;
  /** 1 at the head of the trail, falling linearly to 0 at the window's edge. */
  opacity: number;
}

/**
 * The points still inside the window at `now`, oldest first.
 *
 * A point that has reached the full window is dropped rather than drawn at
 * opacity 0: an invisible element that still counts as "a gaze point" is the
 * kind of thing that later turns into an off-by-one in a screenshot.
 *
 * A point stamped in the future - message arrival and render tick can disagree
 * by a frame - is treated as age 0 rather than allowed to produce a negative
 * age and an over-bright dot.
 */
export function visibleTrail(
  points: readonly GazePoint[],
  now: number,
  windowMs: number = TRAIL_WINDOW_MS,
): TrailPoint[] {
  const visible: TrailPoint[] = [];
  for (const point of points) {
    const ageMs = Math.max(0, now - point.t);
    if (ageMs >= windowMs) continue;
    visible.push({ x: point.x, y: point.y, ageMs, opacity: 1 - ageMs / windowMs });
  }
  return visible;
}

/**
 * Append the newest sample and drop whatever it pushed out of the window.
 *
 * Returns a new array: the caller holds this in React state, and mutating it
 * in place would not re-render.
 */
export function pushGaze(
  points: readonly GazePoint[],
  point: GazePoint,
  windowMs: number = TRAIL_WINDOW_MS,
): GazePoint[] {
  const kept = points.filter((candidate) => point.t - candidate.t < windowMs);
  kept.push(point);
  return kept;
}
