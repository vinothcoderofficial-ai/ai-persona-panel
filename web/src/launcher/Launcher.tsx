import type { CSSProperties, ReactNode } from "react";
import { readLastSession, type LastSession } from "@/session/lastSession";

/**
 * `#/home` - the operator's launcher.
 *
 * There are four screens and, before this, the only way to reach any of them
 * was to type a URL. The dashboard's needed a raw uuid, typed by hand, on
 * camera. That was the weakest part of the demo, and this page is the fix.
 *
 * It is an **operator** screen, and only that. CLAUDE.md keeps navigation
 * chrome off the two screens that are being looked at for real - the store,
 * because a shopper is being measured against it and anything else on it is one
 * more thing to look at that is not a product, and the spectator, because it is
 * a dedicated second monitor that gets filmed. So the links live here instead,
 * on a page nobody is measured on.
 *
 * `#/home` is deliberately an *additional* route and never the default one:
 * `scripts/collect_link.py` hands participants `https://host/?variant=X` with
 * no fragment at all, so a bare URL has to stay the store.
 *
 * The variant blurbs below were read from `data/variants/*.json` and
 * `data/planograms/demo_aisle.json`, not remembered.
 * `web/tests/launcher.test.tsx` re-reads those documents and asserts the names
 * still match: a launcher that described D as anything but the no-creative
 * control arm would send an operator to collect the wrong arm of the
 * between-variant Brand Lift, and nothing downstream would notice.
 *
 * Inline styles, like the rest of this app - there is no CSS pipeline. The
 * palette matches the spectator and what-if screens on purpose, so the windows
 * look like one product on a recording, but it is deliberately its own copy:
 * nothing here imports from `src/spectator/`, which is what keeps this module
 * out of the import graph `web/tests/spectatorIsolation.test.ts` guards.
 */

const INK = "#e8eaed";
const BACKDROP = "#12151b";
const PANEL_BG = "#1c2129";
const PANEL_BORDER = "#2b323d";
const ACCENT = "#4f8cff";
/** "This is not the ordinary path" - the rehearsal shortcut and its warning. */
const CAUTION = "#f59e0b";
const GREY = "#7a828f";

const rootStyle: CSSProperties = {
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

const panel: CSSProperties = {
  background: PANEL_BG,
  border: `1px solid ${PANEL_BORDER}`,
  borderRadius: 10,
  padding: 16,
};

const panelHeading: CSSProperties = {
  margin: "0 0 10px",
  fontSize: 13,
  fontWeight: 700,
  letterSpacing: "0.09em",
  textTransform: "uppercase",
  opacity: 0.72,
};

const note: CSSProperties = {
  fontSize: 13,
  opacity: 0.78,
};

const mono: CSSProperties = {
  fontFamily: "ui-monospace, SFMono-Regular, Consolas, Menlo, monospace",
};

const variantRow: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: 14,
  justifyContent: "space-between",
  alignItems: "flex-start",
  padding: "10px 12px",
  borderRadius: 8,
  border: `1px solid ${PANEL_BORDER}`,
  background: "#171c24",
};

const linkButton: CSSProperties = {
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

const cautionLink: CSSProperties = {
  ...linkButton,
  border: `1px dashed ${CAUTION}`,
  background: "transparent",
  color: GREY,
  fontWeight: 400,
};

const gridStyle: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))",
  gap: 14,
};

/** One arm of the experiment, described from its own document. */
interface VariantCard {
  id: string;
  /** Verbatim `name` from `data/variants/<id>.json`. */
  name: string;
  what: ReactNode;
}

