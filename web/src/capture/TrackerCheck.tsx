import { useEffect, useRef, useState } from "react";
import type { CSSProperties } from "react";
import type { GazeSample, GazeTracker } from "@/capture/GazeTracker";
import * as style from "@/capture/styles";

/**
 * The one screen in this app that is allowed to draw a live gaze dot.
 *
 * CLAUDE.md and SPEC risk row 10 are blunt: "The shopper's own screen must not
 * show their gaze dot. People stare at the dot and corrupt the data." That rule
 * is about **measurement**. It bites from the moment a session exists, because
 * from then on every fixation is being written down, and a person watching a
 * dot is producing a recording of themselves watching a dot.
 *
 * This screen runs before any of that. It is reachable only from the capture
 * flow's "you are set" step, which is upstream of `POST /sessions`: there is no
 * session id yet, no prediction lock, no EventLogger, no socket. The samples
 * are read straight off the tracker, counted, drawn and thrown away - nothing
 * is stored, nothing is sent, and there is no recording for staring to spoil.
 *
 * That is also why the whole thing lives in `capture/` and is imported by
 * `CaptureFlow` and by nothing else. **Do not move it, or anything it does,
 * into `store/`.** The identical code one screen later would quietly corrupt
 * every session it ran in, which is a far worse bug than it looks, because the
 * data would still arrive and still look plausible.
 * `web/tests/trackerCheck.test.tsx` guards the boundary.
 *
 * Why it exists at all: without it a shopper is asked to trust a tracker they
 * cannot see, told a verdict about their own eyes, and given no way to tell a
 * working tracker from a broken one. Eight seconds before the store fixes that
 * and costs the study nothing.
 *
 * Clicking in here cannot move the calibration either: `GazeTracker.start()`
 * calls `removeMouseEventListeners()`, so WebGazer no longer retrains itself on
 * clicks. Training happens on the calibration screen and nowhere else.
 */

/** How long the check runs before it closes itself. Short on purpose. */
export const CHECK_SECONDS = 8;

/** The rate and confidence are averaged over the last second of samples. */
const WINDOW_MS = 1000;

export interface TrackerCheckProps {
  tracker: GazeTracker;
  /** Back to "you are set" - on the button, and on the countdown running out. */
  onDone: () => void;
}

interface Readout {
  x: number;
  y: number;
  /** Samples seen in the last second: the tracker's real sampling rate. */
  perSecond: number;
  /** Mean confidence over the same window, 0..1. */
  conf: number;
}

function dotStyle(readout: Readout): CSSProperties {
  return {
    position: "fixed",
    left: readout.x,
    top: readout.y,
    transform: "translate(-50%, -50%)",
    width: 22,
    height: 22,
    borderRadius: "50%",
    background: "rgba(79, 140, 255, 0.55)",
    border: "2px solid #ffffff",
    // The dot is a demonstration, not a target: it must never eat a click.
    pointerEvents: "none",
  };
}

export function TrackerCheck({ tracker, onDone }: TrackerCheckProps): JSX.Element {
  const [readout, setReadout] = useState<Readout | null>(null);
  const [secondsLeft, setSecondsLeft] = useState(CHECK_SECONDS);
  const finish = useRef(onDone);

  // Kept fresh so the countdown effect below can close the screen without
  // holding a stale callback, and without restarting the clock on every render.
  useEffect(() => {
    finish.current = onDone;
  });

  useEffect(() => {
    // Read-only. `subscribe` hands over the same `{x, y, conf, t}` the store
    // gets, and everything derived from it dies with this component.
    const recent: GazeSample[] = [];
    return tracker.subscribe((sample) => {
      recent.push(sample);
      while (recent.length > 1 && sample.t - recent[0].t > WINDOW_MS) recent.shift();
      const total = recent.reduce((sum, seen) => sum + seen.conf, 0);
      setReadout({
        x: sample.x,
        y: sample.y,
        perSecond: recent.length,
        conf: total / recent.length,
      });
    });
  }, [tracker]);

  // Bounded by construction: the check ends whether or not anyone dismisses it.
  useEffect(() => {
    const id = window.setInterval(() => {
      setSecondsLeft((left) => Math.max(0, left - 1));
    }, 1000);
    return () => window.clearInterval(id);
  }, []);

  useEffect(() => {
    if (secondsLeft > 0) return;
    finish.current();
  }, [secondsLeft]);

  return (
    <div style={{ ...style.screen, display: "block" }}>
      {readout !== null && <div data-testid="tracker-check-dot" style={dotStyle(readout)} />}

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
        <h1 style={style.heading}>Is it following you?</h1>
        <p style={style.paragraph}>
          Look slowly around the screen. The dot is the tracker's guess at where
          you are looking - if it drifts along with your eyes, it is working.
        </p>
        <p style={style.note} data-testid="tracker-check-readout">
          {readout === null
            ? "Waiting for the first reading..."
            : `${readout.perSecond} readings in the last second - average confidence ${Math.round(readout.conf * 100)}%`}
        </p>
        <p style={style.note}>
          Nothing on this screen is recorded, and no session has started yet.
          This closes on its own in {secondsLeft}s. Once you start shopping the
          dot is gone for good - watching it would change where you look, and
          that is the one thing we are trying to measure.
        </p>
        <div style={{ ...style.buttonRow, justifyContent: "center" }}>
          <button
            type="button"
            data-testid="tracker-check-done"
            style={style.secondaryButton}
            onClick={() => finish.current()}
          >
            That is enough - take me back
          </button>
        </div>
      </div>
    </div>
  );
}
