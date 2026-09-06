import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CalibrationReport, calibrationReportText } from "@/capture/CalibrationReport";
import { CURSOR_ONLY_ERROR_FRACTION } from "@/capture/calibrationMath";
import { CaptureFlow } from "@/capture/CaptureFlow";
import {
  click,
  installCamera,
  installCaptureEnvironment,
  mount,
  onTarget,
  restoreCaptureEnvironment,
  runCaptureFlow,
  settle,
  text,
  wayOff,
} from "./captureRunner";

/**
 * W2 (A): the shopper is told the number they were judged on.
 *
 * `calibration_error_px` is measured on the four validation points, it decides
 * the session's `mode`, and it is written into the session document - and until
 * now it was displayed to nobody. The person being measured got a bare verdict,
 * and the standard they were judged against (12% of screen width, SPEC M2 and
 * `CURSOR_ONLY_ERROR_FRACTION`) was never stated to them at all.
 *
 * So both branches of that verdict have to say three things: what was measured,
 * what it was compared with, and what follows from it. This file is the check
 * that neither branch can quietly go back to being a bare verdict.
 */

describe("the sentence a shopper is shown at the verdict", () => {
  it("states the measured error, the same error as a percentage, and the cut-off", () => {
    const sentence = calibrationReportText(
      { mode: "webcam", calibration_error_px: 84 },
      1440,
    );

    expect(sentence).toContain("84 pixels");
    expect(sentence).toContain("5.8%"); // 84 / 1440
    expect(sentence).toContain("12%"); // CURSOR_ONLY_ERROR_FRACTION
    expect(sentence).toMatch(/eye tracking/i);
  });

  it("states the same three things when the verdict goes the other way", () => {
    const sentence = calibrationReportText(
      { mode: "cursor_only", calibration_error_px: 249.4 },
      1440,
    );

    expect(sentence).toContain("249 pixels");
    expect(sentence).toContain("17.3%"); // 249.4 / 1440
    expect(sentence).toContain("12%");
    expect(sentence).toMatch(/mouse/i);
  });

  it("quotes the threshold from the constant, not from a number typed in the copy", () => {
    // If SPEC M2 ever moves the cut-off, the sentence moves with it rather than
    // telling the shopper a limit that is no longer the one being applied.
    const sentence = calibrationReportText(
      { mode: "webcam", calibration_error_px: 10 },
      1000,
    );
    expect(sentence).toContain(`${Math.round(CURSOR_ONLY_ERROR_FRACTION * 100)}%`);
  });

  it("says plainly that nothing was measured rather than inventing a number", () => {
    // The camera-refused and tracker-failed paths never run a validation, so
    // there is no error to show. A "0 px" or a "NaN%" here would be a lie.
    const sentence = calibrationReportText(
      { mode: "cursor_only", calibration_error_px: null },
      1440,
    );

    expect(sentence).toMatch(/no accuracy figure was measured/i);
    expect(sentence).toMatch(/mouse/i);
    expect(sentence).not.toMatch(/NaN|Infinity|null|undefined/);
  });

  it("reports the pixels without a percentage when the screen width is unreadable", () => {
    // modeFromValidation drops exactly this case to cursor_only with the error
    // still attached, so the screen has a real measurement and nothing to
    // divide it by. An offscreen window reports a screen width of 0.
    const sentence = calibrationReportText(
      { mode: "cursor_only", calibration_error_px: 84 },
      0,
    );

    expect(sentence).toContain("84 pixels");
    expect(sentence).not.toMatch(/NaN|Infinity/);
    expect(sentence).toMatch(/screen width/i);
    expect(sentence).toMatch(/mouse/i);
  });

  it("renders that sentence into the panel", () => {
    const view = mount(
      <CalibrationReport
        outcome={{ mode: "webcam", calibration_error_px: 84 }}
        screenWidthPx={1440}
      />,
    );

    expect(text(view.container, "done-accuracy")).toContain("84 pixels");

    view.unmount();
  });
});

describe("the verdict screen, driven through the whole flow", () => {
  beforeEach(() => {
    installCaptureEnvironment();
  });

  afterEach(() => {
    restoreCaptureEnvironment();
  });

  it("shows the number behind an accepted calibration", async () => {
    const run = await runCaptureFlow(onTarget);

    const shown = text(run.view.container, "done-accuracy");
    click(run.view.container, "done-start");
    const measured = run.result().calibration_error_px;

    expect(run.result().mode).toBe("webcam");
    expect(measured).not.toBeNull();
    expect(shown).toContain(`${Math.round(measured as number)} pixels`);
    expect(shown).toContain("12%");

    run.result().tracker?.stop();
    run.view.unmount();
  });

  it("shows the number behind a rejected calibration, not just the rejection", async () => {
    const run = await runCaptureFlow(wayOff);

    const shown = text(run.view.container, "done-accuracy");
    click(run.view.container, "done-start");
    const measured = run.result().calibration_error_px;

    expect(run.result().mode).toBe("cursor_only");
    expect(measured).not.toBeNull();
    expect(shown).toContain(`${Math.round(measured as number)} pixels`);
    expect(shown).toContain("12%");
    // The old screen said only "not accurate enough to trust". A verdict with
    // no number in it is what this whole task is about.
    expect(shown).toMatch(/\d/);

    run.view.unmount();
  });

  it("does not pretend to have measured anything when the camera was refused", async () => {
    installCamera(() =>
      Promise.reject(new DOMException("Permission denied", "NotAllowedError")),
    );
    const view = mount(<CaptureFlow onComplete={vi.fn()} />);

    click(view.container, "consent-agree");
    click(view.container, "intake-has_list-yes");
    click(view.container, "intake-same_brand-yes");
    click(view.container, "intake-hurry-yes");
    click(view.container, "intake-continue");
    click(view.container, "camera-start");
    await settle();
    click(view.container, "camera-cursor-only");

    const shown = text(view.container, "done-accuracy");
    expect(shown).toMatch(/no accuracy figure was measured/i);
    expect(shown).not.toMatch(/NaN|Infinity/);

    view.unmount();
  });
});
