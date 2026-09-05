import { useEffect, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { AgreementMeter } from "@/spectator/AgreementMeter";
import { ClockOverlay } from "@/spectator/ClockOverlay";
import { GazeTrail } from "@/spectator/GazeTrail";
import { LiveHeatmap } from "@/spectator/LiveHeatmap";
import { PredictionBadge } from "@/spectator/PredictionBadge";
import { EVIDENCE_LABEL, formatElapsed, type LiveUpdate } from "@/spectator/liveMessage";
import {
  NO_LOCK,
  fetchLock,
  fetchPredictionLock,
  lockFromQuery,
  resolveLock,
  type LockView,
} from "@/spectator/lock";
import { readLastSession, type LastSession } from "@/session/lastSession";
import {
  SpectatorSocket,
  type SpectatorSocketFactory,
  type SpectatorStatus,
} from "@/spectator/SpectatorSocket";
import { pushGaze, type GazePoint } from "@/spectator/trail";
import {
  ALERT,
  FAKE,
  GREY,
  INK,
  PANEL_BG,
  PANEL_BORDER,
  REAL,
  bigNumber,
  button,
  hazard,
  mono,
  note,
  panel,
  panelHeading,
  root,
} from "@/spectator/styles";

/**
 * The second screen. **Never the shopper's.**
 *
 * CLAUDE.md: *"The shopper's own screen must not show their gaze dot. People
 * stare at the dot and corrupt the data. The dot belongs on the spectator view
 * only."* This page is opened in a second window or on a second monitor while
 * somebody shops, and `web/tests/spectatorIsolation.test.ts` walks the import
 * graph from `store/PlanogramScene.tsx` to prove nothing here can reach it.
 *
 * It subscribes to `ws/spectator/{session_id}` (read-only - the shopper's
 * ingest socket is `capture/SessionSocket` and is deliberately a separate
 * class) and renders the SPEC 4.7 stream: the gaze trail, the live attention
 * beside the locked prediction, the agreement meter, the prediction badge and
 * the wall clock.
 *
 * `?session=<id>` on its own is a complete instruction: the prediction badge
 * and the locked heatmap column come from `GET /sessions/{id}/prediction`. The
 * lock params below are explicit overrides of that default, in the precedence
 * `lock.ts:resolveLock` documents.
 *
 * URL - the params may be written before the hash (`/?session=x#/spectator`,
 * the way the dashboard is opened) or after it (`#/spectator?session=x`):
 *
 *     #/spectator?session=<id>
 *                [&fake=1]                         ws.py's synthetic demo stream
 *                [&sha256=<hex>&locked_at=<iso>]   overrides the badge
 *                [&lock=<url>]                     overrides both, whole document
 *                [&screen_w=&screen_h=]            the shopper's screen size
 *                [&screenshot=<url>]               a still of the station
 *                [&ceiling=<rho>]                  the panel's noise ceiling
 */

/**
 * ws.py's fake stream draws gaze in [0,1439] x [0,899], and 1440x900 is the
 * commonest laptop panel in the office, so it is also the least surprising
 * default for a real session that did not say. `?screen_w=&screen_h=` overrides
 * it with what the session document actually recorded.
 */
export const DEFAULT_SCREEN = { w: 1440, h: 900 };

/** How often the trail re-renders so the fade is smooth on camera. */
export const FADE_INTERVAL_MS = 60;

export interface SpectatorParams {
  sessionId: string | null;
  fake: boolean;
  lock: LockView;
  lockUrl: string | null;
  screen: { w: number; h: number };
  screenshotUrl: string | null;
  ceiling: number | null;
}

function positiveInt(value: string | null, fallback: number): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? Math.round(parsed) : fallback;
}

