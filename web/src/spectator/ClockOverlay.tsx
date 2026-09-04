import { useEffect, useState } from "react";
import type { CSSProperties } from "react";
import { INK, PANEL_BORDER, mono } from "@/spectator/styles";

/**
 * The wall clock, SPEC 6 ("a visible clock runs throughout" the live segments).
 *
 * It exists for one sentence in the demo video: *"prediction locked at
 * 10:32:07, shopping began 10:32:41."* Both halves of that claim have to be
 * legible in the same unedited take, so this is deliberately oversized -
 * `PredictionBadge` shows the first time, this shows the second, and a viewer
 * can read them off the recording without being asked to trust a caption.
 */

/** Local wall-clock time as `HH:MM:SS`. Shared with `PredictionBadge`. */
export function wallClockTime(date: Date): string {
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

export interface ClockOverlayProps {
  /** Injected in tests; the real clock otherwise. */
  now?: () => Date;
  /** How often the display is refreshed. `null` freezes it (tests). */
  intervalMs?: number | null;
}

export function ClockOverlay({ now, intervalMs = 250 }: ClockOverlayProps) {
  const readClock = now ?? (() => new Date());
  const [time, setTime] = useState(() => wallClockTime(readClock()));

  useEffect(() => {
    if (intervalMs === null) return undefined;
    // Sub-second so the seconds digit never visibly lags on the recording.
    // Storing the formatted string means an unchanged second is a no-op
    // re-render rather than a new Date object every tick.
    const timer = setInterval(() => setTime(wallClockTime(readClock())), intervalMs);
    return () => clearInterval(timer);
    // `readClock` is stable in production and fixed in tests; re-subscribing on
    // every render would restart the timer forever.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs]);

  return (
    <div style={boxStyle}>
      <div style={labelStyle}>Wall clock</div>
      <div data-testid="wall-clock" style={timeStyle}>
        {time}
      </div>
    </div>
  );
}

const boxStyle: CSSProperties = {
  padding: "8px 16px",
  border: `1px solid ${PANEL_BORDER}`,
  borderRadius: 10,
  textAlign: "right",
};

const labelStyle: CSSProperties = {
  fontSize: 11,
  letterSpacing: "0.09em",
  textTransform: "uppercase",
  opacity: 0.6,
};

const timeStyle: CSSProperties = {
  ...mono,
  fontSize: 40,
  fontWeight: 700,
  lineHeight: 1.05,
  color: INK,
  fontVariantNumeric: "tabular-nums",
};
