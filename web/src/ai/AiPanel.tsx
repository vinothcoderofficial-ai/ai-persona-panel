import { useCallback, useEffect, useState } from "react";
import type { CSSProperties } from "react";
import {
  AiRequestError,
  getAiStatus,
  getPersonaPolicy,
  getPersonaTrace,
  regeneratePersonaPolicy,
  type AiPolicyResponse,
  type AiRegenerateResponse,
  type AiStatus,
  type AiTrace,
  type AiTraceShopper,
  type FetchLike,
  type PersonaPolicy,
  getResolvedPlanogram,
} from "@/ai/client";
import { buildAisle, describeSlot, type MappedBay } from "@/panel/aisleMap";
import * as style from "@/ai/styles";

/**
 * `#/ai` — where the language model is, and how to make it run.
 *
 * The AI in this project has always been real: `sim/policy.py` turns an
 * archetype into a numeric policy at temperature 0, and `sim/slow_agent.py`
 * walks twenty shoppers per persona through the store one turn at a time, each
 * turn with a stated reason. Both have been committed under `data/cache/` since
 * S12/S13 — and *nothing rendered any of it*, so from a running instance there
 * was no evidence a model had ever been involved and no way to make one answer.
 * That is the gap this screen closes, and it closes it three ways:
 *
 *  1. **The configuration, said out loud.** Which provider, which model, and —
 *     the part that actually matters when a demo fails on camera — whether a
 *     call would go out at all, with the server's own sentence when it would
 *     not. "AI unavailable" with no cause is the least useful thing this page
 *     could say, so it never says it.
 *  2. **The prompt beside the policy.** Eleven numbers with no instruction next
 *     to them are unfalsifiable. The prompt shown here is the string
 *     `sim/policy.py` actually sends, fetched from the server, never re-rendered
 *     here — a second copy of that template would drift from the first.
 *  3. **The trace.** Turn by turn, with the model's own reason, and carts named
 *     as products. This is the screen's strongest claim, because it is the one
 *     thing on it that a numpy simulator could not have produced.
 *
 * **Re-asking never adopts.** `data/cache/policies/` holds pre-registered
 * inputs; every lock in `predictions/` is hashed against the simulation those
 * numbers drive. So the button shows what the model says *now*, beside what is
 * being run, and says in as many words that nothing changed. The one place that
 * could be misread is the moment after a successful re-ask, which is exactly
 * where `ai-not-adopted` sits.
 */

export interface AiPanelProps {
  /** Injected in tests; the real screen uses the browser's own `fetch`. */
  fetchImpl?: FetchLike;
}

type Load<T> =
  | { status: "loading" }
  | { status: "error"; detail: string }
  | { status: "ready"; value: T };

const defaultFetch: FetchLike = (input, init) => fetch(input, init);

