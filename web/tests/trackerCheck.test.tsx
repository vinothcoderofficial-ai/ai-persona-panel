import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CHECK_SECONDS } from "@/capture/TrackerCheck";
import {
  click,
  find,
  has,
  installCaptureEnvironment,
  onTarget,
  restoreCaptureEnvironment,
  runCaptureFlow,
  text,
  wayOff,
} from "./captureRunner";

/**
 * W2 (B): the shopper may ask to see the tracker working - during setup, and
 * only during setup.
 *
 * CLAUDE.md and SPEC risk row 10 forbid a gaze dot on the shopper's screen:
 * people stare at the dot, and a stared-at dot is not shopping data. That rule
 * is about measurement. This screen runs before `POST /sessions` exists, before
 * an EventLogger, before a socket - nothing here is recorded, so staring at
 * this dot corrupts nothing, and it is the only way a person can satisfy
 * themselves that the thing measuring them actually follows their eyes.
 *
 * The distinction is exactly one screen wide, so this file pins both halves:
 * the check is offered while the tracker is still the capture flow's own, and
 * it is gone the instant the tracker is handed to the store.
 */

const SRC = resolve(dirname(fileURLToPath(import.meta.url)), "..", "src");

function walk(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) out.push(...walk(path));
    else if (/\.tsx?$/.test(name)) out.push(path);
  }
  return out;
}

describe("the optional tracker check", () => {
  beforeEach(() => {
    installCaptureEnvironment();
  });

  afterEach(() => {
    restoreCaptureEnvironment();
  });

  it("is offered, not forced - the shopper can walk straight past it", async () => {
    const run = await runCaptureFlow(onTarget);

    expect(has(run.view.container, "done-check")).toBe(true);
    // Skipping it is one click on the button that was always there.
    click(run.view.container, "done-start");
    expect(run.result().mode).toBe("webcam");

    run.result().tracker?.stop();
    run.view.unmount();
  });

  it("is not offered when there is no camera left to check", async () => {
    const run = await runCaptureFlow(wayOff);

    // cursor_only released the camera at the verdict. Offering a gaze check
    // with no tracker behind it would be a button that lies.
    expect(has(run.view.container, "done-check")).toBe(false);

    run.view.unmount();
  });

  it("draws the dot and a live readout of what the tracker is producing", async () => {
    const run = await runCaptureFlow(onTarget);
    click(run.view.container, "done-check");

    expect(has(run.view.container, "tracker-check-readout")).toBe(true);

    await act(async () => {
      for (let n = 0; n < 12; n += 1) run.fake.emit(640, 400);
    });

    const dot = find(run.view.container, "tracker-check-dot");
    expect(dot.style.left).toBe("640px");
    expect(dot.style.top).toBe("400px");

    const readout = text(run.view.container, "tracker-check-readout");
    expect(readout).toContain("12");
    expect(readout).toContain("100%");

    click(run.view.container, "tracker-check-done");
    click(run.view.container, "done-start");
    run.result().tracker?.stop();
    run.view.unmount();
  });

  it("ends on its own, without being dismissed", async () => {
    const run = await runCaptureFlow(onTarget);
    click(run.view.container, "done-check");
    expect(has(run.view.container, "tracker-check-readout")).toBe(true);

    await act(async () => {
      run.fake.emit(100, 100);
      vi.advanceTimersByTime(CHECK_SECONDS * 1000);
    });

    expect(has(run.view.container, "tracker-check-dot")).toBe(false);
    expect(has(run.view.container, "tracker-check-readout")).toBe(false);
    expect(has(run.view.container, "done-start")).toBe(true);

    click(run.view.container, "done-start");
    run.result().tracker?.stop();
    run.view.unmount();
  });

  it("can be dismissed early, and leaves the measurement exactly as it was", async () => {
    const run = await runCaptureFlow(onTarget);
    click(run.view.container, "done-check");
    click(run.view.container, "tracker-check-done");

    expect(has(run.view.container, "tracker-check-dot")).toBe(false);
    expect(has(run.view.container, "done-accuracy")).toBe(true);
    // The check reads the tracker. It never retrains it, and it never touches
    // the outcome that validation already decided.
    click(run.view.container, "done-start");
    expect(run.result().mode).toBe("webcam");
    expect(run.result().calibration_error_px ?? Number.NaN).toBeCloseTo(0, 6);
    expect(run.fake.wg.recordScreenPosition.mock.calls).toHaveLength(45); // calibration only

    run.result().tracker?.stop();
    run.view.unmount();
  });

  it("is gone the moment the store owns the tracker", async () => {
    const run = await runCaptureFlow(onTarget);
    click(run.view.container, "done-start");

    // Shopping has started: the tracker belongs to PlanogramScene now, and
    // there is no route left from this screen to a live dot.
    expect(has(run.view.container, "done-check")).toBe(false);

    run.result().tracker?.stop();
    run.view.unmount();
  });
});

describe("the check cannot be tidied into the shopping screen", () => {
  it("is imported by nothing under src/store", () => {
    for (const path of walk(join(SRC, "store"))) {
      const source = readFileSync(path, "utf8");
      expect(
        source,
        `${relative(SRC, path).split(sep).join("/")} must not reach TrackerCheck`,
      ).not.toMatch(/TrackerCheck/);
    }
  });

  it("lives in capture/, where no session exists yet", () => {
    expect(statSync(join(SRC, "capture", "TrackerCheck.tsx")).isFile()).toBe(true);
  });
});
