import { useLayoutEffect, useRef, useState } from "react";
import type { CSSProperties } from "react";
import {
  diffRows,
  prefersReducedMotion,
  type Attention,
  type DiffRow,
} from "@/whatif/diff";
import {
  DOWN,
  GREY,
  NEW,
  OLD,
  PANEL_BORDER,
  UP,
  mono,
  note,
  panel,
  panelHeading,
} from "@/whatif/styles";

/**
 * SPEC M9: *"`HeatmapDiff.tsx` animates from previous to new attention over
 * 600 ms."*
 *
 * The sweep is the **bar**, never the number. Every figure on this component is
 * the run's own value from the first frame: a digit counting up through 0.043
 * on its way to 0.062 is a number that was never true, and this page is filmed.
 * So `t` moves the geometry and nothing else, which is also what makes PLAN
 * section 9's *"what-if animation (keep the number)"* a one-line change - set
 * `durationMs` to 0 and every figure here is exactly as it is now.
 *
 * `prefers-reduced-motion: reduce` skips straight to the final frame.
 */

/** SPEC M9's 600 ms. */
export const ANIMATION_MS = 600;

/** Schedules one frame and returns a canceller, so tests can drive it by hand. */
export type FrameScheduler = (callback: () => void) => () => void;

const rafScheduler: FrameScheduler = (callback) => {
  const handle = requestAnimationFrame(() => callback());
  return () => cancelAnimationFrame(handle);
};

export interface HeatmapDiffProps {
  /** The attention vector that was on screen before this run. */
  previous: Attention;
  /** This run's `population_fixation_prob` - the numbers being reported. */
  next: Attention;
  durationMs?: number;
  /** Defaults to the viewer's `prefers-reduced-motion` setting. */
  reducedMotion?: boolean;
  requestFrame?: FrameScheduler;
  now?: () => number;
}

const defaultNow = () => performance.now();

function maximum(rows: DiffRow[]): number {
  let max = 0;
  for (const row of rows) {
    // Scaled to the larger of the two runs so the axis cannot change halfway
    // through a sweep, which would read as movement that is not in the data.
    max = Math.max(max, row.value, row.previous ?? 0);
  }
  return max;
}

function barWidth(value: number, max: number): string {
  if (!(max > 0)) return "0%";
  return `${Number(Math.min(100, (value / max) * 100).toFixed(2))}%`;
}

function deltaColour(delta: number | null): string {
  if (delta === null || delta === 0) return GREY;
  return delta > 0 ? UP : DOWN;
}

function formatDelta(delta: number | null): string {
  if (delta === null) return "new";
  return `${delta > 0 ? "+" : delta < 0 ? "-" : ""}${Math.abs(delta).toFixed(3)}`;
}

export function HeatmapDiff({
  previous,
  next,
  durationMs = ANIMATION_MS,
  reducedMotion,
  requestFrame = rafScheduler,
  now = defaultNow,
}: HeatmapDiffProps) {
  const [t, setT] = useState(1);

  // Held in refs so a caller passing inline arrows cannot restart the sweep on
  // every render - the same reason SpectatorView holds its socket factory.
  const requestFrameRef = useRef(requestFrame);
  requestFrameRef.current = requestFrame;
  const nowRef = useRef(now);
  nowRef.current = now;

  const still = reducedMotion ?? prefersReducedMotion();

  // A layout effect, not a plain one: the first frame must be in place before
  // the browser paints, or the sweep begins with a flash of its own endpoint.
  useLayoutEffect(() => {
    if (still || durationMs <= 0) {
      setT(1);
      return undefined;
    }

    const started = nowRef.current();
    let cancel: (() => void) | null = null;
    let stopped = false;

    const tick = () => {
      if (stopped) return;
      const fraction = (nowRef.current() - started) / durationMs;
      if (fraction >= 1) {
        setT(1);
        return;
      }
      setT(fraction);
      cancel = requestFrameRef.current(tick);
    };

    setT(0);
    cancel = requestFrameRef.current(tick);
    return () => {
      stopped = true;
      cancel?.();
    };
    // `next` is the whole point: a new result restarts the sweep. `previous` is
    // deliberately not a dependency - it changes in the same update as `next`.
  }, [next, durationMs, still]);

  const rows = diffRows(previous, next, t);
  const max = maximum(rows);

  return (
    <section style={panel} data-testid="heatmap-diff">
      <div style={panelHeading}>Population attention per slot — this run vs the last</div>

      {rows.length === 0 && (
        <div style={note} data-testid="heat-diff-empty">
          No attention has been reported yet.
        </div>
      )}

      <div style={{ maxHeight: "46vh", overflowY: "auto" }}>
        {rows.map((row) => (
          <div
            key={row.slotId}
            data-testid={`heat-diff-row-${row.slotId}`}
            data-slot-id={row.slotId}
            data-value={String(row.value)}
            data-previous={row.previous === null ? "" : String(row.previous)}
            data-frame={String(row.frame)}
            style={rowStyle}
          >
            <div style={{ ...slotColumnStyle, ...mono }}>{row.slotId}</div>

            <div style={trackStyle}>
              {row.previous !== null && (
                <span
                  style={{
                    ...ghostStyle,
                    width: barWidth(row.previous, max),
                  }}
                />
              )}
              <span
                style={{
                  ...barStyle,
                  width: barWidth(row.frame, max),
                }}
              />
            </div>

            <div
              data-testid={`heat-diff-value-${row.slotId}`}
              style={{ ...mono, ...valueStyle }}
            >
              {row.value.toFixed(3)}
            </div>
            <div
              data-testid={`heat-diff-delta-${row.slotId}`}
              style={{ ...mono, ...deltaStyle, color: deltaColour(row.delta) }}
            >
              {formatDelta(row.delta)}
            </div>
          </div>
        ))}
      </div>

      <div style={{ ...note, marginTop: 8 }}>
        <span style={{ color: OLD }}>▮</span> the previous run &nbsp;
        <span style={{ color: NEW }}>▮</span> this one. Figures are this run&apos;s own
        numbers throughout — only the bars move.
      </div>
    </section>
  );
}

const rowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 10,
  padding: "3px 0",
};

const slotColumnStyle: CSSProperties = {
  width: 86,
  flex: "0 0 86px",
  fontSize: 12,
  color: GREY,
};

const trackStyle: CSSProperties = {
  position: "relative",
  flex: 1,
  minWidth: 0,
  height: 16,
  display: "flex",
  alignItems: "center",
};

const barStyle: CSSProperties = {
  position: "absolute",
  left: 0,
  display: "block",
  height: 12,
  minWidth: 2,
  borderRadius: 3,
  background: NEW,
};

const ghostStyle: CSSProperties = {
  position: "absolute",
  left: 0,
  display: "block",
  height: 16,
  borderRight: `2px solid ${OLD}`,
  borderRadius: 3,
  background: "rgba(245, 158, 11, 0.16)",
};

const valueStyle: CSSProperties = {
  flex: "0 0 68px",
  textAlign: "right",
  fontSize: 13,
};

const deltaStyle: CSSProperties = {
  flex: "0 0 62px",
  textAlign: "right",
  fontSize: 13,
  borderLeft: `1px solid ${PANEL_BORDER}`,
  paddingLeft: 8,
};
