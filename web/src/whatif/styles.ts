import type { CSSProperties } from "react";

/**
 * The what-if page is on the demo shot list ("10,000 re-run, `elapsed_ms` on
 * screen"), so it is dressed for a camera: large figures, high contrast, and
 * nothing important in a thin grey.
 *
 * Inline styles, like the rest of this app - there is no CSS pipeline. The
 * palette matches the spectator screen's on purpose, so the two windows look
 * like one product on a recording, but this module is deliberately its own
 * copy: nothing under `src/whatif/` imports from `src/spectator/`, which keeps
 * the what-if page out of the import graph that
 * `web/tests/spectatorIsolation.test.ts` guards.
 */

export const INK = "#e8eaed";
export const BACKDROP = "#12151b";
export const PANEL_BG = "#1c2129";
export const PANEL_BORDER = "#2b323d";
/** The new run. */
export const NEW = "#4f8cff";
/** The run it is being compared with. */
export const OLD = "#f59e0b";
/** A rise. */
export const UP = "#4ade80";
/** A fall. */
export const DOWN = "#ff6b5e";
/** "There is no number here" - never a zero-length bar. */
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

export const label: CSSProperties = {
  display: "block",
  marginBottom: 4,
  fontSize: 12,
  letterSpacing: "0.08em",
  textTransform: "uppercase",
  opacity: 0.7,
};

export const select: CSSProperties = {
  width: "100%",
  padding: "8px 10px",
  borderRadius: 7,
  border: `1px solid ${PANEL_BORDER}`,
  background: "#232935",
  color: INK,
  fontFamily: "inherit",
  fontSize: 14,
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