export function AiPanel({ fetchImpl = defaultFetch }: AiPanelProps) {
  const [status, setStatus] = useState<Load<AiStatus>>({ status: "loading" });
  const [selected, setSelected] = useState<string | null>(null);
  const [policy, setPolicy] = useState<Load<AiPolicyResponse>>({ status: "loading" });
  const [trace, setTrace] = useState<Load<AiTrace>>({ status: "loading" });

  /**
   * The shelf, for naming the slots a turn is about.
   *
   * The trace records targets as slot ids - `B1S1P1` - which told a viewer
   * nothing at all on a screen whose whole job is to be readable evidence. This
   * is the same join the dashboard and the replay use, and like both of them it
   * degrades to the raw id rather than blocking: variant A is the unpatched
   * baseline and its slots are the base planogram's, which is what the traces
   * were generated against.
   */
  const [aisle, setAisle] = useState<MappedBay[]>([]);
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const planogram = await getResolvedPlanogram("A", fetchImpl);
        if (!cancelled) setAisle(buildAisle(planogram));
      } catch {
        if (!cancelled) setAisle([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [fetchImpl]);

  const [asking, setAsking] = useState(false);
  const [asked, setAsked] = useState<AiRegenerateResponse | null>(null);
  const [askError, setAskError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const value = await getAiStatus(fetchImpl);
        if (cancelled) return;
        setStatus({ status: "ready", value });
        // The first persona on disk, so the screen is never empty on arrival.
        setSelected((current) => current ?? value.personas[0]?.persona_id ?? null);
      } catch (error) {
        if (cancelled) return;
        setStatus({ status: "error", detail: messageOf(error) });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [fetchImpl]);

  // Policy and trace are fetched independently: a persona with a policy and no
  // trace (the normal state before `python -m sim.slow_agent --all` has run)
  // must still show its policy rather than one failure blanking both halves.
  useEffect(() => {
    if (selected === null) return undefined;
    let cancelled = false;
    setPolicy({ status: "loading" });
    setTrace({ status: "loading" });
    // A re-ask belongs to the persona it was made for; carrying it across a
    // selection would caption one persona's policy with another's answer.
    setAsked(null);
    setAskError(null);

    void (async () => {
      try {
        const value = await getPersonaPolicy(selected, fetchImpl);
        if (!cancelled) setPolicy({ status: "ready", value });
      } catch (error) {
        if (!cancelled) setPolicy({ status: "error", detail: messageOf(error) });
      }
    })();

    void (async () => {
      try {
        const value = await getPersonaTrace(selected, fetchImpl);
        if (!cancelled) setTrace({ status: "ready", value });
      } catch (error) {
        if (!cancelled) setTrace({ status: "error", detail: messageOf(error) });
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [selected, fetchImpl]);

  const ask = useCallback(async () => {
    if (selected === null) return;
    setAsking(true);
    setAskError(null);
    setAsked(null);
    try {
      setAsked(await regeneratePersonaPolicy(selected, fetchImpl));
    } catch (error) {
      setAskError(messageOf(error));
    } finally {
      setAsking(false);
    }
  }, [selected, fetchImpl]);

  const canCall = status.status === "ready" && status.value.can_call;
  const reason = status.status === "ready" ? status.value.reason : null;

  return (
    <div data-testid="ai-panel" style={style.root}>
      <header style={{ marginBottom: 16 }}>
        <div style={{ display: "flex", gap: 14, alignItems: "baseline", flexWrap: "wrap" }}>
          <div style={{ fontSize: 26, fontWeight: 700, letterSpacing: "-0.01em" }}>
            The model
          </div>
          <a data-testid="ai-home-link" style={style.linkButton} href="#/home">
            ← All screens
          </a>
        </div>
        <div style={{ ...style.note, marginTop: 4, maxWidth: 780 }}>
          Persona policies and shopping traces are generated by a language model and cached
          under <code style={style.monoStyle}>data/cache/</code>. This page shows what it was
          asked, what it answered, and lets you ask it again — without touching what the
          simulator is running.
        </div>
      </header>

      <StatusBar load={status} />

      {status.status === "ready" && (
        <div style={{ display: "grid", gap: 14, marginTop: 14 }}>
          <div style={style.panel}>
            <div style={style.panelHeading}>Personas</div>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
                gap: 10,
              }}
            >
              {status.value.personas.map((persona) => (
                <button
                  key={persona.persona_id}
                  type="button"
                  data-testid={`ai-persona-${persona.persona_id}`}
                  style={
                    persona.persona_id === selected ? style.tabSelected : style.tab
                  }
                  onClick={() => setSelected(persona.persona_id)}
                >
                  <div style={{ fontFamily: style.mono, color: style.ACCENT }}>
                    {persona.persona_id}
                  </div>
                  <div style={{ ...style.note, marginTop: 3 }}>{persona.description}</div>
                  <div style={{ ...style.note, marginTop: 5, fontSize: 12 }}>
                    {persona.policy_cached ? "policy ✓" : "no policy"} ·{" "}
                    {persona.trace_cached
                      ? `${persona.trace_n_shoppers} traced shoppers`
                      : "no trace"}
                  </div>
                </button>
              ))}
            </div>
          </div>

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(340px, 1fr))",
              gap: 14,
              alignItems: "start",
            }}
          >
            <PromptPanel load={policy} />
            <PolicyPanel
              load={policy}
              canCall={canCall}
              reason={reason}
              asking={asking}
              asked={asked}
              askError={askError}
              onAsk={() => void ask()}
            />
          </div>

          <TracePanel load={trace} aisle={aisle} />
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------

function messageOf(error: unknown): string {
  if (error instanceof AiRequestError) return error.detail;
  return error instanceof Error ? error.message : String(error);
}

function StatusBar({ load }: { load: Load<AiStatus> }) {
  if (load.status === "loading") {
    return (
      <div style={style.panel} data-testid="ai-status">
        Reading the AI configuration…
      </div>
    );
  }
  if (load.status === "error") {
    return (
      <div style={style.panel}>
        <div style={style.panelHeading}>Configuration</div>
        <div data-testid="ai-status" style={{ color: style.ALERT }}>
          {load.detail}
        </div>
        <div style={{ ...style.note, marginTop: 8 }}>
          Start the API with <code style={style.monoStyle}>make api</code>, then reload.
        </div>
      </div>
    );
  }

  const s = load.value;
  return (
    <div style={style.panel}>
      <div style={style.panelHeading}>Configuration</div>
      <div
        data-testid="ai-status"
        style={{ display: "flex", flexWrap: "wrap", gap: 22, alignItems: "baseline" }}
      >
        <Field label="provider" value={s.provider} />
        <Field label="model" value={s.model ?? "—"} big />
        <Field label="planogram" value={s.planogram_id} />
        <Field
          label="api key"
          value={s.api_key_set ? "set" : "not set"}
          tone={s.api_key_set ? style.OK : style.GREY}
        />
        {/*
          "configured", never "available". The server checks that a provider is
          selected and a credential is present — it does not make a request, so
          it cannot know the credential is *accepted*. Saying "available" here
          was wrong in the first live run: the key was set and expired, the
          label read available, and the button then failed. A word this screen
          cannot back up is worse than no word.
        */}
        <Field
          label="live calls"
          value={s.can_call ? "configured" : "unavailable"}
          tone={s.can_call ? style.OK : style.ALERT}
        />
      </div>
      {s.reason !== null && (
        <div data-testid="ai-cannot-call" style={style.alertBox}>
          {s.reason}
        </div>
      )}
      <div style={{ ...style.note, marginTop: 10 }}>
        Prompt template: <code style={style.monoStyle}>{s.prompt_template}</code>
        {s.can_call && (
          <>
            {" "}
            · Configured is not the same as working — a credential is only known to be
            accepted once you ask.
          </>
        )}
      </div>
    </div>
  );
}

function Field({
  label,
  value,
  tone,
  big,
}: {
  label: string;
  value: string;
  tone?: string;
  big?: boolean;
}) {
  return (
    <div>
      <div style={{ ...style.note, fontSize: 11, textTransform: "uppercase", letterSpacing: "0.08em" }}>
        {label}
      </div>
      <div
        style={{
          fontFamily: style.mono,
          fontSize: big === true ? 20 : 15,
          color: tone ?? style.INK,
          marginTop: 2,
        }}
      >
        {value}
      </div>
    </div>
  );
}

function PromptPanel({ load }: { load: Load<AiPolicyResponse> }) {
  return (
    <div style={style.panel}>
      <div style={style.panelHeading}>What the model was asked</div>
      <pre data-testid="ai-prompt" style={style.pre}>
        {load.status === "ready"
          ? load.value.prompt
          : load.status === "error"
            ? load.detail
            : "Loading…"}
      </pre>
      <div style={{ ...style.note, marginTop: 8 }}>
        Rendered by <code style={style.monoStyle}>sim/policy.py</code> from the store&apos;s
        real brands and categories, and fetched from the server — this is the string that
        was sent, not a copy of the template.
      </div>
    </div>
  );
}

function PolicyPanel({
  load,
  canCall,
  reason,
  asking,
  asked,
  askError,
  onAsk,
}: {
  load: Load<AiPolicyResponse>;
  canCall: boolean;
  reason: string | null;
  asking: boolean;
  asked: AiRegenerateResponse | null;
  askError: string | null;
  onAsk: () => void;
}) {
  const disabled = !canCall || asking || load.status !== "ready";
  return (
    <div style={style.panel}>
      <div style={style.panelHeading}>What it answered</div>

      {load.status === "ready" ? (
        <PolicyTable policy={load.value.policy} changed={asked?.changed_fields ?? []} />
      ) : (
        <div data-testid="ai-policy" style={style.note}>
          {load.status === "error" ? load.detail : "Loading…"}
        </div>
      )}

      <div style={{ marginTop: 14, display: "flex", gap: 10, alignItems: "center" }}>
        <button
          type="button"
          data-testid="ai-ask"
          disabled={disabled}
          style={disabled ? style.disabledButton : style.primaryButton}
          onClick={onAsk}
        >
          {asking ? "Asking the model…" : "Ask the model again"}
        </button>
        {!canCall && reason !== null && (
          <span style={{ ...style.note, color: style.GREY }}>No call can be made.</span>
        )}
      </div>

      {askError !== null && (
        <div data-testid="ai-ask-error" style={style.alertBox}>
          {askError}
        </div>
      )}

      {asked !== null && <ReAsk asked={asked} />}
    </div>
  );
}

/**
 * One row per top-level policy field, with the ones a re-ask moved marked.
 *
 * Objects are printed as compact JSON rather than flattened. `brand_affinity`
 * moving on one brand is one thing to look at, not five rows — the same
 * whole-value comparison the server's `changed_fields` makes.
 */
function PolicyTable({
  policy,
  changed,
}: {
  policy: PersonaPolicy;
  changed: string[];
}) {
  const moved = new Set(changed);
  return (
    <div data-testid="ai-policy" style={{ display: "grid", gap: 4 }}>
      {Object.entries(policy).map(([key, value]) => (
        <div
          key={key}
          style={{
            display: "flex",
            gap: 10,
            padding: "3px 6px",
            borderRadius: 5,
            background: moved.has(key) ? "#2a2415" : "transparent",
          }}
        >
          <span
            style={{
              flex: "0 0 190px",
              fontFamily: style.mono,
              fontSize: 12.5,
              color: moved.has(key) ? style.CHANGED : style.GREY,
            }}
          >
            {key}
          </span>
          <span style={{ fontFamily: style.mono, fontSize: 12.5, wordBreak: "break-word" }}>
            {renderValue(value)}
          </span>
        </div>
      ))}
    </div>
  );
}

function renderValue(value: unknown): string {
  if (typeof value === "number") return String(value);
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

function ReAsk({ asked }: { asked: AiRegenerateResponse }) {
  return (
    <div style={{ marginTop: 14 }}>
      <div style={style.panelHeading}>
        Asked again — {asked.model} · {asked.elapsed_s.toFixed(2)}s
      </div>

      {/*
        The caption and the rows are one region, not two. The comparison is what
        `ai-diff` names — a testid on the sentence alone would let the rows go
        missing while the assertion that a difference is reported still passed.
      */}
      <div data-testid="ai-diff">
        <div style={{ ...style.note, marginBottom: 8 }}>
          {asked.differs ? (
            <>
              The model changed {asked.changed_fields.length} field
              {asked.changed_fields.length === 1 ? "" : "s"}, committed → fresh:
            </>
          ) : (
            <>It gave the same answer as the one being simulated.</>
          )}
        </div>

        {asked.differs && (
          <div style={{ display: "grid", gap: 4 }}>
            {asked.changed_fields.map((field) => (
              <div key={field} style={changedRow}>
                <span
                  style={{
                    flex: "0 0 190px",
                    fontFamily: style.mono,
                    fontSize: 12.5,
                    color: style.CHANGED,
                  }}
                >
                  {field}
                </span>
                <span style={{ fontFamily: style.mono, fontSize: 12.5, color: style.GREY }}>
                  {renderValue(asked.committed?.[field])}
                </span>
                <span style={{ fontFamily: style.mono, fontSize: 12.5 }}>→</span>
                <span style={{ fontFamily: style.mono, fontSize: 12.5 }}>
                  {renderValue(asked.policy[field])}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/*
        The one sentence on this page that has to be unmissable. Everything
        above it is a fresh answer from a model; the simulator is still running
        the committed one, and `predictions/` locks are hashed against that.
      */}
      <div data-testid="ai-not-adopted" style={style.cautionBox}>
        This answer was <strong>not</strong> adopted. The simulator is still running the
        committed policy. The fresh one was written to{" "}
        <code style={style.monoStyle}>{asked.written_to}</code> — copy it over the committed
        file yourself if you mean to change what is simulated, and regenerate the
        predictions that depend on it.
      </div>
    </div>
  );
}

const changedRow: CSSProperties = {
  display: "flex",
  gap: 10,
  alignItems: "baseline",
  padding: "3px 6px",
  borderRadius: 5,
  background: "#2a2415",
};

function TracePanel({ load, aisle }: { load: Load<AiTrace>; aisle: MappedBay[] }) {
  if (load.status !== "ready") {
    return (
      <div style={style.panel}>
        <div style={style.panelHeading}>The model shopping</div>
        <div data-testid="ai-trace" style={style.note}>
          {load.status === "error" ? load.detail : "Loading…"}
        </div>
      </div>
    );
  }

  const trace = load.value;
  return (
    <div style={style.panel}>
      <div style={style.panelHeading}>The model shopping</div>
      <div data-testid="ai-trace-header" style={{ ...style.note, marginBottom: 12 }}>
        {trace.n_shoppers} shoppers · {trace.n_turns} turns · model{" "}
        <code style={style.monoStyle}>{trace.model}</code> · temperature {trace.temperature}{" "}
        · seed {trace.seed}. Each turn below is one model call: it chose the action and wrote
        the reason.
      </div>
      <div data-testid="ai-trace" style={{ display: "grid", gap: 12 }}>
        {trace.shoppers.map((shopper) => (
          <Shopper key={shopper.shopper_index} shopper={shopper} aisle={aisle} />
        ))}
      </div>
    </div>
  );
}

function Shopper({ shopper, aisle }: { shopper: AiTraceShopper; aisle: MappedBay[] }) {
  return (
    <div
      data-testid={`ai-trace-shopper-${shopper.shopper_index}`}
      style={{
        border: `1px solid ${style.PANEL_BORDER}`,
        borderRadius: 8,
        background: "#171c24",
        padding: 12,
      }}
    >
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "baseline" }}>
        <strong>Shopper {shopper.shopper_index + 1}</strong>
        <span style={style.note}>
          {shopper.n_turns} turns · left via {shopper.end_reason}
        </span>
      </div>

      <ol style={{ margin: "10px 0 0", padding: "0 0 0 20px", display: "grid", gap: 6 }}>
        {shopper.turns.map((turn) => (
          <li
            key={turn.turn}
            data-testid={`ai-turn-${shopper.shopper_index}-${turn.turn}`}
            style={{ fontSize: 13.5 }}
          >
            <span style={{ fontFamily: style.mono, color: style.ACCENT }}>{turn.action}</span>
            {turn.target !== null && <TurnTarget aisle={aisle} slotId={turn.target} />}
            <span style={{ opacity: 0.5 }}> · {turn.station_id}</span>
            <div style={{ ...style.note, marginTop: 1 }}>&ldquo;{turn.reason}&rdquo;</div>
          </li>
        ))}
      </ol>

      {/* Product names, never bare sku ids. `cart_detail` exists for this. */}
      <div style={{ ...style.note, marginTop: 10 }}>
        Left with:{" "}
        {shopper.cart_detail.length === 0 ? (
          <em>nothing</em>
        ) : (
          shopper.cart_detail
            .map((line) => `${line.name} (${line.brand}, ${line.price})`)
            .join(" · ")
        )}
      </div>
    </div>
  );
}


/**
 * The slot a turn is about, named.
 *
 * The product first, because that is what a viewer is reading for, and the slot
 * id after it in small type, because that is the key the trace, the attention
 * vector and every patch are written in - an operator reading a turn and then
 * writing a what-if patch needs both. Falls back to the id alone when the
 * planogram is not loaded or does not know the slot; it never guesses.
 */
function TurnTarget({ aisle, slotId }: { aisle: MappedBay[]; slotId: string }) {
  const described = describeSlot(aisle, slotId);
  if (described === null) {
    return <span style={{ fontFamily: style.mono, opacity: 0.7 }}> {slotId}</span>;
  }
  return (
    <>
      {" "}
      <strong>{described.product}</strong>
      <span style={{ opacity: 0.5 }}> ({described.position})</span>
      <span style={{ fontFamily: style.mono, opacity: 0.35, fontSize: 11 }}> {slotId}</span>
    </>
  );
}