const VARIANTS: VariantCard[] = [
  {
    id: "A",
    name: "Baseline",
    what: (
      <>
        The seed planogram with <code style={mono}>&quot;patches&quot;: []</code> — nothing
        moved. <code style={mono}>AD_1</code> (Crunch) sits on{" "}
        <code style={mono}>B3_ENDCAP</code>, the bay 3 endcap header, exactly where{" "}
        <code style={mono}>demo_aisle</code> puts it.
      </>
    ),
  },
  {
    id: "B",
    name: "Focal SKU moved to eye level (known effect)",
    what: (
      <>
        One <code style={mono}>move_sku</code> patch: <code style={mono}>SKU_008</code>{" "}
        (Orchid Nuts 120g) leaves <code style={mono}>B1S5P1</code> on the bottom shelf for{" "}
        <code style={mono}>B1S3P2</code> at eye level. The advertising is untouched. This is
        the known-effect arm: an instrument that cannot see this is not measuring anything.
      </>
    ),
  },
  {
    id: "C",
    name: "Ad creative moved to the bay 1 shelf talker",
    what: (
      <>
        Two <code style={mono}>set_ad_creative</code> patches:{" "}
        <code style={mono}>B3_ENDCAP</code> is cleared and <code style={mono}>AD_1</code>{" "}
        appears on <code style={mono}>B1_TALKER</code>, the shelf talker attached to B1S3.
        Same creative, same shelves, different position.
      </>
    ),
  },
  {
    id: "D",
    name: "Control arm - no ad creative anywhere",
    what: (
      <>
        Three <code style={mono}>set_ad_creative</code> patches, one per ad slot:{" "}
        <code style={mono}>B1_TALKER</code>, <code style={mono}>B2_DECAL</code> and{" "}
        <code style={mono}>B3_ENDCAP</code> all set to <code style={mono}>null</code>. It is
        the only arm carrying no creative at all, which is what makes a between-variant
        Brand Lift measurable — every other arm is read against this one.
      </>
    ),
  },
];

export interface LauncherProps {
  /** Injected in tests; otherwise the note the store left in localStorage. */
  readStoredSession?: () => LastSession | null;
}

