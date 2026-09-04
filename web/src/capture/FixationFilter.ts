import { hitTest, type ScreenRect } from "@/store/SlotMapper";
import type { GazeSample } from "@/capture/GazeTracker";

/**
 * Raw gaze -> fixations. SPEC M2's pipeline, and the only implementation of it.
 *
 * `docs/PLAN.md` 13 overrides the SPEC here: **browser only**. There is no
 * `analytics/noise.py` twin and there must never be one - the server stores
 * fixations exactly as it receives them, so this file is the single definition
 * of what a fixation is for this project.
 *
 *     conf gate  ->  median filter (window 5)  ->  I-DT  ->  centroid  ->  hitTest
 *
 * Everything above the hitTest is pure: no React, no WebGazer, no clock of its
 * own. Time comes from the samples, so a stream can be replayed offline, from a
 * fixture, or at any speed, and produce byte-identical fixations. `hitTest`
 * lives in the caller's step (`fixationPayload`), not in the filter, so the
 * filter never needs to know a planogram exists.
 *
 * Streaming and batch are the same code: `filterFixations` is `push` in a loop
 * followed by `end`, and `push` emits a fixation the moment the shopper's gaze
 * leaves it, so the live spectator feed does not wait for checkout.
 */

/**
 * SPEC M2, step one. `GazeTracker` already drops below this before a sample is
 * published; the filter re-applies it so this module is self-contained and can
 * be tested against a fixture without a tracker. The two constants are asserted
 * equal in `web/tests/fixationFilter.test.ts` so they cannot drift apart.
 */
export const MIN_CONFIDENCE = 0.5;

/** Sliding median window, in samples. Odd, so the median is a real sample value. */
export const MEDIAN_WINDOW = 5;

/** I-DT dispersion threshold, in CSS pixels. */
export const DISPERSION_PX = 60;

/** A run shorter than this is a saccade or a glance, not a fixation. */
export const MIN_FIXATION_MS = 100;

/** How far the median window reaches either side of the sample it is centred on. */
const HALF_WINDOW = (MEDIAN_WINDOW - 1) / 2;

export interface Fixation {
  /** Centroid of the run, in CSS pixels. An exact mean - nothing is rounded. */
  x: number;
  y: number;
  /** Last sample's timestamp minus the first's. */
  dur_ms: number;
  t_start: number;
  t_end: number;
  /** How many median-filtered samples the run held. */
  n_samples: number;
}

/**
 * SPEC 4.3: the `fixation` event payload, exactly these five fields.
 *
 * A type and not an interface, like `CursorDwell`: only a type alias gets an
 * implicit index signature, and without one it cannot be handed straight to
 * `EventLogger.log`'s `Record<string, unknown>` payload.
 */
export type FixationPayload = {
  x: number;
  y: number;
  dur_ms: number;
  slot_id: string | null;
  shelf_id: string | null;
};

/** One median-filtered sample. Confidence is spent by this point. */
interface Smoothed {
  x: number;
  y: number;
  t: number;
}

/**
 * Middle value of an odd-length window.
 *
 * Every window this filter builds has length 2r+1, so the result is always one
 * of the input values and never an average of two - a rank filter, not a
 * smoother. (Given an even length it would return the upper of the two middle
 * values; nothing here produces one.)
 */
function median(values: number[]): number {
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[sorted.length >> 1];
}

export class FixationFilter {
  /**
   * The last MEDIAN_WINDOW samples that passed the confidence gate.
   * `recent[0]` is gated-stream index `first`.
   */
  private recent: GazeSample[] = [];
  private first = 0;
  /** How many samples have passed the gate, ever. The gated stream's length. */
  private accepted = 0;

  /** The open I-DT run, and its bounding box. */
  private run: Smoothed[] = [];
  private minX = 0;
  private maxX = 0;
  private minY = 0;
  private maxY = 0;

  /**
   * Feed one raw sample. Returns any fixation that this sample just closed —
   * at most one, since one sample can push at most one run over the threshold.
   *
   * The median window needs two samples of look-ahead, so a sample is smoothed
   * (and only then offered to I-DT) once two more have arrived. That is the
   * whole latency of the pipeline: about 60 ms at WebGazer's ~30 Hz.
   */
  push(sample: GazeSample): Fixation[] {
    // A non-finite coordinate would poison the run's bounding box for good.
    if (!Number.isFinite(sample.x) || !Number.isFinite(sample.y)) return [];
    if (sample.conf < MIN_CONFIDENCE) return [];

    this.recent.push(sample);
    if (this.recent.length > MEDIAN_WINDOW) {
      this.recent.shift();
      this.first += 1;
    }
    this.accepted += 1;

    const index = this.accepted - 1 - HALF_WINDOW;
    if (index < 0) return [];
    // The stream is still running, so this index has at least HALF_WINDOW
    // samples after it: only its distance from the start can shrink the window.
    return this.admit(this.smooth(index, Math.min(HALF_WINDOW, index)));
  }

  /**
   * Close the stream: smooth the samples still held back, close the open run,
   * and reset. Safe to call twice, and the filter is reusable afterwards.
   */
  end(): Fixation[] {
    const emitted: Fixation[] = [];
    const n = this.accepted;
    for (let index = Math.max(0, n - HALF_WINDOW); index < n; index += 1) {
      // Now that the end is known, the trailing windows shrink symmetrically.
      const radius = Math.min(HALF_WINDOW, index, n - 1 - index);
      emitted.push(...this.admit(this.smooth(index, radius)));
    }
    emitted.push(...this.close());

    this.recent = [];
    this.first = 0;
    this.accepted = 0;
    return emitted;
  }

