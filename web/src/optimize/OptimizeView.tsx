import { useCallback, useEffect, useState } from "react";
import type { CSSProperties } from "react";
import { AiRequestError, type FetchLike } from "@/ai/client";
import * as style from "@/ai/styles";

/**
 * `#/optimize` — the recommendation, on a screen.
 *
 * `analytics/optimizer.py` has scored every placement, ranked them, named the
 * best and reported where the current one sits since S24, and
 * `analytics/slot_value.py` could price the difference. Neither had a route or
 * a screen, so the strongest claim this project makes — that it is a
 * recommendation engine rather than an A/B testing tool, because it models
 * purchase and not only attention — was invisible from the running product.
 *
 * The whole difficulty here is not showing a ranking; it is showing one without
 * over-claiming. A table of percentages sorted descending reads as certainty,
 * and at any run size the leaders routinely sit inside each other's Monte Carlo
 * spread. So this screen is built around four refusals, each of which the
 * server already reports and a careless table would drop:
 *
 *  1. **An undefined objective reads as undefined.** A creative taken down has
 *     no ad-to-purchase lift. Printing 0% would rank "no advertising" as a
 *     measured, mediocre option instead of an unanswerable question.
 *  2. **The range is a seed spread, and says so.** Not a confidence interval —
 *     `SeedSpread` and docs/METHODOLOGY.md §12.7 explain why there is no honest
 *     CI available — and it carries the seeds, because a range quoted without
 *     its count says nothing.
 *  3. **An unresolved winner is announced as unresolved.** If the top pick's
 *     spread overlaps another row's, the two are not ranked against each other
 *     and the screen says which rows, by name.
 *  4. **Skipped candidates are listed.** A shelf level a bay does not have is
 *     not a move that lost.
 *
 * The summary sentence is printed verbatim from `optimizer.summary()` rather
 * than composed here: the CLI, `RESULTS.md` and this screen have to say the
 * same thing, and three renderings of one result is how they stop.
 */

export interface OptimizeViewProps {
  fetchImpl?: FetchLike;
  /** Which arm to rank against. Defaults to the unpatched baseline. */
  variantId?: string;
  creativeId?: string;
}

const defaultFetch: FetchLike = (input, init) => fetch(input, init);

interface SeedSpread {
  seeds: number[];
  values: number[];
  low: number;
  high: number;
  n_seeds: number;
  width: number;
}

interface Entry {
  rank: number;
  candidate_id: string;
  kind: string;
  label: string;
  patches: Array<Record<string, unknown>>;
  /** Null when the metric does not exist for this configuration. Never 0. */
  objective: number | null;
  objective_text: string | null;
  is_current: boolean;
  seed_spread: SeedSpread | null;
  unresolved_against: string[];
}

interface Ranking {
  variant_id: string;
  creative_id: string;
  focal_sku_id: string | null;
  objective_name: string;
  n_synth: number;
  seed: number;
  spread_seeds: number[];
  elapsed_ms: number;
  n_candidates: number;
  current_rank: number | null;
  top_pick_is_resolved: boolean | null;
  beats_current: string[];
  summary_lines: string[];
  spread_caveat: string;
  skipped: Array<{ candidate_id: string; kind: string; reason: string }>;
  entries: Entry[];
}

type Load =
  | { status: "idle" }
  | { status: "running" }
  | { status: "error"; detail: string }
  | { status: "ready"; value: Ranking };

