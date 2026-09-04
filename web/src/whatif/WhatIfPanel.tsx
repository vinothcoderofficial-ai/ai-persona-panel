import { useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";
import type { Planogram } from "@/contracts/planogram.schema";
import { getResolvedVariant } from "@/api/client";
import { HeatmapDiff, ANIMATION_MS } from "@/whatif/HeatmapDiff";
import { LiftBars } from "@/whatif/LiftBars";
import { WhatIfControls } from "@/whatif/WhatIfControls";
import { postWhatIf, type RunWhatIf, type WhatIfResponse } from "@/whatif/client";
import {
  createDebouncer,
  timerSchedule,
  DEBOUNCE_MS,
  type Debouncer,
  type Schedule,
} from "@/whatif/debounce";
import { personaLiftRows } from "@/whatif/lift";
import {
  DEFAULT_N_SYNTH,
  EMPTY_SELECTION,
  focalSlots,
  toRequestBody,
  type FocalSlots,
  type WhatIfRequestBody,
  type WhatIfSelection,
} from "@/whatif/patches";
import {
  ALERT,
  GREY,
  INK,
  PANEL_BORDER,
  alertPanel,
  bigNumber,
  mono,
  note,
  panel,
  panelHeading,
  root,
} from "@/whatif/styles";

/**
 * The S8 what-if page: change one thing about the shelf and watch 10,000
 * synthetic shoppers per persona re-run against it.
 *
 * How it hangs together
 * ---------------------
 * `WhatIfControls` owns no state; this component holds the selection,
 * `patches.ts` turns it into the exact request body, and `POST /whatif` does
 * every piece of thinking. Nothing here resolves a planogram or computes an
 * attention formula - `resolve()` lives only in `api/app/resolve.py` and the
 * simulator only in `sim/`.
 *
 * The opening request is the baseline
 * -----------------------------------
 * On load the page posts `patches: []`, which the endpoint treats as the
 * exactly-neutral baseline and answers from its cache. That response is kept:
 * it is what the per-persona bars are measured against, since a what-if
 * response only ever carries one run's `per_persona`. It is fetched
 * unconditionally and undebounced, because a viewer who touches a dropdown
 * within the first 300 ms must not end up with no baseline at all.
 *
 * Every later change is debounced by 300 ms (SPEC M9) and skipped entirely when
 * it would repeat the request already on screen.
 *
 * Every figure describes one run
 * ------------------------------
 * The focal SKU's slots are captured *with* the request that used them, not
 * read back off the live selection. Between choosing "eye level" and the
 * debounced call landing, the selection already says eye level while the
 * numbers on screen are still the run before it - and a lift computed across
 * that seam would be a figure describing no run that was ever performed.
 *
 * What is never done
 * ------------------
 * No spinner. Warm p50 is about 9 ms because the endpoint shares a simulation
 * cache with the prediction lock, so a spinner would be a flash of nothing.
 * What is shown instead is `elapsed_ms` - labelled as server compute, not the
 * round trip - and, when a call fails, its message with the figures explicitly
 * marked stale and a way to run it again. Numbers left looking current after
 * the API died would be the same lie as a fabricated 0%.
 */

/**
 * The variant the page opens. It must be **patch-free**: `POST /whatif` applies
 * patches to the base planogram, so the layout these controls reason about has
 * to be that same base. `data/variants/A.json` is `"patches": []` for exactly
 * this reason, and it is the resolved form of `demo_aisle`.
 */
export const BASELINE_VARIANT_ID = "A";

export interface WhatIfPanelProps {
  variantId?: string;
  /** Supplied directly in tests; otherwise fetched from the variant above. */
  planogram?: Planogram;
  loadPlanogram?: (variantId: string) => Promise<Planogram>;
  runWhatIf?: RunWhatIf;
  nSynth?: number;
  seed?: number;
  debounceMs?: number;
  /** The debounce timer, injectable so tests need no real waiting. */
  schedule?: Schedule;
  animationMs?: number;
  reducedMotion?: boolean;
}

/** Only about `GET /variants/{id}/resolved`; "idle" means it was handed in. */
type FetchState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; planogram: Planogram };

/** What the focal SKU was, for the request whose answer is being drawn. */
interface RunContext {
  slots: FocalSlots;
  focalSkuId: string | null;
}

const NO_FOCAL: RunContext = { slots: { baseline: null, patched: null }, focalSkuId: null };

/** One completed run, with the question it answered. */
interface Shown {
  response: WhatIfResponse;
  context: RunContext;
}

interface Frames {
  previous: Record<string, number>;
  next: Record<string, number>;
}

