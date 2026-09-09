import { useCallback, useState } from "react";
import type { CSSProperties } from "react";
import type { FetchLike } from "@/ai/client";
import * as style from "@/ai/styles";
import { labToCss } from "@/store/palette";

/**
 * `#/vision` — drop in a clip, get a shelf.
 *
 * Before S30, `vision/` held two empty `__init__.py` files. "Video → 3D store"
 * was a claim with nothing behind it: no detector, no pipeline, no committed
 * `video_aisle.json`, and nothing in the running product that so much as
 * accepted a file. The pipeline now exists — classical CV, shelf edges and
 * colour runs, running on a CPU — and this is the screen that makes it usable
 * without a terminal.
 *
 * The whole design problem here is **not over-reading the result**. A planogram
 * drawn on a screen looks exactly as authoritative whether it came from a
 * carefully hand-authored file or from six frames of a phone video, and this
 * one came from six frames of a phone video. So:
 *
 *  * every product is shown as unidentified, because the pipeline reads
 *    geometry and colour and cannot read brands, names or prices;
 *  * per-slot confidence is on screen, because a slot seen once and a slot seen
 *    in every frame must not look alike;
 *  * "this was not saved" is stated rather than left to be inferred from the
 *    absence of the id somewhere else;
 *  * a clip with no shelves produces the server's refusal, and never an empty
 *    store — a wall that became a planogram would be indistinguishable from a
 *    real shelf everywhere downstream.
 */

export interface VisionViewProps {
  fetchImpl?: FetchLike;
}

const defaultFetch: FetchLike = (input, init) => fetch(input, init);

interface Slot {
  slot_id: string;
  sku_id: string | null;
  facings: number;
  confidence?: number;
}

interface Shelf {
  shelf_id: string;
  level: string;
  slots: Slot[];
}

interface Sku {
  sku_id: string;
  name: string;
  color_lab: number[];
}

interface Planogram {
  planogram_id: string;
  name: string;
  source: string;
  bays: Array<{ bay_id: string; shelves: Shelf[] }>;
  skus: Sku[];
}

interface Reading {
  planogram: Planogram;
  frames_sampled: number;
  notes: string[];
  saved: boolean;
}

type Load =
  | { status: "idle" }
  | { status: "reading"; filename: string }
  | { status: "error"; detail: string }
  | { status: "ready"; value: Reading };

/**
 * Is this a reading, rather than merely a 200?
 *
 * Guarded before it can bite. Three components in this codebase have blanked a
 * page on an unexpected success body; this one checks the shape it is about to
 * walk instead of joining them.
 */
function isReading(value: unknown): value is Reading {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Partial<Reading>;
  const planogram = candidate.planogram as Partial<Planogram> | undefined;
  return (
    typeof planogram === "object" &&
    planogram !== null &&
    Array.isArray(planogram.bays) &&
    Array.isArray(planogram.skus) &&
    Array.isArray(candidate.notes)
  );
}

export function VisionView({ fetchImpl = defaultFetch }: VisionViewProps) {
  const [state, setState] = useState<Load>({ status: "idle" });

  const read = useCallback(
    async (file: File) => {
      setState({ status: "reading", filename: file.name });
      try {
        const form = new FormData();
        form.append("video", file);
        const response = await fetchImpl("/api/vision/planogram", {
          method: "POST",
          body: form,
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
          setState({ status: "error", detail });
          return;
        }

        const value = (await response.json()) as unknown;
        if (!isReading(value)) {
          setState({
            status: "error",
            detail:
              "The server answered, but not with a reading. Check the API is the " +
              "version this page expects.",
          });
          return;
        }
        setState({ status: "ready", value });
      } catch (error) {
        setState({
          status: "error",
          detail: error instanceof Error ? error.message : String(error),
        });
      }
    },
    [fetchImpl],
  );

  return (
    <div data-testid="vision-view" style={style.root}>
      <header style={{ marginBottom: 14 }}>
        <div style={{ display: "flex", gap: 14, alignItems: "baseline", flexWrap: "wrap" }}>
          <div style={{ fontSize: 26, fontWeight: 700, letterSpacing: "-0.01em" }}>
            A shelf, read from video
          </div>
          <a data-testid="vision-home-link" style={style.linkButton} href="#/home">
            ← All screens
          </a>
        </div>
        <div data-testid="vision-intro" style={{ ...style.note, marginTop: 4, maxWidth: 840 }}>
          Point a camera at a shelf bay, roughly front-on, with the shelf edges visible
          across most of the frame, and drop the clip here. The pipeline samples it at 2 fps,
          finds the shelf edges, segments each shelf into product facings by colour, and
          agrees them across frames at IoU 0.5.{" "}
          <strong>It reads geometry and colour, not products</strong> — brands, names, prices
          and promotions are not observable from video and are written as unknown rather than
          guessed.
        </div>
      </header>

      <div style={{ ...style.panel, display: "flex", gap: 14, flexWrap: "wrap", alignItems: "center" }}>
        <label style={{ ...style.note, display: "flex", gap: 10, alignItems: "center" }}>
          <span>Aisle clip</span>
          <input
            data-testid="vision-file"
            type="file"
            accept="video/*"
            style={{ ...style.tab, padding: "7px 9px", cursor: "pointer" }}
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file !== undefined) void read(file);
            }}
          />
        </label>
        {state.status === "reading" && (
          <span style={style.note}>Reading {state.filename}…</span>
        )}
      </div>

      {state.status === "error" && (
        <div data-testid="vision-error" style={style.alertBox}>
          {state.detail}
        </div>
      )}

      {state.status === "ready" && <Result reading={state.value} />}
    </div>
  );
}

