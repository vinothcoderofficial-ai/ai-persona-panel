import type { CSSProperties } from "react";
import {
  EVIDENCE_LABEL,
  MEANINGFUL_MIN_EVIDENCE,
  type EvidenceKind,
} from "@/spectator/liveMessage";
import { GREY, PANEL_BORDER, REAL, bigNumber, note, panel, panelHeading } from "@/spectator/styles";

/**
 * The agreement meter: Spearman between the live fused attention and the
 * **locked** prediction, as `api/app/live.py` computes it.
 *
 * **Grey until the server says `meaningful`.** SPEC 4.7: *"`meaningful` is
 * false until `n_fixations >= 15`; the meter shows 'warming up' before that."*
 * This component does not merely grey the number out - it does not render it
 * at all. Four fixations into a session the rank correlation over a handful of
 * slots swings wildly, and a spectator who reads 0.94 off a recording at second
 * three has been misled by the thing that exists to prevent exactly that. The
 * count of evidence so far is shown instead, so the meter is still visibly
 * alive.
 *
 * **What is being counted is named, not assumed.** SPEC 4.7's `n_fixations` is
 * only the right count for a webcam session; a `cursor_only` session - the only
 * kind the demo produces - has none, and counts cursor dwells instead. The
 * server sends `evidence_count` with an `evidence_kind` beside it and this
 * component prints the label it was given, so the meter never reads "n of 15
 * fixations" over a number that is not fixations.
 *
 * The threshold is never re-derived here: `meaningful` is the server's verdict
 * and this component obeys it.
 */

export interface AgreementMeterProps {
  /** Spearman rho against the locked prediction, or null if none was sent. */
  spearman: number | null;
  /** The server's verdict. Below 15 units of evidence it is false. */
  meaningful: boolean;
  /** `evidence_count` from the frame: fixations, or cursor dwells. */
  evidenceCount: number;
  /**
   * Which of the two `evidenceCount` holds. Null before the first frame has
   * arrived, when the mode is simply not known yet - the meter then says it is
   * waiting rather than guessing a label.
   */
  evidenceKind: EvidenceKind | null;
  /**
   * The panel's split-half noise ceiling, when one is known
   * (`?ceiling=` on the spectator URL). Without it, relative agreement is not
   * shown at all rather than computed against a guess.
   */
  ceiling?: number | null;
}

/**
 * `relative_agreement = min(1, rho / ceiling)` — docs/PLAN.md S17.
 *
 * Null whenever the figure would be meaningless: no rho, no ceiling, or a
 * ceiling of zero (a panel that does not agree with itself gives nothing to
 * measure against).
 */
export function relativeAgreement(
  rho: number | null,
  ceiling: number | null | undefined,
): number | null {
  if (rho === null || ceiling === null || ceiling === undefined) return null;
  if (!Number.isFinite(rho) || !Number.isFinite(ceiling) || ceiling <= 0) return null;
  return Math.min(1, rho / ceiling);
}

/** rho in [-1, 1] as a position along the track. */
function trackPosition(rho: number): string {
  const clamped = Math.min(1, Math.max(-1, rho));
  return `${Number((((clamped + 1) / 2) * 100).toFixed(2))}%`;
}

export function AgreementMeter({
  spearman,
  meaningful,
  evidenceCount,
  evidenceKind,
  ceiling,
}: AgreementMeterProps) {
  const relative = meaningful ? relativeAgreement(spearman, ceiling) : null;
  const label = evidenceKind === null ? null : EVIDENCE_LABEL[evidenceKind];

  if (!meaningful) {
    return (
      <div data-testid="agreement-meter" data-state="warming_up" style={warmingPanel}>
        <div style={panelHeading}>Agreement with the locked prediction</div>
        <div style={{ ...bigNumber, color: GREY }}>Warming up</div>
        <div style={{ ...note, marginTop: 6 }}>
          {label === null ? (
            <>Waiting for the first update from this session.</>
          ) : (
            <>
              {evidenceCount} of {MEANINGFUL_MIN_EVIDENCE} {label}.
            </>
          )}{" "}
          No correlation is shown until the session carries enough evidence to mean
          anything.
        </div>
        <div style={trackStyle}>
          <div style={{ ...fillStyle, width: "0%", background: GREY }} />
        </div>
      </div>
    );
  }

  return (
    <div data-testid="agreement-meter" data-state="meaningful" style={panel}>
      <div style={panelHeading}>Agreement with the locked prediction</div>
      {spearman === null ? (
        <div style={{ ...bigNumber, color: GREY }}>No agreement figure sent</div>
      ) : (
        <>
          <div data-testid="agreement-rho" style={{ ...bigNumber, color: REAL }}>
            {spearman.toFixed(2)}
          </div>
          <div style={{ ...note, marginTop: 6 }}>
            Spearman rho, live attention vs the prediction locked before this session.
          </div>
          <div style={trackStyle}>
            <div style={zeroMarkStyle} />
            <div
              style={{
                ...markerStyle,
                left: trackPosition(spearman),
              }}
            />
          </div>
          <div style={{ ...note, display: "flex", justifyContent: "space-between" }}>
            <span>-1</span>
            <span>0</span>
            <span>+1</span>
          </div>
          {relative !== null && typeof ceiling === "number" && (
            <div data-testid="agreement-relative" style={{ ...note, marginTop: 8 }}>
              Relative to the noise ceiling ({ceiling.toFixed(2)}): {relative.toFixed(2)}
            </div>
          )}
        </>
      )}
      <div style={{ ...note, marginTop: 8 }}>
        {evidenceCount} {label ?? "events"} so far.
      </div>
    </div>
  );
}

const warmingPanel: CSSProperties = {
  ...panel,
  color: GREY,
  // Dashed, grey and unmistakably provisional. Written as the `border`
  // shorthand, not `borderStyle`, because React warns when a re-render swaps
  // one form for the other and the meter re-renders on every message.
  border: `1px dashed ${PANEL_BORDER}`,
};

const trackStyle: CSSProperties = {
  position: "relative",
  height: 12,
  marginTop: 12,
  borderRadius: 6,
  border: `1px solid ${PANEL_BORDER}`,
  background: "#141821",
};

const fillStyle: CSSProperties = {
  height: "100%",
  borderRadius: 6,
};

const zeroMarkStyle: CSSProperties = {
  position: "absolute",
  left: "50%",
  top: -3,
  bottom: -3,
  width: 1,
  background: PANEL_BORDER,
};

const markerStyle: CSSProperties = {
  position: "absolute",
  top: -4,
  width: 6,
  height: 18,
  marginLeft: -3,
  borderRadius: 3,
  background: REAL,
};
