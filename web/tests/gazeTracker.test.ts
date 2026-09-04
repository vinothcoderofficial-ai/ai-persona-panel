import { describe, expect, it, vi } from "vitest";
import {
  GazeTracker,
  MIN_GAZE_CONF,
  type GazeSample,
  type WebGazerLike,
} from "@/capture/GazeTracker";

/**
 * WebGazer cannot run in jsdom (no WebGL, no getUserMedia), so the tracker takes
 * its library through an injectable loader and the privacy contract is asserted
 * against this fake. What the fake receives is exactly what the real WebGazer
 * would receive.
 */
interface FakePrediction {
  x: number;
  y: number;
  confidence?: number;
  eyeFeatures?: unknown;
  all?: unknown;
}

function makeFakeWebGazer() {
  const held: { listener: ((data: unknown, elapsed: number) => void) | null } = {
    listener: null,
  };
  const wg = {
    showVideo: vi.fn(),
    showVideoPreview: vi.fn(),
    showFaceOverlay: vi.fn(),
    showFaceFeedbackBox: vi.fn(),
    showPredictionPoints: vi.fn(),
    saveDataAcrossSessions: vi.fn(),
    setRegression: vi.fn(),
    removeMouseEventListeners: vi.fn(),
    setGazeListener: vi.fn((fn: (data: unknown, elapsed: number) => void) => {
      held.listener = fn;
    }),
    clearGazeListener: vi.fn(),
    recordScreenPosition: vi.fn(),
    begin: vi.fn(async (): Promise<void> => {}),
    end: vi.fn(),
    stopVideo: vi.fn(),
    params: { videoElementId: "webgazerVideoFeed" } as Record<string, unknown>,
  };
  return {
    wg,
    api: wg as unknown as WebGazerLike,
    emit(data: FakePrediction | null): void {
      if (held.listener === null) throw new Error("setGazeListener was never called");
      held.listener(data, 0);
    },
  };
}

function trackerWith(fake: ReturnType<typeof makeFakeWebGazer>, t = 1234): GazeTracker {
  return new GazeTracker({ load: async () => fake.api, now: () => t });
}

describe("GazeTracker privacy contract", () => {
  it("turns the video preview and the prediction dot off, before it starts", async () => {
    const fake = makeFakeWebGazer();
    await trackerWith(fake).start();

    expect(fake.wg.showVideo).toHaveBeenCalledWith(false);
    expect(fake.wg.showPredictionPoints).toHaveBeenCalledWith(false);
    // Nothing may ever switch them back on.
    for (const call of fake.wg.showVideo.mock.calls) expect(call[0]).toBe(false);
    for (const call of fake.wg.showPredictionPoints.mock.calls) expect(call[0]).toBe(false);

    // WebGazer defaults every one of these to true, so "applied" means "applied
    // before begin() opens the camera".
    expect(fake.wg.showVideo.mock.invocationCallOrder[0]).toBeLessThan(
      fake.wg.begin.mock.invocationCallOrder[0],
    );
    expect(fake.wg.showPredictionPoints.mock.invocationCallOrder[0]).toBeLessThan(
      fake.wg.begin.mock.invocationCallOrder[0],
    );
  });

  it("never asks WebGazer to save data across sessions", async () => {
    const fake = makeFakeWebGazer();
    await trackerWith(fake).start();

    // WebGazer's default is true and it persists to localforage, so this must be
    // called, and called with false, before begin().
    expect(fake.wg.saveDataAcrossSessions).toHaveBeenCalledWith(false);
    for (const call of fake.wg.saveDataAcrossSessions.mock.calls) expect(call[0]).toBe(false);
    expect(fake.wg.saveDataAcrossSessions.mock.invocationCallOrder[0]).toBeLessThan(
      fake.wg.begin.mock.invocationCallOrder[0],
    );
  });

  it("emits samples with exactly x, y, conf and t — nothing from the camera", async () => {
    const fake = makeFakeWebGazer();
    const tracker = trackerWith(fake, 5150);
    const seen: GazeSample[] = [];
    tracker.subscribe((sample) => seen.push(sample));
    await tracker.start();

    // The real prediction object carries eye image patches. They must not
    // survive the hop into a sample.
    fake.emit({
      x: 640.25,
      y: 360.5,
      eyeFeatures: { left: { patch: "pixels" }, right: { patch: "pixels" } },
      all: [{ x: 640.25, y: 360.5 }],
    });

    expect(seen).toHaveLength(1);
    expect(Object.keys(seen[0]).sort()).toEqual(["conf", "t", "x", "y"]);
    expect(seen[0]).toEqual({ x: 640.25, y: 360.5, conf: 1, t: 5150 });
  });

  it("stops the tracker and lets nothing through afterwards", async () => {
    const fake = makeFakeWebGazer();
    const tracker = trackerWith(fake);
    const seen: GazeSample[] = [];
    tracker.subscribe((sample) => seen.push(sample));
    await tracker.start();

    tracker.stop();

    expect(fake.wg.clearGazeListener).toHaveBeenCalled();
    expect(fake.wg.stopVideo).toHaveBeenCalled();
    expect(fake.wg.end).toHaveBeenCalled();
    expect(tracker.running).toBe(false);

    // WebGazer's loop can fire once more after end(); that sample goes nowhere.
    fake.emit({ x: 1, y: 2 });
    expect(seen).toHaveLength(0);
  });
});

