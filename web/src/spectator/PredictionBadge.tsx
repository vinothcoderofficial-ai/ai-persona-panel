import type { CSSProperties } from "react";
import { wallClockTime } from "@/spectator/ClockOverlay";
import type { LockView } from "@/spectator/lock";
import { ALERT, FAKE, GREY, INK, LOCKED, mono, note, panelHeading } from "@/spectator/styles";

/**
 * The evidence badge: the first 8 hex characters of the lock's `sha256` and its
 * `created_at` (SPEC 4.6).
 *
 * This is the on-camera half of the demo's central claim - *"prediction locked
 * at 10:32:07, shopping began 10:32:41"* - so two things matter more than
 * looking tidy:
 *
 *  1. **It is on screen before any gaze data arrives.** `SpectatorView` renders
 *     it from the URL, before the socket has even opened, because a badge that
 *     only appeared alongside the first heatmap frame would prove nothing about
 *     ordering.
 *  2. **It is never filled in from nothing.** With no lock supplied it says so
 *     in as many words. A badge that invents eight plausible hex characters
 *     would be the exact opposite of evidence.
 */

export interface PredictionBadgeProps {
  lock: LockView;
  /** True when the feed is ws.py's synthetic stream: there is no real lock. */
  fake: boolean;
}

function lockedAt(createdAt: string): string | null {
  const date = new Date(createdAt);
  return Number.isNaN(date.getTime()) ? null : wallClockTime(date);
}

export function PredictionBadge({ lock, fake }: PredictionBadgeProps) {
  const time = lock.created_at === null ? null : lockedAt(lock.created_at);

  return (
    <div data-testid="prediction-badge" style={badgeStyle(fake)}>
      <div style={panelHeading}>Prediction locked before this session</div>

      {fake && (
        <div data-testid="prediction-fake" style={fakeLineStyle}>
          FAKE STREAM — there is no prediction lock behind these numbers.
        </div>
      )}

      {lock.sha256_prefix === null ? (
        <div data-testid="prediction-lock-missing" style={missingStyle}>
          No prediction lock supplied. Open this page with{" "}
          <code style={mono}>?sha256=&lt;hash&gt;&amp;locked_at=&lt;created_at&gt;</code> (or{" "}
          <code style={mono}>?lock=&lt;url of predictions/&#123;id&#125;.json&gt;</code>) to
          show the badge. Nothing here is filled in from guesswork.
        </div>
      ) : (
        <div style={{ display: "flex", gap: 28, flexWrap: "wrap", alignItems: "flex-end" }}>
          <div>
            <div style={labelStyle}>sha256</div>
            <code data-testid="prediction-hash" style={hashStyle}>
              {lock.sha256_prefix}
            </code>
          </div>
          <div>
            <div style={labelStyle}>locked at</div>
            {time !== null && (
              <div data-testid="prediction-locked-time" style={timeStyle}>
                {time}
              </div>
            )}
            <div data-testid="prediction-created-at" style={isoStyle}>
              {lock.created_at}
            </div>
          </div>
        </div>
      )}

      {lock.prediction_id !== null && (
        <div style={{ ...note, marginTop: 6 }}>
          prediction_id <span style={mono}>{lock.prediction_id}</span>
        </div>
      )}
    </div>
  );
}

function badgeStyle(fake: boolean): CSSProperties {
  return {
    flex: "1 1 320px",
    padding: "12px 16px",
    borderRadius: 10,
    border: `2px solid ${fake ? FAKE : LOCKED}`,
    background: fake ? "#241d05" : "#1f1a10",
  };
}

const fakeLineStyle: CSSProperties = {
  marginBottom: 8,
  color: FAKE,
  fontWeight: 700,
  letterSpacing: "0.04em",
};

const missingStyle: CSSProperties = {
  color: ALERT,
  fontSize: 14,
};

const labelStyle: CSSProperties = {
  fontSize: 11,
  letterSpacing: "0.09em",
  textTransform: "uppercase",
  color: GREY,
};

const hashStyle: CSSProperties = {
  ...mono,
  fontSize: 32,
  fontWeight: 700,
  letterSpacing: "0.06em",
  color: LOCKED,
};

const timeStyle: CSSProperties = {
  ...mono,
  fontSize: 32,
  fontWeight: 700,
  color: INK,
  fontVariantNumeric: "tabular-nums",
};

const isoStyle: CSSProperties = {
  ...mono,
  fontSize: 12,
  color: GREY,
};
