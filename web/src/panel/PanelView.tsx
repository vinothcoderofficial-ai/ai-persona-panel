import { useCallback, useEffect, useMemo, useState } from "react";
import type { CSSProperties } from "react";
import {
  getAiStatus,
  getPersonaTrace,
  getResolvedPlanogram,
  type AiPersonaSummary,
  type AiTrace,
  type AiTraceShopper,
  type FetchLike,
  type PlanogramDocument,
} from "@/ai/client";
import {
  buildAisle,
  describeSlot,
  levelLabel,
  type MappedBay,
  type MappedSlot,
} from "@/panel/aisleMap";
import { stateAt, stationsOf } from "@/panel/playback";
import * as style from "@/ai/styles";

/**
 * `#/panel` — a synthetic shopper, shopping.
 *
 * The demo had a human half and no visible synthetic one. A person could shop
 * the 3D aisle and be scored against a locked prediction afterwards, but the
 * synthetic panel — the thing the whole project is named for — only ever
 * appeared as an aggregate: a Spearman, a purchase share, a lift. The twenty
 * shopping trips per persona that `sim/slow_agent.py` generated, each turn with
 * the model's stated reason, sat in `data/cache/traces/` and were drawn by
 * nothing. "Synthetic isn't there, only human interaction" was a fair reading of
 * the running product.
 *
 * So: the same shelf the shopper sees, drawn as products rather than slot ids,
 * with one persona's trip replayed over it a turn at a time — the bay it is
 * standing at, the slot it is looking at, the reason it gave, the cart filling.
 *
 * **Deliberately not the 3D scene.** `PlanogramScene` is the measured screen:
 * it owns a session, a webcam tracker and the checkout gate, and rendering it
 * here would mean handing it a fake logger and a fake consent to replay a
 * shopper that is not being measured. A replay that could open a session, or
 * trip a gate, would put invented rows next to real ones in the evidence. This
 * draws the same planogram from the same resolved document, and touches nothing
 * a session owns.
 *
 * Every product name on screen comes from `aisleMap`, which is the one place the
 * slot-to-product join happens.
 */

export interface PanelViewProps {
  fetchImpl?: FetchLike;
  /** Which arm's shelf to replay against. Defaults to the unpatched baseline. */
  variantId?: string;
  /** Milliseconds per turn while playing. */
  advanceMs?: number;
}

const defaultFetch: FetchLike = (input, init) => fetch(input, init);

