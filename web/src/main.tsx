import { useEffect, useState } from "react";
import type { CSSProperties } from "react";
import { createRoot } from "react-dom/client";
import type { Planogram } from "@/contracts/planogram.schema";
import type { Session } from "@/contracts/session.schema";
import { createSession, getResolvedVariant } from "@/api/client";
import type { ArchetypeLabel, Intake } from "@/capture/archetype";
import { CaptureFlow, type CaptureResult } from "@/capture/CaptureFlow";
import type { GazeTracker } from "@/capture/GazeTracker";
import { SessionSocket } from "@/capture/SessionSocket";
import { PlanogramScene } from "@/store/PlanogramScene";
import Experiment from "@/dashboard/Experiment";
import { SpectatorView } from "@/spectator/SpectatorView";

type LoadState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; planogram: Planogram; events: SessionSocket };

/** What the capture flow decides, and what POST /sessions is told (SPEC 4.3). */
interface SessionFields {
  consent: boolean;
  mode: Session["mode"];
  calibration_error_px: number | null;
  intake?: Intake;
  archetype_label?: ArchetypeLabel;
  /**
   * The running tracker a webcam session brings out of the capture flow. It is
   * not part of the session document - it is the live camera, and holding it
   * here is what makes a webcam session produce gaze at all.
   */
  tracker?: GazeTracker;
}

/**
 * `?skip_capture=1` goes straight to the store, for development and for
 * cursor-only testing. It records `consent: false`, which is the truth - nobody
 * sat down and agreed to anything - and which makes the session identifiable as
 * a developer session: SessionGate (S11) rejects it with `no_consent` instead of
 * letting it into the real panel.
 */
const DEV_SKIP_FIELDS: SessionFields = {
  consent: false,
  mode: "cursor_only",
  calibration_error_px: null,
};

function variantFromQuery(): string {
  const requested = new URLSearchParams(window.location.search).get("variant");
  return requested !== null && requested.length > 0 ? requested : "A";
}

function skipCaptureFromQuery(): boolean {
  const value = new URLSearchParams(window.location.search).get("skip_capture");
  return value !== null && value !== "0" && value !== "false";
}

function Store({ variantId, fields }: { variantId: string; fields: SessionFields }) {
  const [state, setState] = useState<LoadState>({ status: "loading" });

  // The camera goes back when the store goes away, whatever happened on the way
  // there: a session that failed to open, a closed tab, an unmounted app.
  // PlanogramScene releases it too, and GazeTracker.stop() is idempotent.
  useEffect(() => {
    const tracker = fields.tracker;
    if (tracker === undefined) return undefined;
    return () => tracker.stop();
  }, [fields]);

  useEffect(() => {
    let cancelled = false;
    let events: SessionSocket | null = null;

    void (async () => {
      try {
        // The session comes first: the server writes the prediction lock on
        // POST /sessions, before it will accept a single event. Consent, mode,
        // intake and the calibration error all come from the capture flow that
        // ran before this component mounted.
        const session = await createSession({
          session_id: crypto.randomUUID(),
          variant_id: variantId,
          consent: fields.consent,
          started_at: new Date().toISOString(),
          // An offscreen window reports a screen of 0; fall back to the viewport.
          screen_w: Math.round(window.screen.width || window.innerWidth),
          screen_h: Math.round(window.screen.height || window.innerHeight),
          mode: fields.mode,
          calibration_error_px: fields.calibration_error_px,
          intake: fields.intake,
          archetype_label: fields.archetype_label,
        });
        const planogram = await getResolvedVariant(variantId);
        if (cancelled) return;

        // Only now, with the session created and its prediction locked, is
        // there anything the socket is allowed to connect to: ws.py refuses a
        // session it does not know (4404) or that has no lock (4409).
        events = new SessionSocket(session.session_id);
        events.start();
        setState({ status: "ready", planogram, events });
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
      events?.stop();
    };
  }, [variantId, fields]);

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
  // consent and mode go down with the scene because checkout is where the
  // session gate runs: it needs what the shopper agreed to and which tracker
  // actually measured them, and both were decided by the capture flow long
  // before the store mounted.
  return (
    <PlanogramScene
      planogram={state.planogram}
      logger={state.events}
      tracker={fields.tracker ?? null}
      consent={fields.consent}
      mode={fields.mode}
    />
  );
}

function App() {
  const variantId = variantFromQuery();
  // No session exists until the capture flow finishes. Consent is what the
  // shopper chose on that first screen, never a constant in this file.
  const [fields, setFields] = useState<SessionFields | null>(
    skipCaptureFromQuery() ? DEV_SKIP_FIELDS : null,
  );

  if (fields === null) {
    return <CaptureFlow onComplete={(result: CaptureResult) => setFields(result)} />;
  }
  return <Store variantId={variantId} fields={fields} />;
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

// #/spectator?session=<id> is the second screen (S7). It is a separate window
// on a second monitor and never the shopper's: CLAUDE.md forbids the gaze dot
// on the screen of the person being measured, because people stare at it. This
// file is the only module that knows about both, and it renders exactly one.
// SpectatorView reads its own session id, ?fake=1 and prediction badge from the
// query string, the way Experiment does.
const isSpectator = window.location.hash.startsWith("#/spectator");

// No StrictMode: its double-mounted effects would open two sessions, and write
// two prediction locks, on every page load.
createRoot(container).render(
  isSpectator ? <SpectatorView /> : isDashboard ? <Experiment /> : <App />,
);
