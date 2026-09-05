import type { CSSProperties } from "react";

/**
 * The dashboard is the fourth screen, and the one where "how close was
 * synthetic to real" finally gets read out as numbers. It is dressed the same
 * way the what-if panel and the spectator view are, so a recording that cuts
 * between the three does not look like three different products: large
 * figures for the headline metrics, high contrast, and real shown in blue
 * everywhere synthetic is shown in amber.
 *
 * Inline styles, like the rest of this app - there is no CSS pipeline. The
 * palette matches the other two screens' on purpose, but this module is
 * deliberately its own copy: nothing under `src/dashboard/` imports from
 * `src/whatif/` or `src/spectator/`, which keeps the dashboard out of the
 * import graph that `web/tests/spectatorIsolation.test.ts` guards.
 */

export const INK = "#e8eaed";
export const BACKDROP = "#12151b";
export const PANEL_BG = "#1c2129";
export const PANEL_BORDER = "#2b323d";
/** The real shopper's own measured attention. */
export const REAL = "#4f8cff";
/** The synthetic panel's prediction. */
export const SYNTH = "#f59e0b";
/** "There is no number here" - the colour of an absent figure and of muted chart chrome. */
export const GREY = "#7a828f";
export const ALERT = "#ff6b5e";

export const root: CSSProperties = {
  position: "fixed",
  inset: 0,
  overflow: "auto",
  padding: 18,
  boxSizing: "border-box",
  background: BACKDROP,
  color: INK,
  fontFamily: "system-ui, -apple-system, Segoe UI, sans-serif",
  fontSize: 15,
  lineHeight: 1.45,
};

export const panel: CSSProperties = {
  background: PANEL_BG,
  border: `1px solid ${PANEL_BORDER}`,
  borderRadius: 10,
  padding: 16,
};

export const panelHeading: CSSProperties = {
  margin: "0 0 10px",
  fontSize: 13,
  fontWeight: 700,
  letterSpacing: "0.09em",
  textTransform: "uppercase",
  opacity: 0.72,
};

export const mono: CSSProperties = {
  fontFamily: "ui-monospace, SFMono-Regular, Consolas, Menlo, monospace",
};

export const bigNumber: CSSProperties = {
  ...mono,
  fontSize: 40,
  fontWeight: 700,
  lineHeight: 1.1,
};

export const note: CSSProperties = {
  fontSize: 13,
  opacity: 0.66,
};

export const alertPanel: CSSProperties = {
  margin: "0 0 14px",
  padding: "10px 14px",
  borderRadius: 8,
  border: `1px solid ${ALERT}`,
  background: "#2a1512",
  color: INK,
  fontSize: 14,
};

/** Used wherever a figure is absent, so "not applicable" never looks like data. */
export const absent: CSSProperties = {
  ...mono,
  color: GREY,
  fontSize: 15,
  fontStyle: "italic",
};
