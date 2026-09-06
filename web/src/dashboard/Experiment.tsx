import { useEffect, useState } from "react";
import type { CSSProperties } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { toChartRows } from "@/dashboard/chartRows";
import {
  NOT_APPLICABLE,
  formatMetric,
  type ExperimentResult,
} from "@/dashboard/experimentResult";
import {
  buildReportHtml,
  buildReportJson,
  fetchPredictionLock,
  reportFilename,
} from "@/dashboard/report";
import {
  GREY,
  INK,
  PANEL_BG,
  PANEL_BORDER,
  REAL,
  SYNTH,
  absent,
  alertPanel,
  bigNumber,
  mono,
  note,
  panel,
  panelHeading,
  root,
} from "@/dashboard/styles";

/**
 * Re-exported from `@/dashboard/experimentResult`, which is where the response
 * shape and the "a figure that is not a number is never printed as one" rule
 * now live: the exported session report applies the identical rule, and the
 * printed document and the screen must not be able to disagree about what a
 * figure is.
 */
export { NOT_APPLICABLE, formatMetric };
export type { ExperimentResult };

/** The API serves SPEC's root paths; the vite dev proxy strips this prefix. */
const API_BASE = "/api";

type LoadState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; result: ExperimentResult };

interface RequestParams {
  experimentId: string | null;
  sessionId: string | null;
  variantId: string | null;
}

function paramsFromLocation(): RequestParams {
  const params = new URLSearchParams(window.location.search);
  return {
    experimentId: params.get("experiment"),
    sessionId: params.get("session"),
    variantId: params.get("variant"),
  };
}

async function failure(method: string, path: string, res: Response): Promise<Error> {
  const body = await res.text().catch(() => "");
  const detail = body.trim().length > 0 ? ` — ${body.trim().slice(0, 200)}` : "";
  return new Error(`${method} ${path} failed: ${res.status} ${res.statusText}${detail}`);
}

/**
 * `?experiment=<id>` fetches an already-run experiment. `?session=<id>&variant=<id>`
 * runs a new one first via POST, then returns it -- same response shape either way.
 */
async function fetchExperiment({
  experimentId,
  sessionId,
  variantId,
}: RequestParams): Promise<ExperimentResult> {
  if (experimentId) {
    const path = `/experiments/${encodeURIComponent(experimentId)}`;
    const res = await fetch(`${API_BASE}${path}`);
    if (!res.ok) throw await failure("GET", path, res);
    return (await res.json()) as ExperimentResult;
  }

  if (sessionId && variantId) {
    const path = "/experiments";
    const res = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ variant_id: variantId, session_id: sessionId }),
    });
    if (!res.ok) throw await failure("POST", path, res);
    return (await res.json()) as ExperimentResult;
  }

  throw new Error(
    "Experiment needs either ?experiment=<id> or ?session=<id>&variant=<id> in the URL.",
  );
}

function HeadlineFigure({
  testId,
  label,
  value,
  digits,
}: {
  testId: string;
  label: string;
  value: number;
  digits: number;
}) {
  const computed = Number.isFinite(value);
  return (
    <div style={figureStyle}>
      <div style={figureLabelStyle}>{label}</div>
      <div
        data-testid={testId}
        data-absent={String(!computed)}
        style={computed ? bigNumber : absentFigure}
      >
        {formatMetric(value, digits)}
      </div>
    </div>
  );
}

/**
 * Hand the browser a file it did not fetch.
 *
 * The report is generated here, in the page, out of data the page already has
 * -- there is no report endpoint and there is no server round trip -- so the
 * only way it reaches the operator is as an object URL the browser is told to
 * save. The URL is revoked on the next macrotask rather than on the next
 * statement: `click()` only *queues* the download, and revoking synchronously
 * has been observed to cancel it. Leaving it un-revoked would pin the whole
 * document in memory for the life of the tab.
 */
