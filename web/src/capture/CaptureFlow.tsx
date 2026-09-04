import { useCallback, useEffect, useRef, useState } from "react";
import type { Session } from "@/contracts/session.schema";
import { archetypeFromIntake, type ArchetypeLabel, type Intake } from "@/capture/archetype";
import type { ValidationOutcome } from "@/capture/calibrationMath";
import { Calibration } from "@/capture/Calibration";
import { CameraCheck } from "@/capture/CameraCheck";
import { Consent, ConsentDeclined } from "@/capture/Consent";
import { GazeTracker } from "@/capture/GazeTracker";
import { IntakeSurvey } from "@/capture/IntakeSurvey";
import { Validation } from "@/capture/Validation";
import * as style from "@/capture/styles";

/** The session fields this flow is responsible for producing (SPEC 4.3). */
export interface CaptureResult {
  consent: true;
  intake: Intake;
  archetype_label: ArchetypeLabel;
  mode: Session["mode"];
  calibration_error_px: number | null;
}

export interface CaptureFlowProps {
  onComplete: (result: CaptureResult) => void;
}

type Step =
  | "consent"
  | "declined"
  | "intake"
  | "camera"
  | "calibrate"
  | "validate"
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
 */
export function CaptureFlow({ onComplete }: CaptureFlowProps): JSX.Element {
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
    const started = new GazeTracker();
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
  }, [step, finishCursorOnly]);

  // Only reachable if the tracker died between calibration and validation.
  useEffect(() => {
    if (step === "validate" && tracker.current === null) {
      finishCursorOnly("The eye tracker stopped before the check could run.");
    }
  }, [step, finishCursorOnly]);

  const onValidated = useCallback(
    (validated: ValidationOutcome) => {
      // Whatever the verdict, the measurement is over and the camera goes.
      stopTracker();
      setOutcome(validated);
      setFallbackReason(
        validated.mode === "cursor_only"
          ? "The calibration was not accurate enough to trust, so this session follows the mouse."
          : null,
      );
      setStep("done");
    },
    [stopTracker],
  );

  function start(): void {
    if (intake === null || outcome === null) return;
    stopTracker();
    onComplete({
      consent: true,
      intake,
      archetype_label: archetypeFromIntake(intake),
      mode: outcome.mode,
      calibration_error_px: outcome.calibration_error_px,
    });
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

    case "done":
      return (
        <div style={style.screen}>
          <div style={style.panel}>
            <h1 style={style.heading}>You are set</h1>
            <p style={style.paragraph} data-testid="done-mode">
              {outcome?.mode === "webcam"
                ? "Eye tracking is on. You will not see a dot - that is deliberate, watching it would change where you look."
                : "This session follows your mouse instead of your eyes."}
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
            </div>
          </div>
        </div>
      );
  }
}