describe("GazeTracker sample filter", () => {
  it("drops samples below the confidence floor and keeps the rest", async () => {
    const fake = makeFakeWebGazer();
    const tracker = trackerWith(fake, 42);
    const seen: GazeSample[] = [];
    tracker.subscribe((sample) => seen.push(sample));
    await tracker.start();

    expect(MIN_GAZE_CONF).toBe(0.5);

    fake.emit({ x: 10, y: 20, confidence: 0.49 }); // dropped
    fake.emit({ x: 11, y: 21, confidence: 0 }); // dropped
    fake.emit({ x: 12, y: 22, confidence: 0.5 }); // kept: the rule is conf < 0.5
    fake.emit({ x: 13, y: 23, confidence: 0.9 }); // kept

    expect(seen).toEqual([
      { x: 12, y: 22, conf: 0.5, t: 42 },
      { x: 13, y: 23, conf: 0.9, t: 42 },
    ]);
  });

  it("scores a lost eye as zero confidence, so it is dropped", async () => {
    const fake = makeFakeWebGazer();
    const tracker = trackerWith(fake);
    const seen: GazeSample[] = [];
    tracker.subscribe((sample) => seen.push(sample));
    await tracker.start();

    fake.emit({ x: 5, y: 5, eyeFeatures: { left: null, right: { patch: "pixels" } } });
    fake.emit(null); // no face at all
    fake.emit({ x: Number.NaN, y: 5 }); // a regression that blew up

    expect(seen).toEqual([]);
  });

  it("stops delivering to an unsubscribed listener", async () => {
    const fake = makeFakeWebGazer();
    const tracker = trackerWith(fake);
    const seen: GazeSample[] = [];
    const unsubscribe = tracker.subscribe((sample) => seen.push(sample));
    await tracker.start();

    fake.emit({ x: 1, y: 1 });
    unsubscribe();
    fake.emit({ x: 2, y: 2 });

    expect(seen.map((sample) => sample.x)).toEqual([1]);
  });
});

describe("GazeTracker calibration input", () => {
  it("hands calibration clicks to WebGazer and trains on nothing else", async () => {
    const fake = makeFakeWebGazer();
    const tracker = trackerWith(fake);
    await tracker.start();

    // Every click in the store would otherwise retrain the model behind the
    // shopper's back, after the calibration error was measured.
    expect(fake.wg.removeMouseEventListeners).toHaveBeenCalled();

    tracker.record(120, 340);
    expect(fake.wg.recordScreenPosition).toHaveBeenCalledWith(120, 340, "click");
  });

  it("releases the camera when stop() lands while it is still opening", async () => {
    // The shopper closes the tab, or the flow unmounts, in the seconds between
    // "allow" and the first prediction. The camera must not survive that.
    const fake = makeFakeWebGazer();
    let openCamera: () => void = () => {};
    const opening = new Promise<void>((resolve) => {
      openCamera = resolve;
    });
    fake.wg.begin = vi.fn(() => opening);

    const tracker = trackerWith(fake);
    const started = tracker.start();
    for (let i = 0; i < 5 && fake.wg.begin.mock.calls.length === 0; i += 1) {
      await Promise.resolve();
    }
    expect(fake.wg.begin).toHaveBeenCalledTimes(1);

    tracker.stop();
    openCamera();
    await started;

    expect(fake.wg.end).toHaveBeenCalled();
    expect(fake.wg.stopVideo).toHaveBeenCalled();
    expect(tracker.running).toBe(false);
  });

  it("starts once, however many times start() is called", async () => {
    const fake = makeFakeWebGazer();
    const tracker = trackerWith(fake);
    await tracker.start();
    await tracker.start();

    expect(fake.wg.begin).toHaveBeenCalledTimes(1);
    expect(tracker.running).toBe(true);
    tracker.stop();
  });
});
