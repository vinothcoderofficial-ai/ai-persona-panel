import type { CSSProperties } from "react";
import { ALERT, GREY, LOCKED, PANEL_BORDER, REAL, mono, note, panel, panelHeading } from "@/spectator/styles";

/**
 * The live per-slot attention, **beside** the locked prediction (SPEC M9).
 *
 * Beside, never instead of: the whole claim being demonstrated is that a
 * prediction fixed before the session resembles what the person actually did,
 * and that is only visible when both columns are on screen at once.
 *
 * Two rules the rendering obeys:
 *
 *  * **A missing lock is stated, not drawn.** With no locked vector the column
 *    is replaced by a message. Rendering it as zeros would look identical to a
 *    prediction that expected nobody to look at anything.
 *  * **Each column is scaled to its own maximum.** Fused attention (which sums
 *    to 1 across slots) and a locked per-slot fixation probability are not the
 *    same quantity, and drawing them on a shared axis would invite a comparison
 *    of magnitudes that means nothing. What is being compared is the shape -
 *    which is also what the Spearman on the agreement meter measures.
 */

export interface LiveHeatmapProps {
  /** Live fused attention per slot, from the SPEC 4.7 message. */
  attention: Record<string, number>;
  /** The locked `population_fixation_prob`, or null when none was supplied. */
  locked: Record<string, number> | null;
  /** True while the feed is disconnected: what is drawn is no longer current. */
  stale?: boolean;
}

function maximum(values: Record<string, number>): number {
  let max = 0;
  for (const value of Object.values(values)) {
    if (value > max) max = value;
  }
  return max;
}

function barWidth(value: number, max: number): string {
  if (!(max > 0)) return "0%";
  return `${Number(Math.min(100, (value / max) * 100).toFixed(2))}%`;
}

export function LiveHeatmap({ attention, locked, stale = false }: LiveHeatmapProps) {
  const slotIds = [...new Set([...Object.keys(attention), ...Object.keys(locked ?? {})])]
    .sort();
  const realMax = maximum(attention);
  const lockedMax = locked === null ? 0 : maximum(locked);

  return (
    <div data-testid="live-heatmap" style={{ ...panel, opacity: stale ? 0.45 : 1 }}>
      <div style={headerRowStyle}>
        <div style={slotColumnStyle}>Slot</div>
        <div style={{ ...panelHeading, margin: 0, color: REAL, flex: 1 }}>
          Real attention — live
        </div>
        <div style={{ ...panelHeading, margin: 0, color: LOCKED, flex: 1 }}>
          Locked prediction
        </div>
      </div>

      {locked === null && (
        <div data-testid="locked-unavailable" style={unavailableStyle}>
          No locked prediction is available on this screen, so the right-hand column is
          empty rather than zero. Pass{" "}
          <code style={mono}>?lock=&lt;url of predictions/&#123;id&#125;.json&gt;</code> to
          show it beside the live column.
        </div>
      )}

      <div style={{ maxHeight: "48vh", overflowY: "auto" }}>
        {slotIds.length === 0 && (
          <div style={note}>No attention has been reported for this session yet.</div>
        )}
        {slotIds.map((slotId) => {
          const real = attention[slotId];
          const predicted = locked === null ? undefined : locked[slotId];
          return (
            <div
              key={slotId}
              data-testid={`heat-row-${slotId}`}
              data-slot-id={slotId}
              style={rowStyle}
            >
              <div style={{ ...slotColumnStyle, ...mono }}>{slotId}</div>

              <div style={cellStyle}>
                {real === undefined ? (
                  <span style={absentStyle}>not measured</span>
                ) : (
                  <div
                    data-testid={`heat-real-${slotId}`}
                    data-value={String(real)}
                    style={{ ...barRowStyle }}
                  >
                    <span
                      style={{ ...barStyle, width: barWidth(real, realMax), background: REAL }}
                    />
                    <span style={valueStyle}>{real.toFixed(3)}</span>
                  </div>
                )}
              </div>

              <div style={cellStyle}>
                {predicted === undefined ? (
                  <span style={absentStyle}>{locked === null ? "—" : "not in lock"}</span>
                ) : (
                  <div
                    data-testid={`heat-locked-${slotId}`}
                    data-value={String(predicted)}
                    style={{ ...barRowStyle }}
                  >
                    <span
                      style={{
                        ...barStyle,
                        width: barWidth(predicted, lockedMax),
                        background: LOCKED,
                      }}
                    />
                    <span style={valueStyle}>{predicted.toFixed(3)}</span>
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>

      <div style={{ ...note, marginTop: 8 }}>
        Each column is scaled to its own maximum: the two are different quantities, and
        what is being compared is their shape.
      </div>
    </div>
  );
}

const headerRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "flex-end",
  gap: 10,
  paddingBottom: 6,
  borderBottom: `1px solid ${PANEL_BORDER}`,
};

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

const cellStyle: CSSProperties = {
  flex: 1,
  minWidth: 0,
};

const barRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
};

const barStyle: CSSProperties = {
  display: "block",
  height: 12,
  minWidth: 2,
  borderRadius: 3,
};

const valueStyle: CSSProperties = {
  ...mono,
  fontSize: 12,
  opacity: 0.8,
  flex: "0 0 auto",
};

const absentStyle: CSSProperties = {
  ...note,
  color: GREY,
};

const unavailableStyle: CSSProperties = {
  margin: "10px 0",
  padding: "8px 10px",
  borderRadius: 6,
  border: `1px solid ${ALERT}`,
  color: ALERT,
  fontSize: 13,
};