function finiteOrNull(value: string | null): number | null {
  if (value === null || value.length === 0) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function flag(value: string | null): boolean {
  return value !== null && value !== "0" && value !== "false";
}

/**
 * The effective query string for this page, from either side of the hash.
 *
 * `#/dashboard` is opened as `/?experiment=<id>#/dashboard`, so params live in
 * `location.search`. But `#/spectator?session=<id>` is how anyone actually
 * types a hash route, and those params land in `location.hash` where
 * `location.search` cannot see them. Both spellings work; the hash wins on a
 * collision, because it is the half that names this route.
 */
export function spectatorQuery(search: string, hash: string): string {
  const marker = hash.indexOf("?");
  const merged = new URLSearchParams(search);
  if (marker !== -1) {
    for (const [key, value] of new URLSearchParams(hash.slice(marker + 1))) {
      merged.set(key, value);
    }
  }
  return merged.toString();
}

/** Everything this page needs, read out of its own URL. */
export function spectatorParamsFromQuery(search: string): SpectatorParams {
  const params = new URLSearchParams(search);
  const session = params.get("session");
  const lockUrl = params.get("lock");
  const screenshot = params.get("screenshot");
  return {
    sessionId: session !== null && session.length > 0 ? session : null,
    fake: flag(params.get("fake")),
    lock: lockFromQuery(search),
    lockUrl: lockUrl !== null && lockUrl.length > 0 ? lockUrl : null,
    screen: {
      w: positiveInt(params.get("screen_w"), DEFAULT_SCREEN.w),
      h: positiveInt(params.get("screen_h"), DEFAULT_SCREEN.h),
    },
    screenshotUrl: screenshot !== null && screenshot.length > 0 ? screenshot : null,
    ceiling: finiteOrNull(params.get("ceiling")),
  };
}

export interface SpectatorViewProps {
  sessionId?: string | null;
  fake?: boolean;
  lock?: LockView;
  lockUrl?: string | null;
  screen?: { w: number; h: number };
  screenshotUrl?: string | null;
  ceiling?: number | null;
  /** Injected in tests, so the whole page runs in jsdom with no server. */
  createSocket?: SpectatorSocketFactory;
  /**
   * The default lock source, `GET /sessions/{id}/prediction`. Injectable the
   * same way the socket factory is, so tests drive it without a server. It must
   * never reject and never invent a lock: NO_LOCK is how "no lock" is said.
   */
  fetchPrediction?: (sessionId: string) => Promise<LockView>;
  /**
   * The last session this browser opened, used only when the URL names none.
   * Injected in tests; otherwise the note `main.tsx` leaves in localStorage
   * when the store creates a session.
   */
  readStoredSession?: () => LastSession | null;
  /** The spectator's clock, used to age the gaze trail. */
  now?: () => number;
  /** The wall clock on the recording. */
  wallClock?: () => Date;
  /** `null` freezes the fade ticker; tests drive `now` themselves. */
  fadeIntervalMs?: number | null;
}

const defaultNow = () => performance.now();

export function SpectatorView(props: SpectatorViewProps) {
  const fromQuery = useRef<SpectatorParams | null>(null);
  if (fromQuery.current === null) {
    fromQuery.current = spectatorParamsFromQuery(
      spectatorQuery(window.location.search, window.location.hash),
    );
  }
  const query = fromQuery.current;

  /**
   * The session id, and where it came from.
   *
   * It is generated in the browser by `crypto.randomUUID()` when the store
   * opens, and this window is a different window - usually on a different
   * monitor - so "which session?" used to be answered by reading a uuid off a
   * network tab and typing it in. When the URL names no session, this page
   * follows the last session the store opened in this browser instead.
   *
   * A named session always wins, and a followed one is never silent: the note
   * below says which session is on screen and that nobody asked for it by name.
   * Showing one session's gaze under another session's name is the one failure
   * this page must not have.
   */
  const storedSession = useRef<LastSession | null | undefined>(undefined);
  if (storedSession.current === undefined) {
    storedSession.current = (props.readStoredSession ?? readLastSession)();
  }
  const stored = storedSession.current ?? null;
  const namedSessionId = props.sessionId === undefined ? query.sessionId : props.sessionId;
  const named =
    namedSessionId !== null && namedSessionId.length > 0 ? namedSessionId : null;
  const followed = named === null ? stored : null;
  const sessionId = named ?? followed?.session_id ?? null;
  const askedForFake = props.fake ?? query.fake;
  const queryLock = props.lock ?? query.lock;
  const lockUrl = props.lockUrl === undefined ? query.lockUrl : props.lockUrl;
  const screen = props.screen ?? query.screen;
  const screenshotUrl =
    props.screenshotUrl === undefined ? query.screenshotUrl : props.screenshotUrl;
  const ceiling = props.ceiling === undefined ? query.ceiling : props.ceiling;
  const fadeIntervalMs =
    props.fadeIntervalMs === undefined ? FADE_INTERVAL_MS : props.fadeIntervalMs;
  const now = props.now ?? defaultNow;

  const hasSession = sessionId !== null && sessionId.length > 0;

  const [update, setUpdate] = useState<LiveUpdate | null>(null);
  const [status, setStatus] = useState<SpectatorStatus>(
    hasSession ? "connecting" : "disconnected",
  );
  const [points, setPoints] = useState<GazePoint[]>([]);
  const [fileLock, setFileLock] = useState<LockView>(NO_LOCK);
  const [apiLock, setApiLock] = useState<LockView>(NO_LOCK);
  // True only while the default fetch is genuinely in flight, so a slow API
  // never shows "no lock supplied" for a session that has one.
  const [lockLoading, setLockLoading] = useState(false);
  const [attempt, setAttempt] = useState(0);
  // Re-render so the trail fades between messages. The value is never read;
  // ageing is `trail.ts:visibleTrail(points, now())`, computed below.
  const [, setFadeTick] = useState(0);

  // Held in refs so a caller passing an inline arrow cannot make the effect
  // tear the socket down and reopen it on every render.
  const nowRef = useRef(now);
  nowRef.current = now;
  const createSocketRef = useRef(props.createSocket);
  createSocketRef.current = props.createSocket;
  const fetchPredictionRef = useRef(props.fetchPrediction);
  fetchPredictionRef.current = props.fetchPrediction;

  useEffect(() => {
    if (sessionId === null || sessionId.length === 0) return undefined;
    const socket = new SpectatorSocket(sessionId, {
      fake: askedForFake,
      createSocket: createSocketRef.current,
      onUpdate: (next) => {
        setUpdate(next);
        const gaze = next.latest_gaze;
        if (gaze !== null) {
          setPoints((previous) =>
            pushGaze(previous, { x: gaze.x, y: gaze.y, t: nowRef.current() }),
          );
        }
      },
      onStatus: (next) => {
        setStatus(next);
        // The trail is live data by definition. A frozen dot left on screen
        // would read, on camera, as a person staring at one product.
        if (next !== "live") setPoints([]);
      },
    });
    socket.start();
    return () => socket.stop();
  }, [sessionId, askedForFake, attempt]);

  useEffect(() => {
    // A fake stream has no lock at all: `?fake=1` never touches a session, its
    // prediction_id is the constant "fake-prediction", and the yellow badge
    // already says there is nothing behind the numbers. Asking the API would be
    // a guaranteed 404 against a session id that was never registered.
    if (askedForFake || sessionId === null || sessionId.length === 0) return undefined;

    const load = fetchPredictionRef.current ?? fetchPredictionLock;
    let cancelled = false;
    setLockLoading(true);
    const finish = (loaded: LockView) => {
      if (cancelled) return;
      setApiLock(loaded);
      setLockLoading(false);
    };
    try {
      // Issued synchronously, so the badge starts filling in from the moment
      // the window opens rather than a microtask later.
      // An injected stub is allowed to reject; the real fetch never does.
      // Either way a failure is "no lock", never zeros and never a fresh sim.
      void load(sessionId).then(finish, () => finish(NO_LOCK));
    } catch {
      finish(NO_LOCK);
    }
    return () => {
      cancelled = true;
    };
  }, [sessionId, askedForFake]);

  useEffect(() => {
    if (lockUrl === null) return undefined;
    let cancelled = false;
    void fetchLock(lockUrl).then((loaded) => {
      if (!cancelled) setFileLock(loaded);
    });
    return () => {
      cancelled = true;
    };
  }, [lockUrl]);

  useEffect(() => {
    if (fadeIntervalMs === null || points.length === 0) return undefined;
    const timer = setInterval(() => setFadeTick((tick) => tick + 1), fadeIntervalMs);
    return () => clearInterval(timer);
  }, [fadeIntervalMs, points.length]);

  // Lowest priority first: the endpoint is the default, and the two URL forms
  // are explicit overrides. `lock.ts:resolveLock` documents why in that order.
  const lock = resolveLock([apiLock, queryLock, fileLock]);
  // The server's own marks win: a frame that says it is synthetic is synthetic
  // whether or not this page asked for the fake stream.
  const fake = askedForFake || update?.fake === true;
  // "Stale" means a feed that should be live is not. With no session there is
  // nothing to be stale, so nothing is greyed out for effect.
  const stale = hasSession && status === "disconnected";

  return (
    <div
      data-testid="spectator-view"
      data-fake={String(fake)}
      data-stale={String(stale)}
      style={fake ? { ...root, boxShadow: `inset 0 0 0 10px ${FAKE}` } : root}
    >
      {fake && (
        <div data-testid="fake-banner" style={fakeBannerStyle}>
          <span style={fakeBannerTextStyle}>
            FAKE DEMO STREAM — SYNTHETIC DATA, NOT A MEASUREMENT
          </span>
        </div>
      )}

      <header style={headerStyle}>
        <div>
          <div style={panelHeading}>ShopperTwin spectator</div>
          <div style={{ ...mono, fontSize: 13, color: GREY }}>
            session {hasSession ? sessionId : "—"}
          </div>
          <div
            data-testid="spectator-status"
            data-status={status}
            style={statusStyle(status)}
          >
            {status === "live" ? "LIVE" : status === "connecting" ? "CONNECTING" : "DISCONNECTED"}
          </div>
        </div>
        <PredictionBadge
          lock={lock}
          fake={fake}
          loading={lockLoading && lock.sha256_prefix === null}
        />
        <ClockOverlay now={props.wallClock} />
      </header>

      {followed !== null && (
        <div data-testid="spectator-followed-session" style={followedPanelStyle}>
          This URL named no session, so this screen is following the last session started in
          this browser: <code style={mono}>{followed.session_id}</code> — variant{" "}
          {followed.variant_id}, started {followed.started_at}. Open{" "}
          <code style={mono}>#/spectator?session=&lt;session_id&gt;</code> to watch a
          different one.
        </div>
      )}

      {!hasSession && (
        <div data-testid="spectator-no-session" style={alertPanelStyle}>
          No session to watch. Open this page as{" "}
          <code style={mono}>#/spectator?session=&lt;session_id&gt;</code>, or{" "}
          <code style={mono}>#/spectator?session=demo&amp;fake=1</code> for the server&apos;s
          synthetic demo stream.
        </div>
      )}

      {hasSession && stale && (
        <div data-testid="disconnected-banner" style={alertPanelStyle}>
          <strong>Disconnected from ws/spectator.</strong> Everything below is stale — it
          is the last frame received, not what is happening now.
          <button
            type="button"
            data-testid="spectator-reconnect"
            style={{ ...button, marginLeft: 12 }}
            onClick={() => setAttempt((value) => value + 1)}
          >
            Reconnect
          </button>
        </div>
      )}

      <main style={gridStyle}>
        <section style={panel}>
          <div style={panelHeading}>Gaze — 1.5 s trail</div>
          <GazeTrail
            points={points}
            now={now()}
            screen={screen}
            screenshotUrl={screenshotUrl}
          />
          <div style={{ ...note, marginTop: 8 }}>
            The shopper&apos;s own window shows none of this.
          </div>
        </section>

        {/* Dimmed with the heatmap when the feed dies, so no figure on this
            page can be filmed looking current when it is not. */}
        <section style={{ display: "grid", gap: 14, opacity: stale ? 0.45 : 1 }}>
          <div style={panel}>
            <div style={panelHeading}>Session</div>
            <div style={statsRowStyle}>
              {/* Labelled with the kind the server says it counted, so a
                  cursor_only session's dwells are never captioned as
                  fixations. Before the first frame the mode is unknown, and
                  the stat is empty anyway. */}
              <Stat
                testId="stat-evidence"
                label={
                  update === null ? "evidence" : EVIDENCE_LABEL[update.evidence_kind]
                }
                value={update?.evidence_count}
              />
              <Stat
                testId="stat-stations-visited"
                label="stations"
                value={update?.stations_visited}
              />
              <Stat
                testId="stat-elapsed"
                label="elapsed"
                text={update === null ? undefined : formatElapsed(update.t_ms)}
              />
            </div>
          </div>

          <AgreementMeter
            spearman={update?.spearman ?? null}
            meaningful={update?.meaningful ?? false}
            evidenceCount={update?.evidence_count ?? 0}
            evidenceKind={update?.evidence_kind ?? null}
            ceiling={ceiling}
          />
        </section>

        <section style={{ gridColumn: "1 / -1" }}>
          <LiveHeatmap
            attention={update?.attention ?? {}}
            locked={lock.population_fixation_prob}
            stale={stale}
          />
        </section>
      </main>
    </div>
  );
}

function Stat({
  testId,
  label,
  value,
  text,
}: {
  testId: string;
  label: string;
  value?: number;
  text?: string;
}) {
  const shown = text ?? (value === undefined ? "—" : String(value));
  return (
    <div>
      <div style={{ ...note, textTransform: "uppercase", letterSpacing: "0.08em" }}>
        {label}
      </div>
      <div data-testid={testId} style={{ ...bigNumber, color: INK }}>
        {shown}
      </div>
    </div>
  );
}

const headerStyle: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: 16,
  alignItems: "flex-start",
  justifyContent: "space-between",
  marginBottom: 14,
};

