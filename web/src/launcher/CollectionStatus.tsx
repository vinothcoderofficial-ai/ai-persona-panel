import { useEffect, useState } from "react";
import type { CSSProperties } from "react";
import type { FetchLike } from "@/ai/client";

/**
 * The real panel, on the launcher.
 *
 * `RESULTS.md` has said "not yet collected" for every real-panel number since
 * the project started, and finding out why meant opening `shoppertwin.db` in a
 * SQL client. An operator running people back to back could not watch the panel
 * fill, could not see what people were being rejected for, and could not tell
 * whether what they had collected had reached the committed corpus. The first
 * real session was rejected for lasting 29 seconds against a 45-second minimum,
 * and that was only discovered afterwards, from the database.
 *
 * Two distinctions the box exists to hold, both of which a single "sessions: 7"
 * would destroy:
 *
 *  * **Unfinished is not rejected.** Nothing has judged a session that is still
 *    open, and counting it as a rejection would fill the histogram with people
 *    who closed the tab.
 *  * **Collected is not committed.** `scripts/eval.py` reads
 *    `data/sessions/anon/` and nothing else, so a session in SQLite is not
 *    evidence yet however green it looks - and the box says which command moves
 *    it, because a status that reports work without naming it is half a message.
 *
 * A failure to reach the API renders as a failure to reach the API. Zeros there
 * would read as "nobody has shopped", which is a claim about the panel rather
 * than about this browser.
 */

export interface CollectionStatusProps {
  fetchImpl?: FetchLike;
}

interface Status {
  live: {
    total: number;
    accepted: number;
    rejected: number;
    undecided: number;
    no_consent: number;
    reject_reasons: Record<string, number>;
  };
  committed: { sessions: number; directory: string };
  locks: number;
  export_needed: boolean;
  export_command: string;
  eval_command: string;
  gate: {
    min_observed_slots: number;
    min_stations: number;
    min_interactions: number;
    min_fixation_coverage: number;
  };
}

type Load =
  | { status: "loading" }
  | { status: "unreachable" }
  | { status: "ready"; value: Status };

const defaultFetch: FetchLike = (input, init) => fetch(input, init);

/**
 * Is this actually a status document?
 *
 * A 200 is not a promise about shape. A proxy answering for a dead upstream, an
 * older API, a half-written response - each arrives as a successful fetch, and
 * reading `.live.reject_reasons` off it throws during render and blanks the
 * whole launcher, taking every link on the page down with it. That happened
 * here, and it is the third component in this codebase to do it.
 *
 * An unrecognised body is treated as "could not reach the API", which is the
 * honest reading: this browser did not get an answer it understands.
 */
function isStatus(value: unknown): value is Status {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Partial<Status>;
  return (
    typeof candidate.live === "object" &&
    candidate.live !== null &&
    typeof candidate.live.reject_reasons === "object" &&
    typeof candidate.committed === "object" &&
    candidate.committed !== null &&
    typeof candidate.gate === "object" &&
    candidate.gate !== null
  );
}

