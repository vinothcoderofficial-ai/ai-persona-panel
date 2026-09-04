/**
 * The interpolation behind `HeatmapDiff`, kept pure and kept separate.
 *
 * PLAN section 9's drop order opens with *"what-if animation (keep the
 * number)"*. So the animation is one parameter - `t` - of a function that is
 * correct at every value of it, and the component that draws the bars can be
 * driven by a frame loop, by a single jump to `t = 1`, or by nothing at all,
 * without any of the figures changing.
 */

export type Attention = Record<string, number>;

function clamp01(t: number): number {
  if (!Number.isFinite(t)) return 1;
  if (t <= 0) return 0;
  if (t >= 1) return 1;
  return t;
}

function union(from: Attention, to: Attention): string[] {
  return [...new Set([...Object.keys(from), ...Object.keys(to)])].sort();
}

/**
 * The attention vector `t` of the way from `from` to `to`.
 *
 * A slot present on only one side is 0 on the other, never `undefined` and
 * never `NaN`: an unfixated slot has probability 0, and the union of both key
 * sets is carried at every `t` so no bar appears or vanishes mid-sweep.
 *
 * Written as `(1 - t) * a + t * b` rather than `a + (b - a) * t` so the
 * endpoints are exact in floating point: at `t = 1` this is `b` itself, not `b`
 * plus a rounding error. The number that settles on screen is the number the
 * server sent.
 */
export function interpolate(from: Attention, to: Attention, t: number): Attention {
  const fraction = clamp01(t);
  const result: Attention = {};
  for (const slotId of union(from, to)) {
    const a = from[slotId] ?? 0;
    const b = to[slotId] ?? 0;
    result[slotId] = (1 - fraction) * a + fraction * b;
  }
  return result;
}

export interface DiffRow {
  slotId: string;
  /** What the run before this one reported, or null if it reported nothing. */
  previous: number | null;
  /** This run's figure. Never interpolated - this is the answer. */
  value: number;
  /** value - previous, or null when there is no previous to subtract. */
  delta: number | null;
  /** Where the bar is drawn this frame. */
  frame: number;
}

/**
 * One row per slot, sorted by slot id so a re-run cannot reshuffle the chart
 * under the viewer.
 *
 * `value` and `delta` are the run's own numbers and do not depend on `t`;
 * only `frame` does. A slot the previous run never mentioned gets
 * `previous: null` rather than 0, because "we did not measure this before" and
 * "nobody looked at it" are different claims and only one of them is true.
 */
export function diffRows(previous: Attention, next: Attention, t: number): DiffRow[] {
  const frames = interpolate(previous, next, t);
  return union(previous, next).map((slotId) => {
    const before = Object.prototype.hasOwnProperty.call(previous, slotId)
      ? previous[slotId]
      : null;
    const value = next[slotId] ?? 0;
    return {
      slotId,
      previous: before,
      value,
      delta: before === null ? null : value - before,
      frame: frames[slotId] ?? 0,
    };
  });
}

/**
 * Whether this viewer has asked their system not to animate things.
 *
 * Defensive about `matchMedia` because jsdom and older embedded browsers do not
 * all have it; not having it means "no preference expressed", which is the
 * animated default.
 */
export function prefersReducedMotion(): boolean {
  try {
    if (typeof window.matchMedia !== "function") return false;
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
}