const gridStyle: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "minmax(320px, 1.15fr) minmax(280px, 1fr)",
  gap: 14,
};

const statsRowStyle: CSSProperties = {
  display: "flex",
  gap: 28,
  flexWrap: "wrap",
};

const alertPanelStyle: CSSProperties = {
  margin: "0 0 14px",
  padding: "10px 14px",
  borderRadius: 8,
  border: `1px solid ${ALERT}`,
  background: "#2a1512",
  color: INK,
  fontSize: 14,
};

/**
 * Not the red alert panel: nothing is wrong. It says the screen is following
 * rather than obeying, which a viewer has to be able to see - a session id in
 * the header is otherwise indistinguishable from one somebody asked for.
 */
const followedPanelStyle: CSSProperties = {
  margin: "0 0 14px",
  padding: "10px 14px",
  borderRadius: 8,
  border: `1px solid ${PANEL_BORDER}`,
  background: PANEL_BG,
  color: INK,
  fontSize: 14,
};

const fakeBannerStyle: CSSProperties = {
  ...hazard,
  // Sticky, so it cannot be scrolled out of a screenshot or a recording: the
  // page is scrollable and a synthetic frame must never be capturable without
  // the word FAKE in it.
  position: "sticky",
  top: 0,
  zIndex: 5,
  display: "flex",
  justifyContent: "center",
  padding: 6,
  marginBottom: 14,
  borderRadius: 8,
};

const fakeBannerTextStyle: CSSProperties = {
  padding: "6px 18px",
  borderRadius: 6,
  background: "#171208",
  color: FAKE,
  fontWeight: 800,
  fontSize: 20,
  letterSpacing: "0.08em",
  textAlign: "center",
};

function statusStyle(status: SpectatorStatus): CSSProperties {
  const colour = status === "live" ? REAL : status === "connecting" ? GREY : ALERT;
  return {
    display: "inline-block",
    marginTop: 8,
    padding: "3px 12px",
    borderRadius: 999,
    border: `2px solid ${colour}`,
    color: colour,
    fontWeight: 700,
    fontSize: 13,
    letterSpacing: "0.12em",
    background: PANEL_BORDER,
  };
}
