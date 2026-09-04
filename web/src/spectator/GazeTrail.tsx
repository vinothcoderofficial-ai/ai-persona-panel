import type { CSSProperties } from "react";
import { TRAIL_WINDOW_MS, visibleTrail, type GazePoint } from "@/spectator/trail";
import { PANEL_BORDER, REAL, note } from "@/spectator/styles";

/**
 * The shopper's gaze, on the spectator's screen and nowhere else.
 *
 * CLAUDE.md: *"The shopper's own screen must not show their gaze dot. People
 * stare at the dot and corrupt the data. The dot belongs on the spectator view
 * only."* Nothing in `web/src/store` or `web/src/capture` may import this file,
 * and `web/tests/spectatorIsolation.test.ts` walks the import graph to prove it.
 *
 * The component holds no clock: `now` comes in as a prop and the ageing itself
 * is `trail.ts:visibleTrail`, so what is drawn at any instant is a hand-checkable
 * function of the inputs.
 */

export interface GazeTrailProps {
  /** Positions in the shopper's screen coordinates, oldest first. */
  points: readonly GazePoint[];
  /** The spectator's clock, in the same units as `points[].t`. */
  now: number;
  /** The shopper's screen size, so gaze coordinates map onto this frame. */
  screen: { w: number; h: number };
  /**
   * A still of the station the shopper is at, drawn under the trail. Supplied
   * with `?screenshot=<url>`; without one the frame is drawn empty rather than
   * showing a picture of some other station.
   */
  screenshotUrl?: string | null;
}

function percent(value: number, extent: number): string {
  if (!(extent > 0)) return "50%";
  const clamped = Math.min(100, Math.max(0, (value / extent) * 100));
  return `${Number(clamped.toFixed(3))}%`;
}

export function GazeTrail({ points, now, screen, screenshotUrl }: GazeTrailProps) {
  const visible = visibleTrail(points, now);
  const head = visible.length > 0 ? visible[visible.length - 1] : null;
  const tail = visible.slice(0, -1);

  return (
    <div data-testid="gaze-trail" style={frameStyle(screen)}>
      {screenshotUrl != null && screenshotUrl.length > 0 && (
        <img src={screenshotUrl} alt="" style={screenshotStyle} />
      )}

      {tail.map((point, index) => (
        <span
          // Points are a fixed-length window that shifts; the index is the
          // position in the trail, which is exactly what identifies it here.
          key={`${index}-${point.ageMs}`}
          data-testid="gaze-trail-point"
          style={{
            ...trailPointStyle,
            left: percent(point.x, screen.w),
            top: percent(point.y, screen.h),
            opacity: point.opacity,
          }}
        />
      ))}

      {head !== null && (
        <span
          data-testid="gaze-dot"
          data-x={String(head.x)}
          data-y={String(head.y)}
          style={{
            ...dotStyle,
            left: percent(head.x, screen.w),
            top: percent(head.y, screen.h),
          }}
        />
      )}

      {head === null && (
        <div data-testid="gaze-trail-empty" style={emptyStyle}>
          No live gaze in the last {TRAIL_WINDOW_MS / 1000} s
        </div>
      )}
    </div>
  );
}

function frameStyle(screen: { w: number; h: number }): CSSProperties {
  return {
    position: "relative",
    width: "100%",
    // The frame is the shopper's screen, so gaze coordinates land where the
    // shopper was actually looking rather than on a differently-shaped box.
    aspectRatio: `${screen.w} / ${screen.h}`,
    border: `1px solid ${PANEL_BORDER}`,
    borderRadius: 8,
    overflow: "hidden",
    background:
      "repeating-linear-gradient(0deg,#171b22 0 24px,#1b2029 24px 25px)," +
      "repeating-linear-gradient(90deg,#171b22 0 24px,#1b2029 24px 25px)",
  };
}

const screenshotStyle: CSSProperties = {
  position: "absolute",
  inset: 0,
  width: "100%",
  height: "100%",
  objectFit: "cover",
};

const trailPointStyle: CSSProperties = {
  position: "absolute",
  width: 12,
  height: 12,
  marginLeft: -6,
  marginTop: -6,
  borderRadius: "50%",
  background: REAL,
  pointerEvents: "none",
};

const dotStyle: CSSProperties = {
  position: "absolute",
  width: 26,
  height: 26,
  marginLeft: -13,
  marginTop: -13,
  borderRadius: "50%",
  background: "rgba(79,140,255,0.35)",
  border: `3px solid ${REAL}`,
  boxShadow: "0 0 18px rgba(79,140,255,0.75)",
  pointerEvents: "none",
};

const emptyStyle: CSSProperties = {
  ...note,
  position: "absolute",
  inset: 0,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
};