const NO_FRAMES: Frames = { previous: {}, next: {} };

export function WhatIfPanel(props: WhatIfPanelProps) {
  const variantId = props.variantId ?? BASELINE_VARIANT_ID;
  const nSynth = props.nSynth;
  const seed = props.seed;
  const given = props.planogram;

  const [fetched, setFetched] = useState<FetchState>(
    given === undefined ? { status: "loading" } : { status: "idle" },
  );
  const [selection, setSelection] = useState<WhatIfSelection>(EMPTY_SELECTION);
  const [baseline, setBaseline] = useState<WhatIfResponse | null>(null);
  const [shown, setShown] = useState<Shown | null>(null);
  const [frames, setFrames] = useState<Frames>(NO_FRAMES);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  // Inline arrows from a caller must not restart anything, so the injected
  // pieces live in refs - the same shape SpectatorView uses for its socket
  // factory and lock fetch.
  const runWhatIfRef = useRef(props.runWhatIf);
  runWhatIfRef.current = props.runWhatIf;
  const loadPlanogramRef = useRef(props.loadPlanogram);
  loadPlanogramRef.current = props.loadPlanogram;

  const debouncerRef = useRef<Debouncer | null>(null);
  if (debouncerRef.current === null) {
    debouncerRef.current = createDebouncer(
      props.debounceMs ?? DEBOUNCE_MS,
      props.schedule ?? timerSchedule,
    );
  }
  const debouncer = debouncerRef.current;

  /** The body of the request whose answer is on screen, so it is not re-sent. */
  const sent = useRef<string | null>(null);
  /** Only the newest request may write to the screen. */
  const generation = useRef(0);

  // A handed-in planogram wins, so the prop cannot go stale behind state.
  const planogram = given ?? (fetched.status === "ready" ? fetched.planogram : null);

  useEffect(() => {
    if (given !== undefined) return undefined;
    const load = loadPlanogramRef.current ?? getResolvedVariant;
    let cancelled = false;
    void load(variantId).then(
      (loaded) => {
        if (!cancelled) setFetched({ status: "ready", planogram: loaded });
      },
      (reason: unknown) => {
        if (cancelled) return;
        setFetched({
          status: "error",
          message: reason instanceof Error ? reason.message : String(reason),
        });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [variantId, given, attempt]);

  const runRef = useRef<
    ((body: WhatIfRequestBody, context: RunContext, isBaseline: boolean) => void) | null
  >(null);
  if (runRef.current === null) {
    runRef.current = (body, context, isBaseline) => {
      const call = runWhatIfRef.current ?? postWhatIf;
      generation.current += 1;
      const mine = generation.current;
      void call(body).then(
        (response) => {
          // The baseline is a different quantity from what is on screen, and
          // the per-persona bars are useless without it, so it is kept even if
          // a later run has already overtaken it.
          if (isBaseline) setBaseline(response);
          if (generation.current !== mine) return;
          setError(null);
          setShown({ response, context });
          setFrames((current) => ({
            previous: current.next,
            next: response.population_fixation_prob,
          }));
        },
        (reason: unknown) => {
          if (generation.current !== mine) return;
          setError(reason instanceof Error ? reason.message : String(reason));
        },
      );
    };
  }
  const run = runRef.current;

  // The baseline: one undebounced request, before any dropdown can be touched.
  useEffect(() => {
    if (planogram === null) return;
    const body = toRequestBody(planogram, EMPTY_SELECTION, { nSynth, seed });
    sent.current = JSON.stringify(body);
    run(body, NO_FOCAL, true);
  }, [planogram, nSynth, seed, run, attempt]);

  const body = useMemo(
    () => (planogram === null ? null : toRequestBody(planogram, selection, { nSynth, seed })),
    [planogram, selection, nSynth, seed],
  );
  const bodyKey = body === null ? null : JSON.stringify(body);
  const slots = useMemo(
    () => (planogram === null ? NO_FOCAL.slots : focalSlots(planogram, selection)),
    [planogram, selection],
  );

  // SPEC M9's 300 ms. Each change replaces the pending call, so a burst through
  // a dropdown is one simulation carrying the last value.
  useEffect(() => {
    if (body === null || bodyKey === null) return undefined;
    // The opening selection is the baseline request, already in flight; and a
    // selection changed back to what is on screen needs no re-run.
    if (bodyKey === sent.current) return undefined;
    const context: RunContext = {
      slots,
      focalSkuId: slots.baseline === null ? null : selection.focalSkuId,
    };
    debouncer.schedule(() => {
      sent.current = bodyKey;
      run(body, context, false);
    });
    return () => debouncer.cancel();
  }, [body, bodyKey, slots, selection.focalSkuId, debouncer, run]);

  const personaRows = useMemo(
    () =>
      baseline === null || shown === null
        ? []
        : personaLiftRows(baseline.per_persona, shown.response.per_persona, shown.context.slots),
    [baseline, shown],
  );

  // A page that cannot reach the API says so. Leaving the previous run's
  // figures looking current would be the same kind of lie as a fabricated 0%.
  const loadFailure = fetched.status === "error" ? fetched.message : null;
  const failure = error ?? loadFailure;
  const stale = failure !== null;

  const retry = () => {
    // Clearing this is what lets the same selection be asked for twice: it is
    // otherwise suppressed as "already on screen", which it is not.
    sent.current = null;
    setError(null);
    if (fetched.status === "error") setFetched({ status: "loading" });
    setAttempt((value) => value + 1);
  };

  return (
    <div style={root} data-testid="whatif-panel" data-stale={String(stale)}>
      <header style={headerStyle}>
        <div>
          <div style={panelHeading}>ShopperTwin what-if</div>
          <div style={{ ...mono, fontSize: 13, color: GREY }}>
            base {planogram?.planogram_id ?? "—"} · via variant {variantId} (no patches)
          </div>
        </div>
        <Elapsed response={shown?.response ?? null} nSynth={nSynth} />
      </header>

      {failure !== null && (
        <div role="alert" data-testid="whatif-error" style={alertPanel}>
          <strong>
            {loadFailure !== null
              ? "The base planogram could not be loaded."
              : "POST /whatif did not answer."}
          </strong>{" "}
          {failure}
          <button type="button" data-testid="whatif-retry" style={buttonStyle} onClick={retry}>
            Run it again
          </button>
          {shown !== null && (
            <div style={{ ...note, marginTop: 6, color: ALERT }}>
              Everything below is the last run that succeeded, not this one.
            </div>
          )}
        </div>
      )}

      {planogram === null && loadFailure === null && (
        <div style={panel} data-testid="whatif-loading">
          Loading the base planogram from{" "}
          <code style={mono}>GET /variants/{variantId}/resolved</code>…
        </div>
      )}

      {planogram !== null && (
        <main style={{ display: "grid", gap: 14, opacity: stale ? 0.45 : 1 }}>
          <WhatIfControls planogram={planogram} selection={selection} onChange={setSelection} />
          <div style={gridStyle}>
            <HeatmapDiff
              previous={frames.previous}
              next={frames.next}
              durationMs={props.animationMs ?? ANIMATION_MS}
              reducedMotion={props.reducedMotion}
            />
            <LiftBars
              lift={shown?.response.lift_vs_baseline ?? {}}
              rows={personaRows}
              focalSkuId={shown?.context.focalSkuId ?? null}
            />
          </div>
        </main>
      )}
    </div>
  );
}

function Elapsed({ response, nSynth }: { response: WhatIfResponse | null; nSynth?: number }) {
  const shoppers = (nSynth ?? DEFAULT_N_SYNTH).toLocaleString("en-GB");
  return (
    <div style={elapsedStyle}>
      <div style={{ ...note, textTransform: "uppercase", letterSpacing: "0.08em" }}>
        elapsed_ms
      </div>
      <div data-testid="whatif-elapsed" style={{ ...bigNumber, color: INK }}>
        {response === null ? "—" : `${response.elapsed_ms} ms`}
      </div>
      <div data-testid="whatif-elapsed-note" style={{ ...note, maxWidth: 320 }}>
        Server-side compute inside <code style={mono}>POST /whatif</code> — not the browser
        round trip. {shoppers} synthetic shoppers per persona, four personas.
      </div>
      <div data-testid="whatif-sim-run-id" style={{ ...mono, ...note }}>
        {response === null ? "" : `sim_run_id ${response.sim_run_id}`}
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

const elapsedStyle: CSSProperties = {
  padding: "10px 14px",
  borderRadius: 10,
  border: `1px solid ${PANEL_BORDER}`,
  textAlign: "right",
};

const gridStyle: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "minmax(320px, 1.2fr) minmax(320px, 1fr)",
  gap: 14,
  alignItems: "start",
};

const buttonStyle: CSSProperties = {
  marginLeft: 12,
  padding: "6px 14px",
  borderRadius: 7,
  border: `1px solid ${ALERT}`,
  background: "transparent",
  color: INK,
  fontFamily: "inherit",
  fontSize: 13,
  cursor: "pointer",
};
