import { useState } from "react";
import type { CSSProperties, MouseEvent as ReactMouseEvent } from "react";
import { CALIBRATION_POINTS } from "@/capture/calibrationMath";
import type { GazeTracker } from "@/capture/GazeTracker";
import * as style from "@/capture/styles";

/** WebGazer's own recommendation: a handful of clicks per point, eyes on the dot. */
export const CLICKS_PER_POINT = 5;

export interface CalibrationProps {
  tracker: GazeTracker;
  onDone: () => void;
}

function dotStyle(point: { x: number; y: number }, clicks: number): CSSProperties {
  const done = clicks >= CLICKS_PER_POINT;
  return {
    position: "absolute",
    left: `${point.x * 100}%`,
    top: `${point.y * 100}%`,
    transform: "translate(-50%, -50%)",
    width: 34,
    height: 34,
    borderRadius: "50%",
    border: "2px solid #ffffff",
    background: done ? "#2f7a44" : "#c0392b",
    opacity: done ? 0.45 : 0.35 + 0.65 * (clicks / CLICKS_PER_POINT),
    color: "#ffffff",
    fontSize: 13,
    fontFamily: "inherit",
    cursor: done ? "default" : "pointer",
    padding: 0,
  };
}

/**
 * The 9-point calibration. Each click is the one piece of ground truth WebGazer
 * gets: "these eye features mean this screen position". Training happens here
 * and nowhere else - GazeTracker removes WebGazer's own click and mousemove
 * listeners at start, so clicking around the store later cannot quietly move
 * the model out from under the calibration error we are about to measure.
 */
export function Calibration({ tracker, onDone }: CalibrationProps): JSX.Element {
  const [clicks, setClicks] = useState<number[]>(() => CALIBRATION_POINTS.map(() => 0));

  const completed = clicks.filter((count) => count >= CLICKS_PER_POINT).length;

  function hit(index: number, event: ReactMouseEvent<HTMLButtonElement>): void {
    if (clicks[index] >= CLICKS_PER_POINT) return;
    tracker.record(event.clientX, event.clientY);

    const next = clicks.slice();
    next[index] += 1;
    setClicks(next);
    if (next.every((count) => count >= CLICKS_PER_POINT)) onDone();
  }

  // The dots are positioned against a fixed, full-screen box, so the fractions
  // in CALIBRATION_POINTS are fractions of the viewport.
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
        <h1 style={style.heading}>Calibration</h1>
        <p style={style.paragraph}>
          Look straight at each red dot and click it {CLICKS_PER_POINT} times.
          Keep your eyes on the dot you are clicking.
        </p>
        <p style={style.note} data-testid="calibration-progress">
          {completed} of {CALIBRATION_POINTS.length} points done
        </p>
      </div>

      {CALIBRATION_POINTS.map((point, index) => (
        <button
          key={`${point.x}-${point.y}`}
          type="button"
          data-testid={`calibration-point-${index}`}
          style={dotStyle(point, clicks[index])}
          onClick={(event) => hit(index, event)}
        >
          {Math.max(0, CLICKS_PER_POINT - clicks[index])}
        </button>
      ))}
    </div>
  );
}