function download(filename: string, mime: string, contents: string): void {
  const url = URL.createObjectURL(new Blob([contents], { type: mime }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

/**
 * The two exports, side by side, above the numbers they describe.
 *
 * Both are built from the same `ReportInput` by `@/dashboard/report`, so the
 * printed evidence and the machine-readable evidence cannot disagree. The lock
 * is fetched at the moment of export rather than held in page state: it is the
 * one thing in the document that is not already on screen, it never changes
 * once written, and a lock that could not be read produces a report that says
 * so rather than an export that fails.
 */
function ExportRow({ result }: { result: ExperimentResult }) {
  async function exportReport(format: "html" | "json"): Promise<void> {
    const lock = await fetchPredictionLock(result.session_id);
    const input = { result, lock, generatedAt: new Date().toISOString() };
    if (format === "html") {
      download(
        reportFilename(result, "html"),
        "text/html;charset=utf-8",
        buildReportHtml(input),
      );
    } else {
      // Indented, and with the trailing newline every text file should have:
      // this one is meant to be opened and read as much as parsed.
      download(
        reportFilename(result, "json"),
        "application/json;charset=utf-8",
        JSON.stringify(buildReportJson(input), null, 2) + "\n",
      );
    }
  }

  return (
    <section style={panel}>
      <div style={panelHeading}>Session report</div>
      <div style={exportRowStyle}>
        <button
          type="button"
          data-testid="experiment-export-html"
          style={exportButtonStyle}
          onClick={() => {
            void exportReport("html");
          }}
        >
          Export report (HTML)
        </button>
        <button
          type="button"
          data-testid="experiment-export-json"
          style={exportButtonStyle}
          onClick={() => {
            void exportReport("json");
          }}
        >
          Export data (JSON)
        </button>
      </div>
      <div style={{ ...note, marginTop: 10 }}>
        A single self-contained file: the locked prediction hash and the time it was
        locked, the capture mode these figures were fused under, the per-slot comparison,
        and what the comparison cannot support. Print it to PDF from the browser.
      </div>
    </section>
  );
}

/**
 * The page's name, and the way back to `#/home`.
 *
 * CLAUDE.md keeps navigation chrome off the store and off the spectator screen,
 * because a person is being measured against one and the other is filmed beside
 * them. The dashboard is neither: it is read after a session has finished, by
 * the operator, and it is where a demo ends up. Without a link here the
 * launcher - the only page that says what the four screens are - was reachable
 * only by already knowing its URL.
 *
 * Rendered in all three of this component's states, not only the one that
 * loaded. The dashboard is the screen most often arrived at with the wrong URL:
 * it needs `?experiment=` or `?session=&variant=` and says so, and the launcher
 * is what turns the session the store just opened into one of those links. A
 * way out that appears only once the page has succeeded is a way out that is
 * never there when it is wanted.
 */
function TitleRow() {
  return (
    <div style={titleRowStyle}>
      <div style={panelHeading}>ShopperTwin dashboard</div>
      <a data-testid="experiment-home-link" style={homeLinkStyle} href="#/home">
        ← All screens
      </a>
    </div>
  );
}

/**
 * The S5 dashboard page: real vs synthetic per-slot attention, side by side,
 * plus the two headline numbers (Spearman, purchase-share MAE). Self-contained
 * and self-fetching -- it reads its own query params and owns its own
 * loading/error states, so it can be mounted anywhere without extra wiring.
 */
export default function Experiment() {
  const [state, setState] = useState<LoadState>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    setState({ status: "loading" });

    fetchExperiment(paramsFromLocation())
      .then((result) => {
        if (!cancelled) setState({ status: "ready", result });
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setState({
            status: "error",
            message: err instanceof Error ? err.message : String(err),
          });
        }
      });

    return () => {
      cancelled = true;
    };
  }, []);

  if (state.status === "loading") {
    return (
      <div style={root}>
        <header style={headerStyle}>
          <TitleRow />
        </header>
        <div style={panel} data-testid="experiment-loading">
          Loading experiment…
        </div>
      </div>
    );
  }

  if (state.status === "error") {
    return (
      <div style={root}>
        {/* The header comes with the error on purpose. The commonest failure
            here is `fetchExperiment`'s own refusal - "Experiment needs either
            ?experiment=<id> or ?session=<id>&variant=<id>" - and the launcher
            is the page that writes those URLs from the session the store just
            opened. A dead end is the one thing this screen must not be. */}
        <header style={headerStyle}>
          <TitleRow />
        </header>
        <div role="alert" style={alertPanel} data-testid="experiment-error">
          Could not load experiment: {state.message}
        </div>
      </div>
    );
  }

  const { result } = state;
  const rows = toChartRows(result.real_attention, result.synth_attention, result.slot_ids);

  return (
    <div style={root} data-testid="experiment-dashboard">
      <header style={headerStyle}>
        <TitleRow />
        <h1 style={headingStyle}>
          Experiment{" "}
          <span style={mono} data-testid="experiment-id">
            {result.experiment_id}
          </span>
        </h1>
      </header>

      <main style={mainStyle}>
        <section style={panel}>
          <div style={panelHeading}>Real vs synthetic</div>
          <div style={figuresRowStyle}>
            <HeadlineFigure
              testId="experiment-metric-attention-spearman"
              label="Attention Spearman (real vs synthetic)"
              value={result.attention_spearman}
              digits={3}
            />
            <HeadlineFigure
              testId="experiment-metric-purchase-share-mae"
              label="Purchase-share MAE (real vs synthetic)"
              value={result.purchase_share_mae}
              digits={4}
            />
          </div>
        </section>

        <section style={panel}>
          <div style={panelHeading}>Run details</div>
          <dl style={detailGridStyle}>
            <dt style={detailLabelStyle}>Variant</dt>
            <dd style={{ ...mono, ...detailValueStyle }}>{result.variant_id}</dd>
            <dt style={detailLabelStyle}>Session</dt>
            <dd
              style={{ ...mono, ...detailValueStyle }}
              data-testid="experiment-session-id"
            >
              {result.session_id}
            </dd>
            <dt style={detailLabelStyle}>Capture mode</dt>
            <dd
              style={{ ...mono, ...detailValueStyle }}
              data-testid="experiment-mode"
            >
              {/* Not decoration. This value selected the fusion weights for BOTH
                  panels, so every figure on this page is conditional on it -- and
                  a `cursor_only` session measured a mouse pointer, not gaze. */}
              {result.mode ?? <span style={absent}>{NOT_APPLICABLE}</span>}
            </dd>
            <dt style={detailLabelStyle}>Synthetic shoppers per persona</dt>
            <dd style={detailValueStyle}>{result.n_synth}</dd>
            <dt style={detailLabelStyle}>Seed</dt>
            <dd style={detailValueStyle}>{result.seed}</dd>
          </dl>
        </section>

        <ExportRow result={result} />

        <section style={panel}>
          <div style={panelHeading}>Attention by slot</div>
          <ResponsiveContainer width="100%" height={420}>
            <BarChart data={rows} margin={{ top: 8, right: 16, bottom: 48, left: 8 }}>
              <CartesianGrid stroke={PANEL_BORDER} strokeDasharray="3 3" />
              <XAxis
                dataKey="slot_id"
                angle={-45}
                textAnchor="end"
                interval={0}
                height={60}
                stroke={GREY}
                tick={{ fontSize: 12, fill: GREY }}
              />
              <YAxis domain={[0, "auto"]} stroke={GREY} tick={{ fontSize: 12, fill: GREY }} />
              <Tooltip
                contentStyle={{ background: PANEL_BG, border: `1px solid ${PANEL_BORDER}` }}
                labelStyle={{ color: INK }}
                itemStyle={{ color: INK }}
              />
              <Legend wrapperStyle={{ color: INK }} />
              <Bar dataKey="real" name="Real attention" fill={REAL} />
              <Bar dataKey="synth" name="Synthetic attention" fill={SYNTH} />
            </BarChart>
          </ResponsiveContainer>
          <div style={{ ...note, marginTop: 8 }}>
            <span data-testid="experiment-legend-real" style={{ color: REAL }}>
              ▮
            </span>{" "}
            real attention &nbsp;&nbsp;
            <span data-testid="experiment-legend-synth" style={{ color: SYNTH }}>
              ▮
            </span>{" "}
            synthetic attention
          </div>
        </section>
      </main>
    </div>
  );
}