  /**
   * Median of x and of y, taken independently, over the window centred on
   * `index`. The filtered point can therefore be a point no sample occupied,
   * which is the standard behaviour of a per-axis median filter and is what
   * makes it drop a single-sample spike without moving its neighbours.
   *
   * Edges: the window shrinks symmetrically (radius `min(2, i, n-1-i)`), so it
   * stays odd and centred. The first and last sample of a stream therefore pass
   * through unfiltered - there is no context either side of them to filter
   * with. A spike that lands exactly there survives, which can end a run one
   * sample early; it can never invent a fixation, only shorten one.
   */
  private smooth(index: number, radius: number): Smoothed {
    const xs: number[] = [];
    const ys: number[] = [];
    for (let k = index - radius; k <= index + radius; k += 1) {
      const sample = this.recent[k - this.first];
      xs.push(sample.x);
      ys.push(sample.y);
    }
    return {
      x: median(xs),
      y: median(ys),
      t: this.recent[index - this.first].t,
    };
  }

  /**
   * I-DT (Salvucci & Goldberg 2000). Dispersion is
   * `(max x - min x) + (max y - min y)` — the sum of the two ranges, not a
   * Euclidean radius and not a bounding-box diagonal. A 40x40 px cluster has a
   * dispersion of 80 and is two fixations, even though it fits in a 57 px
   * circle.
   *
   * Runs are greedy and maximal: extend while the dispersion holds, close when
   * the next sample would break it, and begin the next run at that sample.
   */
  private admit(smoothed: Smoothed): Fixation[] {
    if (this.run.length === 0) {
      this.startRun(smoothed);
      return [];
    }
    if (this.dispersionWith(smoothed) <= DISPERSION_PX) {
      this.extendRun(smoothed);
      return [];
    }
    const closed = this.close();
    this.startRun(smoothed);
    return closed;
  }

  private startRun(smoothed: Smoothed): void {
    this.run = [smoothed];
    this.minX = smoothed.x;
    this.maxX = smoothed.x;
    this.minY = smoothed.y;
    this.maxY = smoothed.y;
  }

  private extendRun(smoothed: Smoothed): void {
    this.run.push(smoothed);
    this.minX = Math.min(this.minX, smoothed.x);
    this.maxX = Math.max(this.maxX, smoothed.x);
    this.minY = Math.min(this.minY, smoothed.y);
    this.maxY = Math.max(this.maxY, smoothed.y);
  }

  private dispersionWith(smoothed: Smoothed): number {
    return (
      Math.max(this.maxX, smoothed.x) -
      Math.min(this.minX, smoothed.x) +
      (Math.max(this.maxY, smoothed.y) - Math.min(this.minY, smoothed.y))
    );
  }

  /** Emit the open run as a fixation, if it lasted long enough. */
  private close(): Fixation[] {
    const run = this.run;
    this.run = [];
    if (run.length === 0) return [];

    const tStart = run[0].t;
    const tEnd = run[run.length - 1].t;
    const durMs = tEnd - tStart;
    if (durMs < MIN_FIXATION_MS) return [];

    let sumX = 0;
    let sumY = 0;
    for (const smoothed of run) {
      sumX += smoothed.x;
      sumY += smoothed.y;
    }
    return [
      {
        x: sumX / run.length,
        y: sumY / run.length,
        dur_ms: durMs,
        t_start: tStart,
        t_end: tEnd,
        n_samples: run.length,
      },
    ];
  }
}

/**
 * The whole pipeline over a finished stream. Identical to feeding the same
 * samples through `FixationFilter.push` and then `end` — it is that, exactly —
 * so the fixture in `tests/fixtures/` pins the live path as well as this one.
 */
export function filterFixations(samples: Iterable<GazeSample>): Fixation[] {
  const filter = new FixationFilter();
  const fixations: Fixation[] = [];
  for (const sample of samples) fixations.push(...filter.push(sample));
  fixations.push(...filter.end());
  return fixations;
}

/**
 * Centroid -> what the shopper was looking at -> the SPEC 4.3 payload.
 *
 * A centroid on no product but inside a shelf band records that shelf (SPEC
 * M2): attention on empty eye-level space is exactly the signal that makes
 * "move this SKU up a shelf" answerable, so it must not be thrown away as a
 * miss.
 *
 * `padPx` defaults to `SlotMapper`'s 25 px, which exists for gaze: a webcam
 * estimate that lands just off a facing was still a look at that facing.
 * `CursorTracker` passes 0 instead, because a mouse pointer is exact.
 *
 * An ad fixture is neither a slot nor a shelf, so a fixation on one reports
 * both fields null. It still counts as a fixation - toward `n_fixations` and
 * toward `fixation_coverage` - it simply names no target, because SPEC 4.3
 * gives this payload no field to name one with.
 */
export function fixationPayload(
  fixation: Fixation,
  rects: ScreenRect[],
  padPx?: number,
): FixationPayload {
  const hit = hitTest(rects, fixation.x, fixation.y, padPx);
  return {
    x: fixation.x,
    y: fixation.y,
    dur_ms: fixation.dur_ms,
    slot_id: hit?.slot_id ?? null,
    shelf_id: hit?.shelf_id ?? null,
  };
}
