import { useEffect, useRef, useState } from "react";
import {
  VALIDATION_POINTS,
  meanValidationError,
  modeFromValidation,
  type Point,
  type ValidationOutcome,
} from "@/capture/calibrationMath";
import type { GazeTracker } from "@/capture/GazeTracker";
import * as style from "@/capture/styles";

/** Time for the eyes to land on a new dot before anything is counted. */
export const SETTLE_MS = 600;
/** Time spent averaging predictions for one validation point. */
export const COLLECT_MS = 1200;

export interface ValidationProps {
  tracker: GazeTracker;
  /**
   * The threshold is 12% of the **screen** width (SPEC M2), which is the same
   * number the session records as `screen_w`.
   */
  screenWidthPx: number;
  onDone: (outcome: ValidationOutcome) => void;
}

/**
 * The mean of what was collected for one target. An empty collection gives NaN
 * on purpose: a target the tracker never saw must not quietly average away, it
 * has to poison the mean and drop the session to cursor_only.
 */
function centroid(points: Point[]): Point {
  let x = 0;
  let y = 0;
  for (const point of points) {
    x += point.x;
    y += point.y;
  }
  return { x: x / points.length, y: y / points.length };
}

/**
 * The 4-point validation. These points are deliberately not calibration points:
 * measuring the error where the model was trained would measure nothing.
 */
export function Validation({ tracker, screenWidthPx, onDone }: ValidationProps): JSX.Element {
  const [index, setIndex] = useState(0);
  const [collecting, setCollecting] = useState(false);
  const [predicted, setPredicted] = useState<Point[]>([]);
  const reported = useRef(false);

  useEffect(() => {
    if (index >= VALIDATION_POINTS.length) return undefined;

    const collected: Point[] = [];
    let open = false;
    const unsubscribe = tracker.subscribe((sample) => {
      if (open) collected.push({ x: sample.x, y: sample.y });
    });

    const settle = window.setTimeout(() => {
      open = true;
      setCollecting(true);
    }, SETTLE_MS);

    const finish = window.setTimeout(() => {
      open = false;
      setCollecting(false);
      setPredicted((previous) => [...previous, centroid(collected)]);
      setIndex((previous) => previous + 1);
    }, SETTLE_MS + COLLECT_MS);

    return () => {
      window.clearTimeout(settle);
      window.clearTimeout(finish);
      unsubscribe();
    };
  }, [index, tracker]);

  useEffect(() => {
    if (predicted.length < VALIDATION_POINTS.length || reported.current) return;
    reported.current = true;

    // Gaze predictions are in viewport pixels, so the targets are measured
    // there too; only the 12% threshold is a fraction of the screen width.
    const targets = VALIDATION_POINTS.map((point) => ({
      x: point.x * window.innerWidth,
      y: point.y * window.innerHeight,
    }));
    onDone(modeFromValidation(meanValidationError(predicted, targets), screenWidthPx));
  }, [predicted, screenWidthPx, onDone]);

  const point = VALIDATION_POINTS[Math.min(index, VALIDATION_POINTS.length - 1)];

  return (
    <div style={{ ...style.screen, display: "block" }}>
      <div
        style={{
          position: "absolute",
          left: "50%",
          top: 28,
          transform: "translateX(-50%)",
          textAlign: "center",
          maxWidth: 520,
        }}
      >
        <h1 style={style.heading}>Checking the calibration</h1>
        <p style={style.paragraph}>
          Just look at the blue dot - no clicking this time.
        </p>
        <p style={style.note} data-testid="validation-progress">
          Point {Math.min(index + 1, VALIDATION_POINTS.length)} of{" "}
          {VALIDATION_POINTS.length}
          {collecting ? " - hold still" : ""}
        </p>
      </div>

      <div
        data-testid="validation-point"
        style={{
          position: "absolute",
          left: `${point.x * 100}%`,
          top: `${point.y * 100}%`,
          transform: "translate(-50%, -50%)",
          width: 26,
          height: 26,
          borderRadius: "50%",
          background: collecting ? "#4f8cff" : "#33507f",
          border: "2px solid #ffffff",
        }}
      />
    </div>
  );
}
