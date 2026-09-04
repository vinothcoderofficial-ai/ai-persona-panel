import { useEffect, useState } from "react";
import type { CSSProperties } from "react";
import { createRoot } from "react-dom/client";
import type { Planogram } from "@/contracts/planogram.schema";
import { createSession, getResolvedVariant } from "@/api/client";
import { EventLogger } from "@/capture/EventLogger";
import { PlanogramScene } from "@/store/PlanogramScene";
import Experiment from "@/dashboard/Experiment";

type LoadState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; planogram: Planogram; logger: EventLogger };

function variantFromQuery(): string {
  const requested = new URLSearchParams(window.location.search).get("variant");
  return requested !== null && requested.length > 0 ? requested : "A";
}

function App() {
  const [state, setState] = useState<LoadState>({ status: "loading" });
  const variantId = variantFromQuery();

  useEffect(() => {
    let cancelled = false;
    let logger: EventLogger | null = null;

    void (async () => {
      try {
        // The session comes first: the server writes the prediction lock on
        // POST /sessions, before it will accept a single event.
        // S9 puts the real consent screen in front of this.
        const session = await createSession({
          session_id: crypto.randomUUID(),
          variant_id: variantId,
          consent: true,
          started_at: new Date().toISOString(),
          // An offscreen window reports a screen of 0; fall back to the viewport.
          screen_w: Math.round(window.screen.width || window.innerWidth),
          screen_h: Math.round(window.screen.height || window.innerHeight),
          mode: "cursor_only",
        });
        const planogram = await getResolvedVariant(variantId);
        if (cancelled) return;

        logger = new EventLogger(session.session_id);
        logger.start();
        setState({ status: "ready", planogram, logger });
      } catch (error) {
        if (cancelled) return;
        setState({
          status: "error",
          message: error instanceof Error ? error.message : String(error),
        });
      }
    })();

    return () => {
      cancelled = true;
      logger?.stop();
    };
  }, [variantId]);

  if (state.status === "loading") {
    return <div style={messageStyle}>Opening variant {variantId}...</div>;
  }
  if (state.status === "error") {
    return (
      <div style={messageStyle}>
        <div style={{ fontSize: 18, fontWeight: 600 }}>The store could not load.</div>
        <div style={{ marginTop: 10, opacity: 0.85 }}>{state.message}</div>
        <div style={{ marginTop: 10, opacity: 0.6 }}>
          Start the API with <code>make api</code>, then reload.
        </div>
      </div>
    );
  }
  return <PlanogramScene planogram={state.planogram} logger={state.logger} />;
}

const messageStyle: CSSProperties = {
  position: "fixed",
  inset: 0,
  display: "flex",
  flexDirection: "column",
  alignItems: "center",
  justifyContent: "center",
  padding: 24,
  textAlign: "center",
  background: "#151920",
  color: "#e8eaed",
  fontFamily: "system-ui, -apple-system, Segoe UI, sans-serif",
  fontSize: 14,
};

const container = document.getElementById("root");
if (container === null) throw new Error("web/index.html is missing #root");

// #/dashboard shows the experiment page; anything else is the store. A hash is
// enough here -- the dashboard reads its own ids from the query string, and a
// router would be three dependencies for one branch.
const isDashboard = window.location.hash.startsWith("#/dashboard");

// No StrictMode: its double-mounted effects would open two sessions, and write
// two prediction locks, on every page load.
createRoot(container).render(isDashboard ? <Experiment /> : <App />);