// ---------------------------------------------------------------------------

function Result({ reading }: { reading: Reading }) {
  const skus = new Map(reading.planogram.skus.map((sku) => [sku.sku_id, sku]));
  const bay = reading.planogram.bays[0];

  return (
    <div data-testid="vision-result" style={{ display: "grid", gap: 14, marginTop: 14 }}>
      <div style={style.panel}>
        <div style={style.panelHeading}>What was read</div>
        <div style={{ ...style.note, marginBottom: 12 }}>
          {reading.frames_sampled} frames · {bay?.shelves.length ?? 0} shelves ·{" "}
          {(bay?.shelves ?? []).reduce((n, shelf) => n + shelf.slots.length, 0)} facings ·
          source <code style={style.monoStyle}>{reading.planogram.source}</code>
        </div>

        <div style={{ display: "grid", gap: 8 }}>
          {(bay?.shelves ?? []).map((shelf) => (
            <div
              key={shelf.shelf_id}
              data-testid={`vision-shelf-${shelf.shelf_id}`}
              style={{
                display: "flex",
                gap: 10,
                alignItems: "stretch",
                padding: "8px 10px",
                borderRadius: 7,
                border: `1px solid ${style.PANEL_BORDER}`,
                background: "#171c24",
              }}
            >
              <div style={{ flex: "0 0 118px", ...style.note, alignSelf: "center" }}>
                {shelf.level.replace(/_/g, " ")}
              </div>
              {shelf.slots.length === 0 ? (
                // An empty shelf is a real planogram state, not a gap in the
                // reading, and it is what "move a SKU to eye level" moves into.
                <div style={{ ...style.note, alignSelf: "center", opacity: 0.55 }}>
                  empty — no facings found on this shelf
                </div>
              ) : (
                <div style={{ display: "flex", gap: 8, flex: 1, flexWrap: "wrap" }}>
                  {shelf.slots.map((slot) => (
                    <SlotChip
                      key={slot.slot_id}
                      slot={slot}
                      sku={slot.sku_id === null ? undefined : skus.get(slot.sku_id)}
                    />
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>

      <div style={style.panel}>
        <div style={style.panelHeading}>What it could not observe</div>
        <ul data-testid="vision-notes" style={{ margin: 0, paddingLeft: 20, ...style.note }}>
          {reading.notes.map((note, index) => (
            <li key={index} style={{ marginBottom: 4 }}>
              {note}
            </li>
          ))}
        </ul>
      </div>

      {/*
        Reading a video is not committing a store. Without this, a viewer would
        reasonably assume the shelf on screen is now part of the product and can
        be shopped, simulated and compared against the seed planogram.
      */}
      <div data-testid="vision-not-saved" style={style.cautionBox}>
        This reading was <strong>not</strong> saved. Nothing in the database changed and the
        simulator is still running the committed planogram. To keep it, run{" "}
        <code style={style.monoStyle}>
          python -m vision.pipeline --video &lt;clip&gt; --out data/planograms/video_aisle.json
        </code>{" "}
        and commit the result, so the file that gets used is one somebody chose.
      </div>
    </div>
  );
}

function SlotChip({ slot, sku }: { slot: Slot; sku?: Sku }) {
  const confidence = slot.confidence ?? 0;
  return (
    <div style={{ ...chip, borderColor: confidenceColour(confidence) }}>
      <div
        style={{
          width: 12,
          height: 26,
          borderRadius: 3,
          // The colour that was actually measured, shown as itself. It is the
          // one thing about an unidentified product that is real signal.
          background: sku === undefined ? "#333" : labToCss(sku.color_lab),
          flex: "0 0 auto",
        }}
      />
      <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: 12.5 }}>{sku?.name ?? "unidentified"}</div>
        <div style={{ ...style.note, fontSize: 11, fontFamily: style.mono }}>
          {/* Confidence on the face of it. A slot found once and a slot found in
              every frame must not look the same. */}
          confidence {(confidence * 100).toFixed(0)}%
        </div>
      </div>
    </div>
  );
}

function confidenceColour(confidence: number): string {
  if (confidence >= 0.7) return style.OK;
  if (confidence >= 0.4) return style.CHANGED;
  return style.ALERT;
}

const chip: CSSProperties = {
  display: "flex",
  gap: 8,
  alignItems: "center",
  padding: "5px 9px",
  borderRadius: 6,
  border: `1px solid ${style.PANEL_BORDER}`,
  background: "#12161d",
};
