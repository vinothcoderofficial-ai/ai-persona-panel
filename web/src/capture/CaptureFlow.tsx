import { useCallback, useEffect, useRef, useState } from "react";
import type { Session } from "@/contracts/session.schema";
import { archetypeFromIntake, type ArchetypeLabel, type Intake } from "@/capture/archetype";
import type { ValidationOutcome } from "@/capture/calibrationMath";
import { Calibration } from "@/capture/Calibration";
import { CalibrationReport } from "@/capture/CalibrationReport";
import { CameraCheck } from "@/capture/CameraCheck";
import { Consent, ConsentDeclined } from "@/capture/Consent";
import { GazeTracker } from "@/capture/GazeTracker";
import { IntakeSurvey } from "@/capture/IntakeSurvey";
import { CHECK_SECONDS, TrackerCheck } from "@/capture/TrackerCheck";
import { Validation } from "@/capture/Validation";
import * as style from "@/capture/styles";

/** The session fields this flow is responsible for producing (SPEC 4.3). */
export interface CaptureResult {
  consent: true;
  intake: Intake;
  archetype_label: ArchetypeLabel;
  mode: Session["mode"];
  calibration_error_px: number | null;
  /**
   * The calibrated tracker, still running, handed to the shopping session.
   *
   * Present only for `mode: "webcam"`, and absent - not null - otherwise, so a
   * cursor-only result is exactly the object it always was. **Whoever receives
   * it owns the camera** and must call `stop()` when shopping ends or the
   * component unmounts; this flow will not touch it again.
   *
   * It is handed over rather than restarted because `GazeTracker.stop()` ends
   * WebGazer, and WebGazer's model lives in memory: restarting it in the store
   * would throw away the calibration whose error was just measured and written
   * into the session.
   */
  tracker?: GazeTracker;
}

export interface CaptureFlowProps {
  onComplete: (result: CaptureResult) => void;
  /**
   * Injectable so the webcam path can be driven in jsdom, which has neither
   * WebGL nor a camera. Production builds the real tracker.
   */
  createTracker?: () => GazeTracker;
}

type Step =
  | "consent"
  | "declined"
  | "intake"
  | "camera"
  | "calibrate"
  | "validate"
  // Optional, entered from "done" and always returning to it: the shopper
  // asking to watch the tracker work before any session exists. See
  // TrackerCheck.tsx for why this is the one screen allowed to draw a dot.
  | "check"
  | "done";

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** Same fallback as main.tsx: an offscreen window reports a screen width of 0. */
function screenWidth(): number {
  return Math.round(window.screen.width || window.innerWidth);
}

/**
 * Consent -> intake -> camera check -> 9-point calibration -> 4-point
 * validation -> done, and then the store.
 *
 * Two rules shape the whole machine:
 *   - consent is explicit and refusable, and nothing - no camera, no session -
 *     happens before it is given;
 *   - every failure downstream of consent lands in `cursor_only` rather than
 *     turning the person away. A blocked camera, a tracker that will not start
 *     and a calibration error over 12% of the screen width all end the same
 *     way: the session runs, and it says so in `mode`.
 *
 * The camera is released here in every case but one: a `webcam` session hands
 * the running tracker to the store through `CaptureResult.tracker`, because
 * measuring a calibration and then closing the camera would give an honest
 * `calibration_error_px` and not a single gaze event to use it on. Ownership
 * moves exactly once, in `start()`, and nowhere else.
 */