type Load<T> =
  | { status: "loading" }
  | { status: "error"; detail: string }
  | { status: "ready"; value: T };

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function PanelView({
  fetchImpl = defaultFetch,
  variantId = "A",
  advanceMs = 1100,
}: PanelViewProps) {
  const [personas, setPersonas] = useState<AiPersonaSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [planogram, setPlanogram] = useState<Load<PlanogramDocument>>({ status: "loading" });
  const [trace, setTrace] = useState<Load<AiTrace>>({ status: "loading" });
  const [shopperIndex, setShopperIndex] = useState(0);
  const [step, setStep] = useState(0);
  const [playing, setPlaying] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const value = await getResolvedPlanogram(variantId, fetchImpl);
        if (!cancelled) setPlanogram({ status: "ready", value });
      } catch (error) {
        if (!cancelled) setPlanogram({ status: "error", detail: messageOf(error) });
      }
    })();
    void (async () => {
      try {
        const status = await getAiStatus(fetchImpl);
        if (cancelled) return;
        setPersonas(status.personas);
        setSelected((current) => current ?? status.personas[0]?.persona_id ?? null);
      } catch {
        // The persona list is a convenience; a failure here leaves the picker
        // empty and the shelf still drawn, which is more useful than a blank
        // screen. The trace's own failure is the one that gets a message.
        if (!cancelled) setPersonas([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [fetchImpl, variantId]);

  useEffect(() => {
    if (selected === null) return undefined;
    let cancelled = false;
    setTrace({ status: "loading" });
    // A new persona is a new trip. Carrying the step or the shopper across
    // would show one persona's cart under another's name.
    setStep(0);
    setShopperIndex(0);
    setPlaying(false);

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

  const shopper: AiTraceShopper | null =
    trace.status === "ready" ? trace.value.shoppers[shopperIndex] ?? null : null;
  const turns = useMemo(() => shopper?.turns ?? [], [shopper]);
  const playback = stateAt(turns, step);

  const aisle: MappedBay[] = useMemo(
    () => (planogram.status === "ready" ? buildAisle(planogram.value) : []),
    [planogram],
  );

  // Stop at the end rather than looping. A loop makes a five-turn trip look
  // like an endless one, and there is no "the shopper left" moment to read.
  const atEnd = turns.length === 0 || step >= turns.length - 1;
  useEffect(() => {
    if (!playing) return undefined;
    if (atEnd) {
      setPlaying(false);
      return undefined;
    }
    const timer = window.setInterval(() => setStep((n) => n + 1), advanceMs);
    return () => window.clearInterval(timer);
  }, [playing, atEnd, advanceMs]);

  const forward = useCallback(() => setStep((n) => n + 1), []);
  const back = useCallback(() => setStep((n) => Math.max(0, n - 1)), []);

  return (
    <div data-testid="panel-view" style={style.root}>
      <Header
        personas={personas}
        selected={selected}
        onSelect={setSelected}
        trace={trace}
        variantId={variantId}
      />

      <Transport
        playing={playing}
        atEnd={atEnd}
        turnCount={turns.length}
        step={playback.index}
        onPlay={() => (atEnd ? (setStep(0), setPlaying(true)) : setPlaying(!playing))}
        onForward={forward}
        onBack={back}
        onReset={() => {
          setPlaying(false);
          setStep(0);
        }}
        shopperCount={trace.status === "ready" ? trace.value.shoppers.length : 0}
        shopperIndex={shopperIndex}
        onShopper={(index) => {
          setPlaying(false);
          setStep(0);
          setShopperIndex(index);
        }}
      />

      <Caption
        trace={trace}
        action={playback.action}
        reason={playback.reason}
        targetLabel={
          playback.targetSlotId === null
            ? null
            : describeSlot(aisle, playback.targetSlotId)
        }
      />

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "minmax(0, 3fr) minmax(220px, 1fr)",
          gap: 14,
          marginTop: 14,
          alignItems: "start",
        }}
      >
        <Aisle
          load={planogram}
          aisle={aisle}
          currentBay={playback.stationId}
          targetSlotId={playback.targetSlotId}
          visited={stationsOf(turns.slice(0, playback.index + 1))}
        />
        <Cart aisle={aisle} slotIds={playback.cartSlotIds} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------

function Header({
  personas,
  selected,
  onSelect,
  trace,
  variantId,
}: {
  personas: AiPersonaSummary[];
  selected: string | null;
  onSelect: (id: string) => void;
  trace: Load<AiTrace>;
  variantId: string;
}) {
  return (
    <header style={{ marginBottom: 14 }}>
      <div style={{ display: "flex", gap: 14, alignItems: "baseline", flexWrap: "wrap" }}>
        <div style={{ fontSize: 26, fontWeight: 700, letterSpacing: "-0.01em" }}>
          A synthetic shopper
        </div>
        <a data-testid="panel-home-link" style={style.linkButton} href="#/home">
          ← All screens
        </a>
      </div>
      <div style={{ ...style.note, marginTop: 4, maxWidth: 820 }}>
        One persona&apos;s shopping trip, replayed over the same shelf a person shops —
        variant <code style={style.monoStyle}>{variantId}</code>. Every move and every
        reason below was written by the language model, one call per turn, and committed to{" "}
        <code style={style.monoStyle}>data/cache/traces/</code>
        {trace.status === "ready" && (
          <>
            {" "}
            by <code style={style.monoStyle}>{trace.value.model}</code> at temperature{" "}
            {trace.value.temperature}
          </>
        )}
        . Nobody is measured here and no session is opened.
      </div>

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 12 }}>
        {personas.map((persona) => (
          <button
            key={persona.persona_id}
            type="button"
            data-testid={`panel-persona-${persona.persona_id}`}
            style={persona.persona_id === selected ? style.tabSelected : style.tab}
            onClick={() => onSelect(persona.persona_id)}
          >
            <span style={{ fontFamily: style.mono, color: style.ACCENT }}>
              {persona.persona_id}
            </span>
            <span style={{ ...style.note, marginLeft: 8 }}>{persona.description}</span>
          </button>
        ))}
      </div>
    </header>
  );
}

function Transport({
  playing,
  atEnd,
  turnCount,
  step,
  onPlay,
  onForward,
  onBack,
  onReset,
  shopperCount,
  shopperIndex,
  onShopper,
}: {
  playing: boolean;
  atEnd: boolean;
  turnCount: number;
  step: number;
  onPlay: () => void;
  onForward: () => void;
  onBack: () => void;
  onReset: () => void;
  shopperCount: number;
  shopperIndex: number;
  onShopper: (index: number) => void;
}) {
  return (
    <div style={{ ...style.panel, display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
      <button
        type="button"
        data-testid="panel-play"
        style={style.primaryButton}
        onClick={onPlay}
      >
        {playing ? "Pause" : atEnd && turnCount > 0 ? "Play again" : "Play"}
      </button>
      <button type="button" data-testid="panel-step-back" style={style.tab} onClick={onBack}>
        ‹ Back
      </button>
      <button
        type="button"
        data-testid="panel-step-forward"
        style={style.tab}
        onClick={onForward}
      >
        Next ›
      </button>
      <button type="button" data-testid="panel-reset" style={style.tab} onClick={onReset}>
        Restart
      </button>

      <div data-testid="panel-progress" style={{ fontFamily: style.mono, fontSize: 14 }}>
        {turnCount === 0 ? "no turns" : `turn ${step + 1} of ${turnCount}`}
      </div>

      {shopperCount > 1 && (
        <label style={{ ...style.note, display: "flex", gap: 8, alignItems: "center" }}>
          Shopper
          <select
            data-testid="panel-shopper"
            value={shopperIndex}
            style={{ ...style.tab, padding: "6px 8px" }}
            onChange={(event) => onShopper(Number(event.target.value))}
          >
            {Array.from({ length: shopperCount }, (_, index) => (
              <option key={index} value={index}>
                {index + 1}
              </option>
            ))}
          </select>
          of {shopperCount}
        </label>
      )}
    </div>
  );
}

function Caption({
  trace,
  action,
  reason,
  targetLabel,
}: {
  trace: Load<AiTrace>;
  action: string | null;
  reason: string | null;
  targetLabel: { product: string; position: string } | null;
}) {
  if (trace.status !== "ready") {
    return (
      <div data-testid="panel-caption" style={{ ...style.panel, marginTop: 14 }}>
        {trace.status === "error" ? trace.detail : "Loading the trip…"}
      </div>
    );
  }

  return (
    <div data-testid="panel-caption" style={{ ...style.panel, marginTop: 14 }}>
      <div style={{ display: "flex", gap: 12, alignItems: "baseline", flexWrap: "wrap" }}>
        <span
          style={{
            fontFamily: style.mono,
            fontSize: 18,
            color: style.ACCENT,
          }}
        >
          {action ?? "—"}
        </span>
        {targetLabel !== null && (
          <span style={{ fontSize: 16 }}>
            <strong>{targetLabel.product}</strong>{" "}
            <span style={{ opacity: 0.65 }}>· {targetLabel.position}</span>
          </span>
        )}
      </div>
      <div style={{ marginTop: 6, fontSize: 16, lineHeight: 1.5 }}>
        {reason === null || reason === "" ? (
          <em style={{ opacity: 0.6 }}>no reason recorded for this turn</em>
        ) : (
          <>&ldquo;{reason}&rdquo;</>
        )}
      </div>
    </div>
  );
}

function Aisle({
  load,
  aisle,
  currentBay,
  targetSlotId,
  visited,
}: {
  load: Load<PlanogramDocument>;
  aisle: MappedBay[];
  currentBay: string | null;
  targetSlotId: string | null;
  visited: string[];
}) {
  if (load.status !== "ready") {
    return (
      <div style={style.panel}>
        {load.status === "error" ? load.detail : "Loading the shelf…"}
      </div>
    );
  }

  return (
    <div style={{ display: "grid", gap: 12 }}>
      {aisle.map((bay) => {
        const isCurrent = bay.bay_id === currentBay;
        return (
          <div
            key={bay.bay_id}
            data-testid={`panel-bay-${bay.bay_id}`}
            data-current={String(isCurrent)}
            style={{
              ...style.panel,
              borderColor: isCurrent ? style.ACCENT : style.PANEL_BORDER,
              opacity: isCurrent || visited.includes(bay.bay_id) ? 1 : 0.55,
            }}
          >
            <div style={{ display: "flex", gap: 10, alignItems: "baseline" }}>
              <div style={style.panelHeading}>
                {bay.bay_id} · {bay.type}
              </div>
              {isCurrent && (
                <div style={{ ...style.note, color: style.ACCENT }}>shopper is here</div>
              )}
            </div>

            {bay.adSlots.length > 0 && (
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 10 }}>
                {bay.adSlots.map((ad) => (
                  <div
                    key={ad.ad_slot_id}
                    data-testid={`panel-adslot-${ad.ad_slot_id}`}
                    data-target={String(ad.ad_slot_id === targetSlotId)}
                    style={{
                      ...adChip,
                      borderColor:
                        ad.ad_slot_id === targetSlotId ? style.CHANGED : style.PANEL_BORDER,
                      opacity: ad.creative_id === null ? 0.45 : 1,
                    }}
                  >
                    {ad.type.replace(/_/g, " ")}:{" "}
                    {ad.creative_id === null ? "no creative" : `${ad.creative_id} (${ad.brand})`}
                  </div>
                ))}
              </div>
            )}

            <div style={{ display: "grid", gap: 6 }}>
              {bay.shelves.map((shelf) => (
                <div
                  key={shelf.shelf_id}
                  data-testid={`panel-shelf-${shelf.shelf_id}`}
                  style={{ display: "flex", gap: 8, alignItems: "stretch" }}
                >
                  <div
                    style={{
                      flex: "0 0 118px",
                      ...style.note,
                      fontSize: 12,
                      alignSelf: "center",
                    }}
                  >
                    {levelLabel(shelf.level)}
                  </div>
                  <div style={{ display: "flex", gap: 8, flex: 1, minWidth: 0 }}>
                    {shelf.slots.map((slot) => (
                      <Slot
                        key={slot.slot_id}
                        slot={slot}
                        isTarget={slot.slot_id === targetSlotId}
                      />
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function Slot({ slot, isTarget }: { slot: MappedSlot; isTarget: boolean }) {
  const empty = slot.sku_id === null;
  return (
    <div
      data-testid={`panel-slot-${slot.slot_id}`}
      data-target={String(isTarget)}
      style={{
        flex: "1 1 0",
        minWidth: 0,
        display: "flex",
        gap: 8,
        alignItems: "center",
        padding: "6px 8px",
        borderRadius: 7,
        border: `1px solid ${isTarget ? style.ACCENT : style.PANEL_BORDER}`,
        background: isTarget ? "#22304a" : empty ? "#151920" : "#171c24",
      }}
    >
      {!empty && slot.texture_url !== null && (
        <img
          src={slot.texture_url}
          alt=""
          style={{ width: 26, height: 34, objectFit: "cover", borderRadius: 3, flex: "0 0 auto" }}
        />
      )}
      <div style={{ minWidth: 0 }}>
        <div
          style={{
            fontSize: 12.5,
            fontWeight: empty ? 400 : 600,
            opacity: empty ? 0.5 : 1,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {/* Never the slot id. An empty position says so — it is a real slot
              with no product, and it is what "move a SKU to eye level" moves
              something into. */}
          {slot.name ?? "empty"}
        </div>
        {!empty && (
          <div style={{ ...style.note, fontSize: 11 }}>
            {slot.brand} · {slot.price} · {slot.facings} facing
            {slot.facings === 1 ? "" : "s"}
            {slot.promo && <span style={{ color: style.CHANGED }}> · promo</span>}
          </div>
        )}
      </div>
    </div>
  );
}

function Cart({ aisle, slotIds }: { aisle: MappedBay[]; slotIds: string[] }) {
  const lines = slotIds.map((slotId) => ({ slotId, described: describeSlot(aisle, slotId) }));
  const total = slotIds.reduce((sum, slotId) => {
    for (const bay of aisle) {
      for (const shelf of bay.shelves) {
        for (const slot of shelf.slots) {
          if (slot.slot_id === slotId && slot.price !== null) return sum + slot.price;
        }
      }
    }
    return sum;
  }, 0);

  return (
    <div data-testid="panel-cart" style={style.panel}>
      <div style={style.panelHeading}>Cart</div>
      {lines.length === 0 ? (
        <div style={style.note}>Nothing picked up yet.</div>
      ) : (
        <div style={{ display: "grid", gap: 6 }}>
          {lines.map((line, index) => (
            <div key={`${line.slotId}-${index}`} style={{ fontSize: 13.5 }}>
              <div style={{ fontWeight: 600 }}>{line.described?.product ?? line.slotId}</div>
              <div style={{ ...style.note, fontSize: 12 }}>{line.described?.position}</div>
            </div>
          ))}
          <div style={{ marginTop: 6, borderTop: `1px solid ${style.PANEL_BORDER}`, paddingTop: 6 }}>
            Total {total.toFixed(2)}
          </div>
        </div>
      )}
    </div>
  );
}

const adChip: CSSProperties = {
  padding: "5px 9px",
  borderRadius: 6,
  border: `1px solid ${style.PANEL_BORDER}`,
  background: "#171c24",
  fontSize: 12,
};