export function CollectionStatus({ fetchImpl = defaultFetch }: CollectionStatusProps) {
  const [state, setState] = useState<Load>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const response = await fetchImpl("/api/collection/status");
        if (!response.ok) throw new Error(String(response.status));
        const value = (await response.json()) as Status;
        if (!isStatus(value)) throw new Error("not a collection status document");
        if (!cancelled) setState({ status: "ready", value });
      } catch {
        if (!cancelled) setState({ status: "unreachable" });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [fetchImpl]);

  if (state.status === "loading") {
    return (
      <div data-testid="collection-status" style={panel}>
        <div style={heading}>The real panel</div>
        <div style={note}>Reading the panel…</div>
      </div>
    );
  }

  if (state.status === "unreachable") {
    return (
      <div data-testid="collection-status" style={panel}>
        <div style={heading}>The real panel</div>
        <div style={note}>
          Could not reach the API, so the panel is unknown from here. Start it with{" "}
          <code style={mono}>make api</code> and reload.
        </div>
      </div>
    );
  }

  const s = state.value;
  const rejects = Object.entries(s.live.reject_reasons);

  return (
    <div data-testid="collection-status" style={panel}>
      <div style={heading}>The real panel</div>

      {s.live.total === 0 ? (
        <div style={note}>
          Nobody has shopped yet. Open a variant below, or hand out links with{" "}
          <code style={mono}>python scripts/collect_link.py</code>. Every number in{" "}
          <code style={mono}>RESULTS.md</code> that compares real against synthetic stays
          &ldquo;not yet collected&rdquo; until this fills.
        </div>
      ) : (
        <>
          <div style={{ display: "flex", gap: 24, flexWrap: "wrap", marginBottom: 10 }}>
            <Figure label="accepted" value={s.live.accepted} tone={OK} />
            <Figure label="rejected" value={s.live.rejected} tone={ALERT} />
            {/* Unfinished, not rejected. */}
            <Figure label="unfinished" value={s.live.undecided} tone={GREY} />
            <Figure label="declined consent" value={s.live.no_consent} tone={GREY} />
            <Figure label="committed" value={s.committed.sessions} tone={ACCENT} />
            <Figure label="locks" value={s.locks} tone={GREY} />
          </div>

          {rejects.length > 0 && (
            <div data-testid="collection-rejects" style={{ ...note, marginBottom: 8 }}>
              Rejected for:{" "}
              {rejects
                .map(([reason, count]) => `${reason} (${count})`)
                .join(", ")}
              .
            </div>
          )}
        </>
      )}

      <div data-testid="collection-gate" style={{ ...note, marginTop: 4 }}>
        A session is accepted once it has looked at {s.gate.min_observed_slots} or more
        slots, across {s.gate.min_stations} or more bays, with at least{" "}
        {s.gate.min_interactions} interaction — and, for a webcam session,{" "}
        {Math.round(s.gate.min_fixation_coverage * 100)}% fixation coverage. There is no
        minimum length: a shopper with a list who covers the shelf in half a minute is
        evidence, and a slow one who studies a single product is not.
      </div>

      {s.export_needed && (
        <div data-testid="collection-action" style={action}>
          Accepted sessions are in the database but not in{" "}
          <code style={mono}>{s.committed.directory}</code>, which is the only thing{" "}
          <code style={mono}>scripts/eval.py</code> reads. Commit them with{" "}
          <code style={mono}>{s.export_command}</code>, then{" "}
          <code style={mono}>{s.eval_command}</code> to regenerate{" "}
          <code style={mono}>RESULTS.md</code>.
        </div>
      )}
    </div>
  );
}

function Figure({ label, value, tone }: { label: string; value: number; tone: string }) {
  return (
    <div>
      <div style={{ fontSize: 22, fontWeight: 700, color: tone, fontFamily: monoFamily }}>
        {value}
      </div>
      <div style={{ ...note, fontSize: 11.5, textTransform: "uppercase", letterSpacing: "0.06em" }}>
        {label}
      </div>
    </div>
  );
}

// The launcher owns its own palette (see Launcher.tsx) and this is a copy of
// the same values, for the same reason: nothing under src/launcher/ imports
// from src/spectator/.
const INK = "#e8eaed";
const PANEL_BG = "#1c2129";
const PANEL_BORDER = "#2b323d";
const ACCENT = "#4f8cff";
const CAUTION = "#f59e0b";
const GREY = "#7a828f";
const OK = "#4ade80";
const ALERT = "#ff6b5e";
const monoFamily = "ui-monospace, SFMono-Regular, Consolas, Menlo, monospace";

const panel: CSSProperties = {
  background: PANEL_BG,
  border: `1px solid ${PANEL_BORDER}`,
  borderRadius: 10,
  padding: 16,
  marginBottom: 16,
  color: INK,
};

const heading: CSSProperties = {
  margin: "0 0 10px",
  fontSize: 13,
  fontWeight: 700,
  letterSpacing: "0.09em",
  textTransform: "uppercase",
  opacity: 0.72,
};

const note: CSSProperties = { fontSize: 13, opacity: 0.78 };
const mono: CSSProperties = { fontFamily: monoFamily };

const action: CSSProperties = {
  marginTop: 12,
  padding: "10px 12px",
  borderRadius: 8,
  border: `1px solid ${CAUTION}`,
  background: "#2a2415",
  fontSize: 13,
  lineHeight: 1.55,
};
