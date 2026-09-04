import type { CSSProperties } from "react";

/**
 * The spectator screen is filmed. Every choice here is for a camera, not for a
 * desk: large type, high contrast, no thin greys on dark except where "this is
 * not to be believed yet" is exactly the message.
 *
 * Inline styles, like the rest of this web app - there is no CSS pipeline and
 * one screen does not earn one. Colours match the store shell in main.tsx.
 */

export const INK = "#e8eaed";
export const BACKDROP = "#12151b";
export const PANEL_BG = "#1c2129";
export const PANEL_BORDER = "#2b323d";
/** Live measurement. */
export const REAL = "#4f8cff";
/** The locked prediction. */
export const LOCKED = "#f59e0b";
/** "Not yet worth believing" - the warming-up meter and every disabled figure. */
export const GREY = "#7a828f";
export const ALERT = "#ff6b5e";
/** Hazard yellow, used only for the fake stream. */
export const FAKE = "#ffd21e";

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
  fontSize: 34,
  fontWeight: 700,
  lineHeight: 1.1,
};

export const note: CSSProperties = {
  fontSize: 13,
  opacity: 0.66,
};

export const button: CSSProperties = {
  padding: "8px 16px",
  borderRadius: 7,
  border: `1px solid ${REAL}`,
  background: "#3b6fd4",
  color: "#ffffff",
  fontFamily: "inherit",
  fontSize: 14,
  cursor: "pointer",
};

/** The diagonal hazard stripes that wrap anything synthetic. */
export const hazard: CSSProperties = {
  backgroundImage: `repeating-linear-gradient(45deg, ${FAKE} 0, ${FAKE} 14px, #171208 14px, #171208 28px)`,
};