const headerStyle: CSSProperties = {
  marginBottom: 14,
};

const titleRowStyle: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: 12,
  alignItems: "baseline",
};

/**
 * Quiet on purpose. This screen reads out how close synthetic came to real, and
 * `dashboard/styles.ts` spends its colours saying which of the two a figure is
 * - real in blue, synthetic in amber, grey for a figure that does not exist. A
 * navigation link takes none of them: it borrows the panel border, so it cannot
 * be mistaken on camera for something that was measured.
 */
const homeLinkStyle: CSSProperties = {
  padding: "4px 12px",
  borderRadius: 999,
  border: `1px solid ${PANEL_BORDER}`,
  color: INK,
  fontSize: 12,
  fontWeight: 600,
  letterSpacing: "0.06em",
  textDecoration: "none",
  whiteSpace: "nowrap",
};

const headingStyle: CSSProperties = {
  margin: "4px 0 0",
  fontSize: 22,
  fontWeight: 700,
};

const mainStyle: CSSProperties = {
  display: "grid",
  gap: 14,
};

const figuresRowStyle: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: 28,
};

const figureStyle: CSSProperties = {
  minWidth: 220,
};

const figureLabelStyle: CSSProperties = {
  ...note,
  textTransform: "uppercase",
  letterSpacing: "0.08em",
};

/** Mirrors whatif/LiftBars.tsx's `absentFigure`: the same size role as `bigNumber`, absent. */
const absentFigure: CSSProperties = {
  ...absent,
  fontSize: 22,
  lineHeight: 1.4,
};

const exportRowStyle: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: 10,
};

/**
 * Borrowed from `homeLinkStyle` for the same reason: `dashboard/styles.ts`
 * spends its colours saying whether a figure is real, synthetic or absent, and
 * a control that produces a document is none of those. It must not be mistaken
 * on camera for something that was measured.
 */
const exportButtonStyle: CSSProperties = {
  padding: "7px 14px",
  borderRadius: 8,
  border: `1px solid ${PANEL_BORDER}`,
  background: "transparent",
  color: INK,
  fontFamily: "inherit",
  fontSize: 13,
  fontWeight: 600,
  letterSpacing: "0.04em",
  cursor: "pointer",
};

const detailGridStyle: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "max-content 1fr",
  columnGap: 14,
  rowGap: 8,
  margin: 0,
};

const detailLabelStyle: CSSProperties = {
  ...note,
  textTransform: "uppercase",
  letterSpacing: "0.06em",
  alignSelf: "center",
};

const detailValueStyle: CSSProperties = {
  margin: 0,
  fontSize: 14,
  alignSelf: "center",
};