export function Launcher({ readStoredSession = readLastSession }: LauncherProps) {
  const stored = readStoredSession();

  return (
    <div data-testid="launcher" style={rootStyle}>
      <header style={{ marginBottom: 18 }}>
        <div style={{ fontSize: 26, fontWeight: 700, letterSpacing: "-0.01em" }}>
          ShopperTwin
        </div>
        <div style={{ ...note, marginTop: 4, maxWidth: 720 }}>
          Operator launcher. Everything the demo needs, without a typed URL. Nobody is
          measured on this page — it is neither the shopper&apos;s screen nor the
          spectator&apos;s.
        </div>
      </header>

      {stored === null ? (
        <div data-testid="launcher-no-last-session" style={{ ...panel, marginBottom: 16 }}>
          <div style={panelHeading}>Last session</div>
          <div style={note}>
            No session has been opened in this browser yet. Open the store below and this
            box will carry its id, ready for the second monitor.
          </div>
        </div>
      ) : (
        <div data-testid="launcher-last-session" style={{ ...panel, marginBottom: 16 }}>
          <div style={panelHeading}>Last session opened in this browser</div>
          <div style={{ ...mono, fontSize: 15, wordBreak: "break-all" }}>
            {stored.session_id}
          </div>
          <div style={{ ...note, marginTop: 4 }}>
            variant {stored.variant_id} · started {stored.started_at}
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 10, marginTop: 12 }}>
            {/* The id is written into the link rather than left to the
                fallback: a URL that names the session it is watching survives
                being copied into another window, and is what should be on
                camera. */}
            <a
              data-testid="launcher-last-spectator"
              style={linkButton}
              href={`#/spectator?session=${encodeURIComponent(stored.session_id)}`}
            >
              Watch it on the spectator screen
            </a>
            {/* Experiment reads location.search and never the hash, so these
                ids go before the `#`. */}
            <a
              data-testid="launcher-last-dashboard"
              style={linkButton}
              href={
                `/?session=${encodeURIComponent(stored.session_id)}` +
                `&variant=${encodeURIComponent(stored.variant_id)}#/dashboard`
              }
            >
              Score it on the dashboard
            </a>
          </div>
        </div>
      )}

      <div data-testid="launcher-store" style={{ ...panel, marginBottom: 14 }}>
        <div style={panelHeading}>Store — the shopper&apos;s screen</div>
        <div style={{ ...note, marginBottom: 12, maxWidth: 760 }}>
          Consent, camera check, calibration and validation, then the 3D aisle with a fixed
          camera per bay. Opening one of these creates a session, and the server writes its
          prediction lock before it will accept a single event — so open the arm you
          actually mean to collect.
        </div>

        <div style={{ display: "grid", gap: 10 }}>
          {VARIANTS.map((variant) => (
            <div
              key={variant.id}
              data-testid={`launcher-variant-${variant.id}`}
              style={variantRow}
            >
              <div style={{ minWidth: 0, flex: "1 1 340px" }}>
                <div style={{ fontWeight: 600 }}>
                  <span style={{ ...mono, color: ACCENT }}>{variant.id}</span> —{" "}
                  {variant.name}
                </div>
                <div style={{ ...note, marginTop: 4 }}>{variant.what}</div>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                <a
                  data-testid={`launcher-open-${variant.id}`}
                  style={linkButton}
                  href={`/?variant=${variant.id}`}
                >
                  Open store
                </a>
                <a
                  data-testid={`launcher-rehearse-${variant.id}`}
                  style={cautionLink}
                  href={`/?variant=${variant.id}&skip_capture=1`}
                >
                  Skip the webcam setup
                </a>
              </div>
            </div>
          ))}
        </div>

        <div data-testid="launcher-rehearse-note" style={{ ...note, marginTop: 12 }}>
          <strong style={{ color: CAUTION }}>Skip the webcam setup</strong> is for rehearsal
          only. It goes straight to the shelves in <code style={mono}>cursor_only</code>{" "}
          mode and records <code style={mono}>consent: false</code> — nobody sat down and
          agreed to anything — so SessionGate rejects that session as{" "}
          <code style={mono}>no_consent</code> and it can never enter the real panel.
        </div>
      </div>

      <div style={gridStyle}>
        <Destination
          testId="launcher-whatif"
          linkTestId="launcher-whatif-link"
          href="#/whatif"
          title="What-if"
          action="Open the what-if panel"
        >
          Move one thing about the shelf — a SKU to another slot, a creative to another ad
          slot — and re-run 10,000 synthetic shoppers per persona against it. It creates no
          session and measures nobody.
        </Destination>

        <Destination
          testId="launcher-spectator"
          linkTestId="launcher-spectator-link"
          href="#/spectator"
          title="Spectator"
          action="Open the spectator screen"
        >
          The second monitor, in its own window. Gaze trail, live attention beside the
          locked prediction, agreement meter, prediction badge, clock. Never open it on the
          shopper&apos;s own screen: people stare at their gaze dot, and then the data is
          about the dot rather than the shelf.
        </Destination>

        <Destination
          testId="launcher-dashboard"
          linkTestId="launcher-dashboard-link"
          href="#/dashboard"
          title="Dashboard"
          action="Open the dashboard"
        >
          Real against synthetic for one finished session: per-slot attention bars, the
          attention Spearman and the purchase-share MAE.
        </Destination>
      </div>
    </div>
  );
}

function Destination({
  testId,
  linkTestId,
  href,
  title,
  action,
  children,
}: {
  testId: string;
  linkTestId: string;
  href: string;
  title: string;
  action: string;
  children: ReactNode;
}) {
  return (
    <div data-testid={testId} style={{ ...panel, display: "flex", flexDirection: "column" }}>
      <div style={panelHeading}>{title}</div>
      <div style={{ ...note, flex: 1 }}>{children}</div>
      <div style={{ marginTop: 12 }}>
        <a data-testid={linkTestId} style={linkButton} href={href}>
          {action}
        </a>
      </div>
    </div>
  );
}
