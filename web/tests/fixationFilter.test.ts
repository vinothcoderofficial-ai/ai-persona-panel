import { describe, expect, it } from "vitest";
import jitteryGaze from "../../tests/fixtures/jittery_gaze.json";
import expectedFixations from "../../tests/fixtures/expected_fixations.json";
import { MIN_GAZE_CONF, type GazeSample } from "@/capture/GazeTracker";
import {
  DISPERSION_PX,
  FixationFilter,
  MEDIAN_WINDOW,
  MIN_CONFIDENCE,
  MIN_FIXATION_MS,
  filterFixations,
  type Fixation,
} from "@/capture/FixationFilter";

const noisy: GazeSample[] = jitteryGaze.samples;

/** A stream of `count` samples `stepMs` apart, all at the same point. */
function held(
  count: number,
  x: number,
  y: number,
  from: number,
  stepMs = 20,
  conf = 0.9,
): GazeSample[] {
  return Array.from({ length: count }, (_, k) => ({
    x,
    y,
    conf,
    t: from + k * stepMs,
  }));
}

describe("the filter constants are pinned", () => {
  // METHODOLOGY.md cites these and the Day-7 noise freeze pins them; a silent
  // change to any one of them silently changes every attention number.
  it("holds the SPEC M2 values", () => {
    expect(MIN_CONFIDENCE).toBe(0.5);
    expect(MEDIAN_WINDOW).toBe(5);
    expect(DISPERSION_PX).toBe(60);
    expect(MIN_FIXATION_MS).toBe(100);
  });

  it("gates confidence at exactly the same value GazeTracker does", () => {
    // GazeTracker already drops below this; the filter re-applies the same
    // threshold so it is testable on its own. The two must never drift apart.
    expect(MIN_CONFIDENCE).toBe(MIN_GAZE_CONF);
  });
});

describe("the acceptance fixture", () => {
  it("turns tests/fixtures/jittery_gaze.json into exactly expected_fixations.json", () => {
    expect(filterFixations(noisy)).toEqual(expectedFixations.fixations);
  });

  it("uses an input that actually exercises every stage", () => {
    // If the fixture ever loses its low-confidence samples, its spike or its
    // sub-threshold cluster, the acceptance test above stops proving anything.
    expect(noisy.filter((sample) => sample.conf < MIN_CONFIDENCE)).toHaveLength(4);
    expect(noisy.some((sample) => sample.x === 700 && sample.y === 100)).toBe(true);
    expect(noisy).toHaveLength(36);
    expect(expectedFixations.fixations).toHaveLength(3);
  });
});

describe("the median filter", () => {
  it("removes a single-sample spike without shifting its neighbours", () => {
    const clean = held(9, 100, 200, 0);
    const spiked = clean.map((sample, i) =>
      i === 4 ? { ...sample, x: 900, y: 50 } : sample,
    );

    const fromClean = filterFixations(clean);
    const fromSpiked = filterFixations(spiked);

    // Not "close to" the clean answer - identical to it. Any neighbour the
    // filter dragged toward the spike would move the centroid off (100, 200).
    expect(fromSpiked).toEqual(fromClean);
    expect(fromSpiked).toEqual([
      { x: 100, y: 200, dur_ms: 160, t_start: 0, t_end: 160, n_samples: 9 },
    ]);
  });

  it("does not let a spike split the fixation it landed in", () => {
    const spiked = held(9, 100, 200, 0);
    spiked[4] = { x: 900, y: 50, conf: 0.9, t: 80 };

    // One fixation over all nine samples, not two either side of the spike.
    expect(filterFixations(spiked).map((f) => f.n_samples)).toEqual([9]);
  });
});

describe("the 100 ms minimum", () => {
  it("rejects a cluster of 99 ms and accepts one of exactly 100 ms", () => {
    const justUnder = held(6, 400, 400, 0);
    justUnder[5] = { x: 400, y: 400, conf: 0.9, t: 99 };
    expect(filterFixations(justUnder)).toEqual([]);

    const exactly = held(6, 400, 400, 0);
    expect(exactly[5].t).toBe(100);
    expect(filterFixations(exactly)).toEqual([
      { x: 400, y: 400, dur_ms: 100, t_start: 0, t_end: 100, n_samples: 6 },
    ]);
  });

  it("gives a single sample no duration and therefore no fixation", () => {
    expect(filterFixations(held(1, 10, 10, 0))).toEqual([]);
    expect(filterFixations([])).toEqual([]);
  });
});

