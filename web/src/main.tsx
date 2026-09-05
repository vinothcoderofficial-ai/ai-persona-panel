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
import { Launcher } from "@/launcher/Launcher";
import { readLastSession, rememberSession, type LastSession } from "@/session/lastSession";
import { PlanogramScene } from "@/store/PlanogramScene";
import Experiment from "@/dashboard/Experiment";
import { SpectatorView } from "@/spectator/SpectatorView";
import { WhatIfPanel } from "@/whatif/WhatIfPanel";

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

/**
 * The effective query for this page, from **either side of the `#`**.
 *
 * `/?variant=B` is the participant link `scripts/collect_link.py` writes, and
 * `#/dashboard?session=<id>&variant=<id>` is the spelling README's screens
 * table documents - so both have to be read, everywhere, or a URL that names
 * something is silently answered with something else. That is exactly what
 * happened before this existed: `#/dashboard?session=X` put X in
 * `location.hash`, the dashboard read only `location.search`, found nothing,
 * loaded the remembered session instead, and printed a note saying no session
 * had been named. Wrong data under a confident caption is the worst failure
 * this app has.
 *
 * The hash wins on a collision, because it is the half that names the route -
 * the same rule, deliberately, that `spectator/SpectatorView.tsx:spectatorQuery`
 * has always applied. It is stated twice rather than shared because that module
 * is a page, not a URL library, and the router may not depend on one screen to
 * read another's address; the two are pinned by their own tests
 * (`spectatorParams.test.ts` and `sessionFallback.test.tsx`).
 */
function mergedQuery(search: string, hash: string): URLSearchParams {
  const merged = new URLSearchParams(search);
  const marker = hash.indexOf("?");
  if (marker !== -1) {
    for (const [key, value] of new URLSearchParams(hash.slice(marker + 1))) {
      merged.set(key, value);
    }
  }
  return merged;
}

function currentQuery(): URLSearchParams {
  return mergedQuery(window.location.search, window.location.hash);
}

function variantFromQuery(): string {
  const requested = currentQuery().get("variant");
  return requested !== null && requested.length > 0 ? requested : "A";
}

