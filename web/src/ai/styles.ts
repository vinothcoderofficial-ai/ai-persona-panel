import type { CSSProperties } from "react";

/**
 * `#/ai` is on the demo shot list, so it is dressed for a camera: the model
 * name large enough to read on a recording, and nothing load-bearing in a thin
 * grey.
 *
 * Inline styles, like the rest of this app — there is no CSS pipeline. The
 * palette matches the launcher, spectator and what-if screens so the windows
 * look like one product, but this module is deliberately its own copy: nothing
 * under `src/ai/` imports from `src/spectator/`, which is what keeps this page
 * out of the import graph `web/tests/spectatorIsolation.test.ts` guards.
 */

export const INK = "#e8eaed";
export const BACKDROP = "#12151b";
export const PANEL_BG = "#1c2129";
export const PANEL_BORDER = "#2b323d";
/** Something live — a model that can be called, a link that goes somewhere. */
export const ACCENT = "#4f8cff";
/** A number the model moved. */
export const CHANGED = "#f59e0b";
/** Something the model cannot do, and why. */
export const ALERT = "#ff6b5e";
export const GREY = "#7a828f";
export const OK = "#4ade80";

export const mono =
  "ui-monospace, SFMono-Regular, Consolas, Menlo, monospace";

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

export const note: CSSProperties = {
  fontSize: 13,
  opacity: 0.78,
};

export const monoStyle: CSSProperties = { fontFamily: mono };

export const linkButton: CSSProperties = {
  display: "inline-block",
  padding: "7px 14px",
  borderRadius: 7,
  border: `1px solid ${ACCENT}`,
  background: "#22304a",
  color: INK,
  fontSize: 13,
  fontWeight: 600,
  textDecoration: "none",
  whiteSpace: "nowrap",
};

export const primaryButton: CSSProperties = {
  ...linkButton,
  cursor: "pointer",
  fontFamily: "inherit",
};

export const disabledButton: CSSProperties = {
  ...primaryButton,
  border: `1px solid ${PANEL_BORDER}`,
  background: "#20242c",
  color: GREY,
  cursor: "not-allowed",
};

export const tab: CSSProperties = {
  padding: "8px 12px",
  borderRadius: 7,
  border: `1px solid ${PANEL_BORDER}`,
  background: "#171c24",
  color: INK,
  fontSize: 14,
  fontFamily: "inherit",
  cursor: "pointer",
  textAlign: "left",
};

export const tabSelected: CSSProperties = {
  ...tab,
  border: `1px solid ${ACCENT}`,
  background: "#22304a",
  fontWeight: 600,
};

export const pre: CSSProperties = {
  margin: 0,
  padding: 12,
  borderRadius: 8,
  border: `1px solid ${PANEL_BORDER}`,
  background: "#12161d",
  color: INK,
  fontFamily: mono,
  fontSize: 12.5,
  lineHeight: 1.5,
  whiteSpace: "pre-wrap",
  wordBreak: "break-word",
  overflowX: "auto",
};

export const alertBox: CSSProperties = {
  marginTop: 12,
  padding: "10px 12px",
  borderRadius: 8,
  border: `1px solid ${ALERT}`,
  background: "#2a1a1a",
  color: INK,
  fontSize: 13,
  lineHeight: 1.5,
};

export const cautionBox: CSSProperties = {
  ...alertBox,
  border: `1px solid ${CHANGED}`,
  background: "#2a2415",
};
