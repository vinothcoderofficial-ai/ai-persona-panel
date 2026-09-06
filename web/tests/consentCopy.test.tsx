import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Consent } from "@/capture/Consent";
import {
  click,
  installCaptureEnvironment,
  mount,
  onTarget,
  restoreCaptureEnvironment,
  runCaptureFlow,
} from "./captureRunner";

/**
 * W2 (C): the consent form has to describe what the code actually does.
 *
 * It used to promise "The camera is released as soon as the setup is finished."
 * That was false, and had been since S11 decision 6. A `webcam` session hands
 * the *running* tracker from CaptureFlow to the store through
 * `CaptureResult.tracker` precisely so that the camera survives the end of
 * setup - otherwise the session would record an honest calibration error and
 * not one gaze sample. The camera is released at checkout, or when the store
 * unmounts, and only a `cursor_only` session gets it back at the end of setup.
 *
 * A participant information sheet that understates when a camera stops
 * recording is not a copy bug, so this file pins the claim to the behaviour: it
 * asserts the false sentence is gone, and it asserts - by running the flow -
 * that the camera really is still open when setup finishes.
 */

const SRC = resolve(dirname(fileURLToPath(import.meta.url)), "..", "src");

function consentText(): string {
  const view = mount(<Consent onAgree={vi.fn()} onDecline={vi.fn()} />);
  const shown = view.container.textContent ?? "";
  view.unmount();
  return shown;
}

describe("the consent form's camera promise", () => {
  it("no longer claims the camera is released when the setup ends", () => {
    const shown = consentText();

    expect(shown).not.toMatch(/released as soon as the setup is finished/i);
    expect(shown).not.toMatch(/as soon as the setup/i);
    expect(shown).not.toMatch(/released .{0,40}\bsetup (is|has) (finished|ended|done)/i);
  });

  it("says the camera stays on while the person shops, and when it stops", () => {
    const shown = consentText();

    expect(shown).toMatch(/camera/i);
    expect(shown).toMatch(/(stays|keeps running|stay on|remains).{0,60}shop/i);
    expect(shown).toMatch(/check ?out/i);
  });

  it("still promises the things that are true, and asks before the camera opens", () => {
    const shown = consentText();

    expect(shown).toMatch(/in your browser/i);
    expect(shown).toMatch(/no video|no frame/i);
    expect(shown).toMatch(/only requested after you agree/i);
  });
});

describe("the behaviour that promise now describes", () => {
  beforeEach(() => {
    installCaptureEnvironment();
  });

  afterEach(() => {
    restoreCaptureEnvironment();
  });

  it("really does leave the camera running when the setup finishes", async () => {
    const run = await runCaptureFlow(onTarget);

    click(run.view.container, "done-start");
    const handed = run.result().tracker;

    // Setup is over, the shopper is about to enter the store - and the camera
    // is still open. This is the fact the old consent bullet denied.
    expect(run.result().mode).toBe("webcam");
    expect(handed?.running).toBe(true);
    expect(run.fake.wg.end).not.toHaveBeenCalled();

    handed?.stop();
    run.view.unmount();
  });

  it("hands the release to the screen the consent form now names", () => {
    // The other half of the promise - "released the moment you check out" - is
    // implemented one directory over, in a file this task does not own, so it
    // is asserted rather than restated: if the store ever stops releasing the
    // tracker, the consent form goes back to being false and this fails.
    const scene = readFileSync(join(SRC, "store", "PlanogramScene.tsx"), "utf8");
    expect(scene).toMatch(/tracker\??\.stop\(\)/);
    expect(scene).toMatch(/const checkout/);
  });
});