function skipCaptureFromQuery(): boolean {
  const value = currentQuery().get("skip_capture");
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

        // Leave the note before anything else can go wrong. The session id is
        // generated here and nowhere else, and the two screens that need it -
        // the spectator on the second monitor, the dashboard afterwards - are
        // opened in other windows, so without this the operator's only copy of
        // it is in a network tab. Recorded at this point on purpose: the
        // session and its prediction lock exist on the server from the moment
        // this call returns, whether or not this mount survives to render.
        // `rememberSession` swallows a localStorage that refuses to be written,
        // so a convenience note can never cost a measurement.
        rememberSession({
          session_id: session.session_id,
          variant_id: session.variant_id,
          started_at: session.started_at,
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

/**
 * Settle which session the dashboard is about, and put it where `Experiment`
 * looks.
 *
 * `Experiment` reads `?experiment=` / `?session=&variant=` out of
 * `location.search` and nowhere else, so this materialises the effective query
 * - both sides of the `#`, hash winning - into `location.search` before it
 * mounts. Two jobs, in this order:
 *
 *   1. **A named session is promoted.** `#/dashboard?session=X` means X, and
 *      moving X into the search is how `Experiment` gets told without reaching
 *      inside it. No note: the URL named a session and that session is what is
 *      on screen.
 *   2. **Only an unnamed one is filled in** from the note the store left, and
 *      that substitution is disclosed on screen and written into the address
 *      bar, so the URL stops implying a session and starts naming one.
 *
 * With nothing named and nothing remembered it does nothing at all, and
 * `Experiment` shows its own message naming the two params it accepts, which is
 * the honest screen.
 *
 * The typed fragment is left exactly as typed. It duplicates what is now in the
 * search, which is untidy, but rewriting somebody's URL to tidy it would also
 * discard any param this function does not know about.
 */
function writeSearch(params: URLSearchParams): boolean {
  const query = params.toString();
  if (query === new URLSearchParams(window.location.search).toString()) return true;
  try {
    window.history.replaceState(
      window.history.state,
      "",
      `${window.location.pathname}?${query}${window.location.hash}`,
    );
    return true;
  } catch {
    return false;
  }
}

function adoptLastSession(): LastSession | null {
  const params = currentQuery();
  const named = (key: string): boolean => {
    const value = params.get(key);
    return value !== null && value.length > 0;
  };

  if (named("experiment") || named("session")) {
    // Promoted, not adopted. A failed rewrite here leaves `Experiment` reading
    // whatever the search already held - which is the URL as typed, minus the
    // hash half it cannot see - and it says so itself rather than being
    // captioned by this file.
    writeSearch(params);
    return null;
  }

  const stored = readLastSession();
  if (stored === null) return null;

  params.set("session", stored.session_id);
  params.set("variant", stored.variant_id);
  // Nothing else can tell Experiment which session to load, so a URL that
  // cannot be rewritten means the honest screen is Experiment's own message
  // rather than a note about a session it is not actually showing.
  if (!writeSearch(params)) return null;
  return stored;
}

/**
 * The dashboard, with one thing in front of it: which session it is showing.
 *
 * The adoption runs in `useState`'s initialiser, which fires exactly once,
 * during this component's own render - before any child effect reads the
 * location, which is what `Experiment` does on mount.
 */
function Dashboard() {
  const [followed] = useState<LastSession | null>(adoptLastSession);

  return (
    <>
      {followed !== null && (
        <p data-testid="dashboard-followed-session" style={followingStyle}>
          This URL named no session, so the dashboard is following the last session started
          in this browser: <strong>{followed.session_id}</strong> (variant{" "}
          {followed.variant_id}, started {followed.started_at}). Put{" "}
          <code>?session=&lt;id&gt;&amp;variant=&lt;id&gt;</code> or{" "}
          <code>?experiment=&lt;id&gt;</code> in the URL to see a different one.
        </p>
      )}
      <Experiment />
    </>
  );
}

/**
 * Which screen the fragment asks for. Anything else - `#/`, `#`, no fragment at
 * all - is the store, and that must stay true: `scripts/collect_link.py` hands
 * participants `https://host/?variant=X` with no fragment, so a bare URL is a
 * shopper's link and can never become a menu.
 *
 * A hash is enough here. Each of these screens reads its own params out of the
 * URL, and a router would be three dependencies for one branch.
 */
type Route = "store" | "home" | "whatif" | "spectator" | "dashboard";

function routeFromHash(hash: string): Route {
  // #/home is the operator's launcher (an additional route, never the default):
  // four screens that were only reachable by typing their URLs, one of which
  // needed a uuid typed by hand on camera.
  if (hash.startsWith("#/home")) return "home";

  // #/whatif is the S8 planning screen: change one thing about the shelf and
  // re-run 10,000 synthetic shoppers per persona against it. It creates no
  // session and measures nobody, so it needs none of the capture flow above; it
  // opens the base planogram through the patch-free variant A, because
  // POST /whatif applies its patches to the base planogram and the controls have
  // to reason about that same layout.
  if (hash.startsWith("#/whatif")) return "whatif";

  // #/spectator?session=<id> is the second screen (S7). It is a separate window
  // on a second monitor and never the shopper's: CLAUDE.md forbids the gaze dot
  // on the screen of the person being measured, because people stare at it. This
  // file is the only module that knows about both, and it renders exactly one.
  // SpectatorView reads its own session id, ?fake=1 and prediction badge from the
  // query string, the way Experiment does.
  if (hash.startsWith("#/spectator")) return "spectator";

  // #/dashboard shows the experiment page, which reads its own ids from the
  // query string.
  if (hash.startsWith("#/dashboard")) return "dashboard";

  return "store";
}

/**
 * The one thing above the four screens: which of them is on.
 *
 * This used to be read once, at module scope, before `render()`. That made the
 * hash a page-load argument rather than a route - typing `#/whatif` while the
 * store was open, or clicking an `<a href="#/dashboard">`, changed the address
 * bar and nothing else until the page was reloaded. Listening for `hashchange`
 * is what makes those links work.
 *
 * Only the *route* is held in state, never the raw hash. React bails out of a
 * `useState` update whose value is unchanged, so a fragment that moves within
 * one screen (`#/` to `#`, `#/spectator` to `#/spectator?session=x`) does not
 * re-render this component at all - and, more to the point, cannot remount the
 * store. A remounted store POSTs a second session and the server writes a
 * second prediction lock for one shopper, which is a real cost in this project:
 * `predictions/` is committed evidence. Leaving the store and coming back is
 * therefore the only thing that opens another session, and that one is
 * deliberate - it is a second visit.
 */
function Root() {
  const [route, setRoute] = useState<Route>(() => routeFromHash(window.location.hash));

  useEffect(() => {
    const onHashChange = () => setRoute(routeFromHash(window.location.hash));
    window.addEventListener("hashchange", onHashChange);
    // The hash can already have moved between the first render and this line -
    // a hand-typed fragment during load, or a redirect. Read it once more so
    // the first paint cannot be of the wrong screen.
    onHashChange();
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  if (route === "home") return <Launcher />;
  if (route === "whatif") return <WhatIfPanel />;
  if (route === "spectator") return <SpectatorView />;
  if (route === "dashboard") return <Dashboard />;
  return <App />;
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

/**
 * The "this is not the session you asked for, because you asked for none" note.
 *
 * Pinned to the viewport rather than laid out above the dashboard: `Experiment`
 * owns its own full-screen root, so a sibling in normal flow would be painted
 * over and the disclosure would exist only in the DOM. It carries its own
 * colours - the palette the spectator and what-if screens use - because
 * `src/dashboard/styles.ts` belongs to that page and this note has to read the
 * same whatever happens to it.
 */
const followingStyle: CSSProperties = {
  position: "fixed",
  left: 0,
  right: 0,
  bottom: 0,
  zIndex: 20,
  margin: 0,
  padding: "8px 14px",
  boxSizing: "border-box",
  borderTop: "1px solid #4f8cff",
  background: "#1c2129",
  color: "#e8eaed",
  fontFamily: "system-ui, -apple-system, Segoe UI, sans-serif",
  fontSize: 13,
  lineHeight: 1.45,
};

const container = document.getElementById("root");
if (container === null) throw new Error("web/index.html is missing #root");

/**
 * The mounted app.
 *
 * Exported for one reason, and nothing in the app reads it: a test that
 * `import()`s this module has no other handle on the root the import just
 * mounted. `Root` unregisters its `hashchange` listener when it unmounts, and
 * jsdom gives every test in a file the same `window`, so a root left behind
 * would keep routing itself on the next hash change - and each store it
 * remounted would open its own session. A browser tab mounts exactly one root
 * and keeps it for the life of the page.
 *
 * No StrictMode: its double-mounted effects would open two sessions, and write
 * two prediction locks, on every page load.
 */
export const appRoot = createRoot(container);
appRoot.render(<Root />);
