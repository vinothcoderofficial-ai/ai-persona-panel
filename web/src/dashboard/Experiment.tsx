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

/** The API serves SPEC's root paths; the vite dev proxy strips this prefix. */
const API_BASE = "/api";

/**
 * POST /experiments' response shape (S5 task brief, decision 4). This
 * deliberately does NOT satisfy schemas/metrics.schema.json -- that schema is
 * the full cross-variant evaluation (noise ceiling, decision agreement,
 * holdout variants) that is S17-S19's job, so there is no generated contract
 * type for it yet. This interface is this page's own honest description of
 * what the endpoint actually returns today.
 */
interface ExperimentResult {
  experiment_id: string;
  variant_id: string;
  session_id: string;
  n_synth: number;
  seed: number;
  slot_ids: string[];
  real_attention: Record<string, number>;
  synth_attention: Record<string, number>;
  attention_spearman: number;
  purchase_share_mae: number;
  real_purchase_share: Record<string, number>;
  synth_purchase_share: Record<string, number>;
}

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

/** The words shown wherever a headline metric could not be computed. */
export const NOT_APPLICABLE = "not applicable";

/**
 * A headline metric as fixed-precision text, or `NOT_APPLICABLE` when there
 * is no real number behind it.
 *
 * `ExperimentResult.attention_spearman` and `.purchase_share_mae` are typed
 * above as required numbers, and in practice they always are one:
 * `analytics/metrics.py` guards both against `NaN` and returns 0.0 rather
 * than an undefined ratio (see that module's docstrings), so nothing in this
 * endpoint's own maths ever produces a missing value. But `ExperimentResult`
 * is -- per its docstring -- this page's own honest description of the
 * endpoint, not a generated, checked contract: `(await res.json()) as
 * ExperimentResult` is a type assertion, not a validation, and an
 * `ExperimentRecord` persisted before one of these fields existed would come
 * back over the wire without it. Calling `.toFixed()` on that `undefined`
 * would crash the page.
 *
 * So this applies the same rule `web/src/whatif/lift.ts:formatLift` uses for
 * the what-if panel's own figures: only a finite number is a figure, and
 * anything else -- missing, `null`, `NaN` -- is "not applicable", never a
 * fabricated 0. A computed 0 (e.g. no rank correlation at all) is a real
 * result and is shown as one, not caught by the same net.
 */
export function formatMetric(value: number, digits: number): string {
  return Number.isFinite(value) ? value.toFixed(digits) : NOT_APPLICABLE;
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
        <div style={panel} data-testid="experiment-loading">
          Loading experiment…
        </div>
      </div>
    );
  }

  if (state.status === "error") {
    return (
      <div style={root}>
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
        <div style={panelHeading}>ShopperTwin dashboard</div>
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
            <dt style={detailLabelStyle}>Synthetic shoppers per persona</dt>
            <dd style={detailValueStyle}>{result.n_synth}</dd>
            <dt style={detailLabelStyle}>Seed</dt>
            <dd style={detailValueStyle}>{result.seed}</dd>
          </dl>
        </section>

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
