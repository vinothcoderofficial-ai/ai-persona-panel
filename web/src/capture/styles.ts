import type { CSSProperties } from "react";

/**
 * The capture screens are plain inline styles, like the rest of the web app -
 * there is no CSS pipeline in this project and one screen flow does not earn
 * one. Colours match the store shell in main.tsx.
 */
export const screen: CSSProperties = {
  position: "fixed",
  inset: 0,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  padding: 24,
  background: "#151920",
  color: "#e8eaed",
  fontFamily: "system-ui, -apple-system, Segoe UI, sans-serif",
  fontSize: 15,
  lineHeight: 1.5,
};

export const panel: CSSProperties = {
  width: "100%",
  maxWidth: 620,
  background: "#1c2129",
  border: "1px solid #2b323d",
  borderRadius: 10,
  padding: "28px 32px",
};

export const heading: CSSProperties = {
  margin: "0 0 14px",
  fontSize: 21,
  fontWeight: 600,
};

export const paragraph: CSSProperties = {
  margin: "0 0 12px",
};

export const list: CSSProperties = {
  margin: "0 0 18px",
  paddingLeft: 20,
  opacity: 0.9,
};

export const note: CSSProperties = {
  margin: "14px 0 0",
  fontSize: 13,
  opacity: 0.65,
};

export const buttonRow: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: 10,
  marginTop: 20,
};

const buttonBase: CSSProperties = {
  padding: "10px 18px",
  borderRadius: 7,
  fontSize: 15,
  fontFamily: "inherit",
  cursor: "pointer",
};

export const primaryButton: CSSProperties = {
  ...buttonBase,
  border: "1px solid #4f8cff",
  background: "#3b6fd4",
  color: "#ffffff",
};

export const secondaryButton: CSSProperties = {
  ...buttonBase,
  border: "1px solid #3a424f",
  background: "transparent",
  color: "#e8eaed",
};

export function disabledButton(disabled: boolean): CSSProperties {
  return disabled ? { opacity: 0.4, cursor: "not-allowed" } : {};
}

/** Yes/No answers and their selected state. Nothing is selected to begin with. */
export function choiceButton(selected: boolean): CSSProperties {
  return {
    ...buttonBase,
    minWidth: 76,
    border: selected ? "1px solid #4f8cff" : "1px solid #3a424f",
    background: selected ? "#27395a" : "transparent",
    color: "#e8eaed",
  };
}