describe("the 60 px dispersion threshold", () => {
  /** Eight samples at the origin, then eight `apart` px to the right. */
  function step(apart: number): GazeSample[] {
    return [...held(8, 0, 0, 0), ...held(8, apart, 0, 160)];
  }

  it("keeps a run whose dispersion is exactly 60 px", () => {
    expect(filterFixations(step(DISPERSION_PX))).toEqual([
      { x: 30, y: 0, dur_ms: 300, t_start: 0, t_end: 300, n_samples: 16 },
    ]);
  });

  it("splits the run at 61 px", () => {
    expect(filterFixations(step(DISPERSION_PX + 1))).toEqual([
      { x: 0, y: 0, dur_ms: 140, t_start: 0, t_end: 140, n_samples: 8 },
      { x: 61, y: 0, dur_ms: 140, t_start: 160, t_end: 300, n_samples: 8 },
    ]);
  });

  it("measures dispersion as the sum of the x and y ranges, not a radius", () => {
    // Salvucci & Goldberg: (max x - min x) + (max y - min y). A 40 x 40 box has
    // a dispersion of 80 and must split, even though its diagonal is only 57 px
    // and a Euclidean reading would keep it in one piece.
    const boxed = [...held(8, 0, 0, 0), ...held(8, 40, 40, 160)];
    expect(filterFixations(boxed).map((f) => f.n_samples)).toEqual([8, 8]);
  });
});

describe("the confidence gate", () => {
  /** Seven samples on target, three off it, seven back on target. */
  function withBurst(burstConf: number): GazeSample[] {
    return [
      ...held(7, 0, 0, 0),
      ...held(3, 400, 400, 140, 20, burstConf),
      ...held(7, 0, 0, 200),
    ];
  }

  it("drops conf < 0.5 before the median filter ever sees it", () => {
    // The burst is dropped, so the stream is fourteen samples on one point with
    // a 60 ms hole in it: one fixation spanning the hole.
    expect(filterFixations(withBurst(0.49))).toEqual([
      { x: 0, y: 0, dur_ms: 320, t_start: 0, t_end: 320, n_samples: 14 },
    ]);
  });

  it("keeps conf of exactly 0.5, which changes the answer", () => {
    // Same points, same timestamps, one hundredth more confidence: now the
    // burst survives the median filter and breaks the run in two. This is what
    // makes the test above a test of the gate rather than of the median filter.
    expect(filterFixations(withBurst(MIN_CONFIDENCE))).toEqual([
      { x: 0, y: 0, dur_ms: 120, t_start: 0, t_end: 120, n_samples: 7 },
      { x: 0, y: 0, dur_ms: 120, t_start: 200, t_end: 320, n_samples: 7 },
    ]);
  });

  it("does not let a dropped sample split the fixation around it", () => {
    // The 40 ms hole the fixture's dropped sample leaves inside fixation A.
    expect(expectedFixations.fixations[0].n_samples).toBe(10);
    expect(expectedFixations.fixations[0].dur_ms).toBe(200);
  });
});

describe("streaming", () => {
  it("emits fixations as the stream runs, not only at the end", () => {
    const filter = new FixationFilter();
    const duringPush: Fixation[] = [];
    for (const sample of noisy) duringPush.push(...filter.push(sample));
    const atEnd = filter.end();

    // The first two fixations close when the shopper looks away from them; only
    // the last one - still open when the stream stops - waits for end().
    expect(duringPush).toEqual(expectedFixations.fixations.slice(0, 2));
    expect(atEnd).toEqual(expectedFixations.fixations.slice(2));
  });

  it("is reusable after end()", () => {
    const filter = new FixationFilter();
    for (const sample of noisy) filter.push(sample);
    filter.end();

    for (const sample of held(6, 800, 100, 0)) filter.push(sample);
    expect(filter.end()).toEqual([
      { x: 800, y: 100, dur_ms: 100, t_start: 0, t_end: 100, n_samples: 6 },
    ]);
  });
});
