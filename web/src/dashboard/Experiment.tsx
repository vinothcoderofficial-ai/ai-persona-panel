import { useEffect, useState } from "react";
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
    return <p>Loading experiment…</p>;
  }

  if (state.status === "error") {
    return <p role="alert">Could not load experiment: {state.message}</p>;
  }

  const { result } = state;
  const rows = toChartRows(result.real_attention, result.synth_attention, result.slot_ids);

  return (
    <section>
      <h1>Experiment {result.experiment_id}</h1>
      <dl>
        <dt>Variant</dt>
        <dd>{result.variant_id}</dd>
        <dt>Session</dt>
        <dd>{result.session_id}</dd>
        <dt>Synthetic shoppers per persona</dt>
        <dd>{result.n_synth}</dd>
        <dt>Seed</dt>
        <dd>{result.seed}</dd>
        <dt>Attention Spearman (real vs synthetic)</dt>
        <dd>{result.attention_spearman.toFixed(3)}</dd>
        <dt>Purchase-share MAE (real vs synthetic)</dt>
        <dd>{result.purchase_share_mae.toFixed(4)}</dd>
      </dl>
      <ResponsiveContainer width="100%" height={420}>
        <BarChart data={rows} margin={{ top: 8, right: 16, bottom: 48, left: 8 }}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="slot_id" angle={-45} textAnchor="end" interval={0} height={60} />
          <YAxis domain={[0, "auto"]} />
          <Tooltip />
          <Legend />
          <Bar dataKey="real" name="Real attention" fill="#2563eb" />
          <Bar dataKey="synth" name="Synthetic attention" fill="#f97316" />
        </BarChart>
      </ResponsiveContainer>
    </section>
  );
}