export function OptimizeView({
  fetchImpl = defaultFetch,
  variantId = "A",
  creativeId = "AD_1",
}: OptimizeViewProps) {
  const [state, setState] = useState<Load>({ status: "running" });
  const [focalSku, setFocalSku] = useState<string>("");

  const run = useCallback(async () => {
    setState({ status: "running" });
    try {
      const response = await fetchImpl("/api/optimize", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          creative_id: creativeId,
          variant_id: variantId,
          focal_sku_id: focalSku === "" ? null : focalSku,
        }),
      });
      if (!response.ok) {
        const raw = await response.text().catch(() => "");
        let detail = `${response.status} ${response.statusText}`;
        try {
          const parsed = JSON.parse(raw) as { detail?: string };
          if (typeof parsed.detail === "string") detail = parsed.detail;
        } catch {
          // keep the status line
        }
        throw new AiRequestError(response.status, detail);
      }
      setState({ status: "ready", value: normalise((await response.json()) as Ranking) });
    } catch (error) {
      setState({
        status: "error",
        detail: error instanceof AiRequestError ? error.detail : String(error),
      });
    }
  }, [fetchImpl, creativeId, variantId, focalSku]);

  useEffect(() => {
    void run();
    // Deliberately only on mount and on an explicit re-run. Ranking the space
    // is many simulations; re-running it on every keystroke in the focal-SKU
    // box would be a very expensive autocomplete.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div data-testid="optimize-view" style={style.root}>
      <header style={{ marginBottom: 14 }}>
        <div style={{ display: "flex", gap: 14, alignItems: "baseline", flexWrap: "wrap" }}>
          <div style={{ fontSize: 26, fontWeight: 700, letterSpacing: "-0.01em" }}>
            Where should this go?
          </div>
          <a data-testid="optimize-home-link" style={style.linkButton} href="#/home">
            ← All screens
          </a>
        </div>
        <div style={{ ...style.note, marginTop: 4, maxWidth: 820 }}>
          Every placement of a creative scored against 10,000 synthetic shoppers per persona
          and ranked, with the placement running today marked so &ldquo;best&rdquo; has
          something to be better than. Exhaustive, not a search: the space is small enough to
          score whole, which is what lets this say &ldquo;ranks 3rd of 8&rdquo;.
        </div>
      </header>

      <div style={{ ...style.panel, display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
        <label style={{ ...style.note, display: "flex", gap: 8, alignItems: "center" }}>
          Also try a SKU at every shelf level
          <input
            data-testid="optimize-focal-sku"
            value={focalSku}
            placeholder="SKU_008 (optional)"
            onChange={(event) => setFocalSku(event.target.value.trim())}
            style={{ ...style.tab, padding: "6px 9px", fontFamily: style.mono, width: 190 }}
          />
        </label>
        <button
          type="button"
          data-testid="optimize-run"
          style={state.status === "running" ? style.disabledButton : style.primaryButton}
          disabled={state.status === "running"}
          onClick={() => void run()}
        >
          {state.status === "running" ? "Scoring every placement…" : "Rank the placements"}
        </button>
      </div>

      {state.status === "error" && (
        <div data-testid="optimize-error" style={style.alertBox}>
          {state.detail}
        </div>
      )}

      {state.status === "ready" && <Result ranking={state.value} />}
    </div>
  );
}

// ---------------------------------------------------------------------------

/**
 * The three list fields, guaranteed to be arrays.
 *
 * The guarantee is the point: `Result` reads `entries[0]` and maps all three,
 * and a 200 carrying anything else - an older API, a proxy answering for a dead
 * upstream - threw during render and left a blank page instead of a screen that
 * says what it does know. Normalising here rather than at each use keeps the one
 * guarantee in one place.
 */
function normalise(ranking: Ranking): Ranking {
  return {
    ...ranking,
    entries: Array.isArray(ranking.entries) ? ranking.entries : [],
    summary_lines: Array.isArray(ranking.summary_lines) ? ranking.summary_lines : [],
    skipped: Array.isArray(ranking.skipped) ? ranking.skipped : [],
    beats_current: Array.isArray(ranking.beats_current) ? ranking.beats_current : [],
    spread_seeds: Array.isArray(ranking.spread_seeds) ? ranking.spread_seeds : [],
  };
}

function Result({ ranking }: { ranking: Ranking }) {
  const best = ranking.entries[0];

  // A ranking with no rows is not a ranking. Saying so beats an empty table
  // that reads as "we looked and there was nothing better".
  if (best === undefined) {
    return (
      <div data-testid="optimize-empty" style={{ ...style.panel, marginTop: 14 }}>
        The server returned no placements to rank. Nothing here is a result — check the API
        is the version this page expects.
      </div>
    );
  }

  return (
    <div style={{ display: "grid", gap: 14, marginTop: 14 }}>
      <div style={style.panel}>
        <div style={style.panelHeading}>The recommendation</div>
        {/* Verbatim from `optimizer.summary()`. The CLI, RESULTS.md and this
            screen have to say the same thing. */}
        <div data-testid="optimize-summary" style={{ fontSize: 17, lineHeight: 1.5 }}>
          {ranking.summary_lines.map((line, index) => (
            <p key={index} style={{ margin: index === 0 ? "0 0 8px" : "0 0 6px" }}>
              {line}
            </p>
          ))}
        </div>

        <div data-testid="optimize-objective" style={{ ...style.note, marginTop: 10 }}>
          Ranked on <strong>{ranking.objective_name}</strong>. The same space ranked on a
          different metric gives a different order, so this ranking only means anything
          together with that name.
        </div>

        <div data-testid="optimize-meta" style={{ ...style.note, marginTop: 4 }}>
          {ranking.n_candidates} placements · {ranking.n_synth} synthetic shoppers per
          persona · seed {ranking.seed} · variant {ranking.variant_id} ·{" "}
          {(ranking.elapsed_ms / 1000).toFixed(1)}s
        </div>
      </div>

      {best?.seed_spread != null && (
        <div data-testid="optimize-spread" style={style.cautionBox}>
          <strong>Seed spread on the top pick:</strong>{" "}
          {formatValue(best.seed_spread.low)} to {formatValue(best.seed_spread.high)} over{" "}
          {best.seed_spread.n_seeds} seeds ({best.seed_spread.seeds.join(", ")}).{" "}
          {ranking.spread_caveat}
        </div>
      )}

      <Resolution ranking={ranking} />

      <div style={style.panel}>
        <div style={style.panelHeading}>Every placement, scored</div>
        <div style={{ display: "grid", gap: 6 }}>
          {ranking.entries.map((entry) => (
            <Row key={entry.candidate_id} entry={entry} />
          ))}
        </div>
        {ranking.current_rank !== null && (
          <div data-testid="optimize-current" style={{ ...style.note, marginTop: 10 }}>
            The placement running today ranks <strong>{ranking.current_rank}</strong> of{" "}
            {ranking.n_candidates}. &ldquo;Best&rdquo; is only worth reading against that.
          </div>
        )}
      </div>

      {ranking.skipped.length > 0 && (
        <div style={style.panel}>
          <div style={style.panelHeading}>Not tried, and why</div>
          {/* Listed rather than dropped: a level missing from the ranking reads
              as "we tried it and it was bad", which is a different and much
              stronger claim than "there was nowhere to put it". */}
          <div data-testid="optimize-skipped" style={{ display: "grid", gap: 5 }}>
            {ranking.skipped.map((skip) => (
              <div key={skip.candidate_id} style={{ ...style.note, fontSize: 13 }}>
                <code style={style.monoStyle}>{skip.candidate_id}</code> — {skip.reason}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * Whether the ranking actually separates its leaders — the recommendation's own
 * weakness, stated rather than left for a reader to infer from a tidy table.
 */
function Resolution({ ranking }: { ranking: Ranking }) {
  const best = ranking.entries[0];
  const unresolved = best?.unresolved_against ?? [];

  if (ranking.top_pick_is_resolved === true) {
    return (
      <div data-testid="optimize-resolution" style={okBox}>
        The top pick&apos;s seed spread clears every other placement&apos;s, so the order at
        the top is settled at this run size.
        {ranking.beats_current.length > 0 && (
          <>
            {" "}
            {ranking.beats_current.length} placement
            {ranking.beats_current.length === 1 ? "" : "s"} also clear the current
            placement&apos;s spread entirely: {ranking.beats_current.join(", ")}.
          </>
        )}
      </div>
    );
  }

  if (ranking.top_pick_is_resolved === false) {
    return (
      <div data-testid="optimize-resolution" style={style.cautionBox}>
        <strong>This order is not settled.</strong> The top pick&apos;s seed spread overlaps{" "}
        {unresolved.join(", ")}, so it is not actually ranked against{" "}
        {unresolved.length === 1 ? "that placement" : "those placements"} at this run size.
        More seeds will not fix it — the spread is a min-max range and can only widen. Only a
        larger run size narrows it.
        {ranking.beats_current.length === 0 && (
          <> No placement clears the current one&apos;s spread either.</>
        )}
      </div>
    );
  }

  return (
    <div data-testid="optimize-resolution" style={{ ...style.panel, ...style.note }}>
      No seed spread was computed, so whether this order is settled is unknown rather than
      true.
    </div>
  );
}

function Row({ entry }: { entry: Entry }) {
  return (
    <div
      data-testid={`optimize-row-${entry.rank}`}
      data-current={String(entry.is_current)}
      style={{
        display: "flex",
        gap: 12,
        alignItems: "center",
        padding: "8px 10px",
        borderRadius: 7,
        border: `1px solid ${entry.is_current ? style.CHANGED : style.PANEL_BORDER}`,
        background: entry.rank === 1 ? "#22304a" : "#171c24",
      }}
    >
      <div style={{ flex: "0 0 28px", fontFamily: style.mono, opacity: 0.65 }}>
        {entry.rank}.
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontWeight: entry.rank === 1 ? 700 : 500 }}>{entry.label}</div>
        {entry.is_current && (
          <div style={{ ...style.note, color: style.CHANGED, fontSize: 12 }}>
            this is what is running today
          </div>
        )}
      </div>
      <div
        style={{
          flex: "0 0 92px",
          textAlign: "right",
          fontFamily: style.mono,
          fontSize: 15,
          // Undefined is not a bad score, and must not be coloured like one.
          color: entry.objective === null ? style.GREY : style.INK,
        }}
      >
        {/* Never 0%. The metric does not exist for this configuration. */}
        {entry.objective_text ?? "undefined"}
      </div>
      <a
        data-testid={`optimize-try-${entry.rank}`}
        style={{ ...style.linkButton, fontSize: 12, padding: "5px 10px" }}
        href="#/whatif"
        title="Open the what-if panel to try this move"
      >
        Try it
      </a>
    </div>
  );
}

function formatValue(value: number): string {
  const percent = value * 100;
  return `${percent >= 0 ? "+" : ""}${percent.toFixed(1)}%`;
}

const okBox: CSSProperties = {
  marginTop: 0,
  padding: "10px 12px",
  borderRadius: 8,
  border: `1px solid ${style.OK}`,
  background: "#16261c",
  color: style.INK,
  fontSize: 13,
  lineHeight: 1.5,
};