export function CaptureFlow({ onComplete, createTracker }: CaptureFlowProps): JSX.Element {
  const [step, setStep] = useState<Step>("consent");
  const [intake, setIntake] = useState<Intake | null>(null);
  const [outcome, setOutcome] = useState<ValidationOutcome | null>(null);
  const [fallbackReason, setFallbackReason] = useState<string | null>(null);
  const [trackerReady, setTrackerReady] = useState(false);
  const tracker = useRef<GazeTracker | null>(null);

  /** Release the camera. The flow ending and the flow unmounting both land here. */
  const stopTracker = useCallback(() => {
    tracker.current?.stop();
    tracker.current = null;
    setTrackerReady(false);
  }, []);

  useEffect(() => stopTracker, [stopTracker]);

  const finishCursorOnly = useCallback(
    (reason: string) => {
      stopTracker();
      setFallbackReason(reason);
      setOutcome({ mode: "cursor_only", calibration_error_px: null });
      setStep("done");
    },
    [stopTracker],
  );

  // The camera is opened here and nowhere earlier: only a shopper who has
  // consented and passed the camera check ever reaches this step.
  useEffect(() => {
    if (step !== "calibrate" || tracker.current !== null) return undefined;

    let cancelled = false;
    const started = createTracker === undefined ? new GazeTracker() : createTracker();
    tracker.current = started;

    void started
      .start()
      .then(() => {
        if (cancelled) {
          started.stop();
          return;
        }
        setTrackerReady(true);
      })
      .catch((error: unknown) => {
        started.stop();
        if (cancelled) return;
        tracker.current = null;
        finishCursorOnly(`The eye tracker could not start. ${messageOf(error)}`);
      });

    return () => {
      cancelled = true;
    };
  }, [step, finishCursorOnly, createTracker]);

  // Only reachable if the tracker died between calibration and validation.
  useEffect(() => {
    if (step === "validate" && tracker.current === null) {
      finishCursorOnly("The eye tracker stopped before the check could run.");
    }
  }, [step, finishCursorOnly]);

  const onValidated = useCallback(
    (validated: ValidationOutcome) => {
      // A cursor_only verdict has nothing left to measure, so the camera goes
      // back now. A webcam verdict keeps it: the tracker is handed to the store
      // in start(), and if the shopper walks away instead, the unmount cleanup
      // above still releases it.
      if (validated.mode !== "webcam") stopTracker();
      setOutcome(validated);
      // No reason line here, in either direction. A validation that ran has a
      // number behind its verdict, and CalibrationReport states that number,
      // the threshold it was compared with and what follows - which is strictly
      // more than the sentence that used to sit here ("not accurate enough to
      // trust") told anybody. `fallbackReason` is left for the failures that
      // produced no measurement at all and therefore need explaining in words:
      // a refused camera, a tracker that would not start.
      setFallbackReason(null);
      setStep("done");
    },
    [stopTracker],
  );

  function start(): void {
    if (intake === null || outcome === null) return;

    const handover = outcome.mode === "webcam" ? tracker.current : null;
    if (handover === null) {
      stopTracker();
    } else {
      // Detached before onComplete, so this component's unmount cleanup - which
      // releases the camera - cannot stop the tracker it has just given away.
      tracker.current = null;
      setTrackerReady(false);
    }

    onComplete({
      consent: true,
      intake,
      archetype_label: archetypeFromIntake(intake),
      mode: outcome.mode,
      calibration_error_px: outcome.calibration_error_px,
      ...(handover === null ? {} : { tracker: handover }),
    });
  }

  /**
   * The verdict screen, and the last thing between consent and the store.
   *
   * It is a function rather than a `case` body because the optional tracker
   * check falls back to it: if the tracker went away while that screen was up,
   * there is nothing left to demonstrate and the shopper should simply be back
   * here, not looking at an empty screen.
   */
  function doneScreen(): JSX.Element {
    // Offered only while this flow still owns a running camera - so: a webcam
    // verdict, before the handover in start(). A cursor_only session gave the
    // camera back at validation and has nothing to show.
    const canCheckTracker =
      outcome?.mode === "webcam" && trackerReady && tracker.current !== null;

    return (
      <div style={style.screen}>
        <div style={style.panel}>
          <h1 style={style.heading}>You are set</h1>
          {/* The measurement first, then what it means for the next ten
              minutes. The other way round leaves the shopper reading a rule
              about a dot before they have been told whether the thing that
              draws it is even switched on. */}
          {outcome !== null && (
            <CalibrationReport outcome={outcome} screenWidthPx={screenWidth()} />
          )}
          <p style={style.paragraph} data-testid="done-mode">
            {outcome?.mode === "webcam"
              ? "You will not see a dot while you shop - that is deliberate, watching it would change where you look."
              : "Nothing else about the study changes - shop exactly as you would have."}
          </p>
          {fallbackReason !== null && (
            <p style={style.note} data-testid="done-reason">
              {fallbackReason}
            </p>
          )}
          <p style={style.paragraph}>
            Shop the shelf the way you normally would. Pick things up, look
            around, and check out when you are done.
          </p>
          <div style={style.buttonRow}>
            <button
              type="button"
              data-testid="done-start"
              style={style.primaryButton}
              onClick={start}
            >
              Start shopping
            </button>
            {canCheckTracker && (
              <button
                type="button"
                data-testid="done-check"
                style={style.secondaryButton}
                onClick={() => setStep("check")}
              >
                Show me the tracker working first
              </button>
            )}
          </div>
          {canCheckTracker && (
            <p style={style.note}>
              That last one takes {CHECK_SECONDS} seconds, records nothing, and
              is the only place you will ever see the dot.
            </p>
          )}
        </div>
      </div>
    );
  }

  switch (step) {
    case "consent":
      return (
        <Consent
          onAgree={() => setStep("intake")}
          onDecline={() => setStep("declined")}
        />
      );

    case "declined":
      return <ConsentDeclined />;

    case "intake":
      return (
        <IntakeSurvey
          onSubmit={(answers) => {
            setIntake(answers);
            setStep("camera");
          }}
        />
      );

    case "camera":
      return (
        <CameraCheck
          onCameraReady={() => setStep("calibrate")}
          onCursorOnly={finishCursorOnly}
        />
      );

    case "calibrate": {
      const active = tracker.current;
      if (active === null || !trackerReady) {
        return (
          <div style={style.screen}>
            <div style={style.panel} data-testid="calibrate-waiting">
              <h1 style={style.heading}>Starting the eye tracker</h1>
              <p style={style.paragraph}>
                This takes a few seconds the first time - the model has to load.
              </p>
            </div>
          </div>
        );
      }
      return <Calibration tracker={active} onDone={() => setStep("validate")} />;
    }

    case "validate": {
      const active = tracker.current;
      // The effect above is already moving this session to cursor_only.
      if (active === null) return <div style={style.screen} />;
      return (
        <Validation tracker={active} screenWidthPx={screenWidth()} onDone={onValidated} />
      );
    }

    case "check": {
      const active = tracker.current;
      // Only reachable from the button above, which only exists while a tracker
      // is running - but a tracker can die at any time, and an empty demo is
      // worse than none.
      if (active === null || !trackerReady) return doneScreen();
      return <TrackerCheck tracker={active} onDone={() => setStep("done")} />;
    }

    case "done":
      return doneScreen();
  }
}
