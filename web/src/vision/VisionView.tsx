import { useCallback, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";
import type { FetchLike } from "@/ai/client";
import * as style from "@/ai/styles";
import { labToCss } from "@/store/palette";

/**
 * `#/vision` — drop in a clip, label what the camera could not read, keep it.
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
 *  * every product starts unidentified, because the pipeline reads geometry and
 *    colour and cannot read brands, names or prices;
 *  * per-slot confidence is on screen, because a slot seen once and a slot seen
 *    in every frame must not look alike;
 *  * a clip with no shelves produces the server's refusal, and never an empty
 *    store — a wall that became a planogram would be indistinguishable from a
 *    real shelf everywhere downstream.
 *
 * ## Why this screen grew a labelling step and a save button
 *
 * It used to end there, with "this was not saved" and an instruction to go and
 * run `python -m vision.pipeline` in a terminal. Two things were wrong with
 * that, and they had to be fixed together.
 *
 * **A video reading is unshoppable, and saving it would not have changed
 * that.** `vision/planogram.py` writes brand "unknown", category "unknown",
 * price 0 on every SKU — correctly; a classical pipeline cannot read any of the
 * four. But `sim/simulator.py` keeps a shopper active only while their goal
 * categories are unmet, and matches those goals against the store's own
 * category list, which on a video reading holds the single string "unknown".
 * Measured against the four committed policies in `data/cache/policies/`, a
 * nine-facing reading gives `path.stations_mean` **0.0** for loyalist, mission
 * and switcher — they never take a step — while browser walks the bay (the one
 * archetype allowed to shop with no goals) and buys nothing, because a purchase
 * needs a goal match too. Label those same nine facings with categories drawn
 * from the policies and all four walk and all four buy.
 *
 * The fix is not a model guessing brands off a blurry pack; that would put
 * invented products where measured ones are and every number downstream would
 * inherit the invention silently. The fix is the operator typing the eight rows
 * they already have in their ERP. Hence the labelling panel — and hence the
 * categories coming from `shoppable_categories` on the server's own response,
 * which is the union of `goal_categories` over every persona's committed
 * policy. A category outside that set is a word no persona is going after, and
 * this screen says so *before* someone types one rather than after they wonder
 * why their store is deserted.
 *
 * **Saving stays a deliberate, labelled act.** `POST /vision/planogram` still
 * returns `saved: false` and still writes nothing; the reason for that is still
 * good. What changed is that a person can now press a button, and what that
 * button writes is marked as what it is:
 *
 *  * a `video_`-prefixed, run-stamped `planogram_id`, which cannot collide with
 *    the hand-authored seed store and cannot silently overwrite the last clip
 *    somebody uploaded (`POST /planograms` upserts);
 *  * `source: "video"` carried through untouched, so the document never stops
 *    declaring where it came from;
 *  * a document `name` that counts how many of its facings a human described;
 *  * per-SKU names — "operator-labelled product 3" against "unidentified
 *    product 3" — so a reader can tell, row by row, what the camera measured
 *    from what a person typed. A field left blank stays "unknown" or 0. The
 *    screen never fills a gap on the operator's behalf.
 *
 * Then a zero-patch variant on that planogram, because the store route resolves
 * variants and never planograms, and the shortest honest bridge from "this is
 * what was read" to "shop it" is a variant that changes nothing at all.
 */

export interface VisionViewProps {
  fetchImpl?: FetchLike;
}

const defaultFetch: FetchLike = (input, init) => fetch(input, init);

const JSON_HEADERS = { "Content-Type": "application/json" };

interface Slot {
  slot_id: string;
  sku_id: string | null;
  facings: number;
  x_m: number;
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
  brand: string;
  category: string;
  price: number;
  promo: boolean;
  color_lab: number[];
}

interface Creative {
  creative_id: string;
  brand: string;
  texture_url: string;
}

interface Planogram {
  planogram_id: string;
  name: string;
  source: string;
  bays: Array<{ bay_id: string; shelves: Shelf[] }>;
  skus: Sku[];
  creatives: Creative[];
}

interface Reading {
  planogram: Planogram;
  frames_sampled: number;
  notes: string[];
  saved: boolean;
  /**
   * Optional on the wire on purpose. An API that predates this field answers a
   * reading the screen can still draw and still save; the operator loses a
   * dropdown, not the feature.
   */
  shoppable_categories?: string[];
}

type Load =
  | { status: "idle" }
  | { status: "reading"; filename: string }
  | { status: "error"; detail: string }
  | { status: "ready"; value: Reading; filename: string };

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
        setState({ status: "ready", value, filename: file.name });
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
          guessed. You fill those in below, and then the shelf is shoppable.
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

      {state.status === "ready" && (
        <Result
          // A new clip is a new reading: the labels typed against the last one
          // describe packs that are no longer on screen, and carrying them over
          // would attach a person's typing to a facing they never looked at.
          key={state.filename + String(state.value.frames_sampled)}
          reading={state.value}
          filename={state.filename}
          fetchImpl={fetchImpl}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// What an operator can type, and what it means when they do not
// ---------------------------------------------------------------------------

/**
 * One facing's row, exactly as typed. Strings rather than parsed values: a
 * half-typed price is a state a person passes through, and turning "2." into 2
 * mid-keystroke would fight them.
 */
interface Label {
  category: string;
  brand: string;
  price: string;
  promo: boolean;
}

const BLANK: Label = { category: "", brand: "", price: "", promo: false };

/** The typed price, or null for "they did not say" — which is not the same as 0. */
function typedPrice(label: Label): number | null {
  const trimmed = label.price.trim();
  if (trimmed === "") return null;
  const value = Number(trimmed);
  return Number.isFinite(value) && value >= 0 ? value : null;
}

function touched(label: Label): boolean {
  return (
    label.category !== "" ||
    label.brand.trim() !== "" ||
    typedPrice(label) !== null ||
    label.promo
  );
}

/**
 * "unidentified product 3" → "operator-labelled product 3".
 *
 * The per-row provenance marker, and it has to live in a field the schema
 * already has: `planogram.schema.json` sets `additionalProperties: false` on a
 * SKU, so there is nowhere to hang a flag. `name` is the field a person reads,
 * and this is the sentence they need it to say.
 */
function operatorName(name: string): string {
  const prefix = "unidentified ";
  return name.startsWith(prefix)
    ? `operator-labelled ${name.slice(prefix.length)}`
    : `${name} (operator-labelled)`;
}

/**
 * A planogram id that says what it is and collides with nothing.
 *
 * `POST /planograms` upserts on the id, so a constant would mean the second
 * clip anybody uploads silently replaces the first — labels and all. The clip's
 * own name is in there because that is what the operator has on disk, and the
 * timestamp and nonce because two clips can share a name and a second.
 */
function readingId(filename: string): string {
  const slug =
    filename
      .replace(/\.[^.]+$/, "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "_")
      .replace(/^_+|_+$/g, "")
      .slice(0, 24) || "clip";
  const stamp = new Date().toISOString().replace(/[-:T]/g, "").slice(0, 14);
  const nonce = Math.random().toString(36).slice(2, 6);
  return `video_${slug}_${stamp}_${nonce}`;
}

/** The document a reader meets months later, with the ratio in its own name. */
function labelledPlanogram(
  reading: Reading,
  labels: Record<string, Label>,
  planogramId: string,
  advert: Advert = NO_ADVERT,
): Planogram {
  const skus = reading.planogram.skus.map((sku) => {
    const label = labels[sku.sku_id] ?? BLANK;
    if (!touched(label)) return sku;
    const price = typedPrice(label);
    const brand = label.brand.trim();
    return {
      ...sku,
      name: operatorName(sku.name),
      // Each field falls back to what the pipeline wrote. A row where somebody
      // set a category and skipped the brand keeps brand "unknown", because
      // that is still true and inventing one here would be the pipeline's
      // original sin committed one layer higher up.
      brand: brand === "" ? sku.brand : brand,
      category: label.category === "" ? sku.category : label.category,
      price: price === null ? sku.price : price,
      promo: label.promo,
    };
  });

  const described = reading.planogram.skus.filter((sku) =>
    touched(labels[sku.sku_id] ?? BLANK),
  ).length;
  const name =
    described === 0
      ? reading.planogram.name
      : `Aisle read from video, ${described} of ${skus.length} products labelled by an ` +
        "operator — positions, sizes and colours measured from the clip; brand, category, " +
        "price and promotion typed by hand";

  // No pack shot was filmed and none is invented, exactly as for the SKUs.
  // `texture_url: ""` is what `ProductSlot`/`AdSlot` read as "draw this as a
  // plain fixture", so an unillustrated creative renders rather than throwing.
  const creatives = advertised(advert)
    ? [
        {
          creative_id: OPERATOR_CREATIVE_ID,
          brand: advert.brand.trim(),
          texture_url: "",
        },
      ]
    : reading.planogram.creatives;

  return { ...reading.planogram, planogram_id: planogramId, name, skus, creatives };
}

/**
 * The advertising half of "what only you can say".
 *
 * `vision/planogram.py` emits `creatives: []` beside `ad_slots: []` and is
 * right to: it detects no signage, and placing a creative nobody filmed would
 * fabricate the exact variable the whole experiment manipulates. The variant
 * schema's `add_ad_slot` can build the fixture, but `set_ad_creative` books it
 * against a `creative_id` the planogram must already carry — and nothing here
 * could put one there, so an ad lift on a video-read shelf needed a creative
 * pasted into the document by hand.
 *
 * `brand` alone is a creative and no fixture: the store carries a poster
 * nobody hung, which is a legitimate thing to save and simulate against later.
 * `brand` **and** `shelf` is a creative plus a talker on that shelf, booked.
 * `shelf` alone books nothing, because a fixture carrying nothing is a
 * measurable object with no creative in it and the screen has no way to know
 * which brand was meant.
 */
interface Advert {
  brand: string;
  shelf: string;
}

const NO_ADVERT: Advert = { brand: "", shelf: "" };

/** The operator named a brand, so there is a creative to save. */
function advertised(advert: Advert): boolean {
  return advert.brand.trim() !== "";
}

const OPERATOR_CREATIVE_ID = "V_AD_1";
const OPERATOR_AD_SLOT_ID = "V_TALKER_1";

/**
 * The patches that install the operator's fixture and book it.
 *
 * Two patches rather than one, in that order, because that is what the ops
 * mean: `add_ad_slot` hangs an empty holder and `set_ad_creative` puts a
 * poster in it. Splitting them keeps one validation path for "is this a
 * creative the planogram carries" — `api/app/resolve.py` already refuses a
 * creative_id the document does not have, and it refuses it the same way
 * whether the variant came from this screen or from `data/variants/`.
 */
function advertPatches(advert: Advert): Array<Record<string, unknown>> {
  if (!advertised(advert) || advert.shelf === "") return [];
  return [
    {
      op: "add_ad_slot",
      ad_slot_id: OPERATOR_AD_SLOT_ID,
      type: "shelf_talker",
      attached_to: advert.shelf,
      // A talker runs along the front of the shelf it is attached to. Left
      // edge, and a width the bay can hold: `vision/planogram.py` fixes the
      // bay at 1.2 m, and this is the only ad geometry nobody measured, so it
      // is a stated default rather than a reading.
      x_m: 0.1,
      width_m: 0.4,
    },
    {
      op: "set_ad_creative",
      ad_slot_id: OPERATOR_AD_SLOT_ID,
      creative_id: OPERATOR_CREATIVE_ID,
    },
  ];
}

type Save =
  | { status: "unsaved" }
  | { status: "saving" }
  /** `planogramId` non-null means the store landed and the variant did not. */
  | { status: "failed"; detail: string; planogramId: string | null }
  | { status: "saved"; planogramId: string; variantId: string };

/** The server's own sentence, or something that at least names the status code. */
async function detailOf(response: Response): Promise<string> {
  const raw = await response.text().catch(() => "");
  try {
    const parsed = JSON.parse(raw) as { detail?: unknown };
    if (typeof parsed.detail === "string" && parsed.detail.length > 0) return parsed.detail;
  } catch {
    // not the JSON envelope; fall through
  }
  const trimmed = raw.trim().slice(0, 200);
  return `${response.status} ${response.statusText}${trimmed === "" ? "" : ` — ${trimmed}`}`;
}

// ---------------------------------------------------------------------------

function Result({
  reading,
  filename,
  fetchImpl,
}: {
  reading: Reading;
  filename: string;
  fetchImpl: FetchLike;
}) {
  const [labels, setLabels] = useState<Record<string, Label>>({});
  const [advert, setAdvert] = useState<Advert>(NO_ADVERT);
  const [save, setSave] = useState<Save>({ status: "unsaved" });
  // The id is drawn once for this reading rather than per render, so what is
  // shown on screen is what will be POSTed.
  const [planogramId] = useState(() => readingId(filename));
  const inFlight = useRef(false);

  const bay = reading.planogram.bays[0];
  const shelves = useMemo(() => bay?.shelves ?? [], [bay]);
  const skus = useMemo(
    () => new Map(reading.planogram.skus.map((sku) => [sku.sku_id, sku])),
    [reading],
  );
  const categories = reading.shoppable_categories ?? [];

  /** Every occupied slot, top shelf first, with the shelf it stands on. */
  const facings = useMemo(
    () =>
      shelves.flatMap((shelf) =>
        shelf.slots
          .filter((slot) => slot.sku_id !== null)
          .map((slot) => ({ slot, level: shelf.level })),
      ),
    [shelves],
  );

  /**
   * The shelves a talker can hang on: the ones the camera actually found.
   *
   * Offered as a list rather than typed, because `add_ad_slot` resolves the
   * owning bay from `attached_to` and refuses an id no bay or shelf carries -
   * so a typo here would come back as a rejected variant after the planogram
   * had already been saved, which is the one failure state this flow cannot
   * cleanly undo.
   */
  const shelfIds = useMemo(() => shelves.map((shelf) => shelf.shelf_id), [shelves]);

  const anyCategory = Object.values(labels).some((label) => label.category !== "");

  const setLabel = (skuId: string, patch: Partial<Label>) =>
    setLabels((current) => ({
      ...current,
      [skuId]: { ...(current[skuId] ?? BLANK), ...patch },
    }));

  const keep = async () => {
    // A ref, not the state: two clicks in one tick share a closure, and the
    // second would post a second planogram from the same clip.
    if (inFlight.current) return;
    inFlight.current = true;
    setSave({ status: "saving" });

    const document = labelledPlanogram(reading, labels, planogramId, advert);
    const patches = advertPatches(advert);
    const variantId = `${planogramId}_asread`;

    // What is on disk, tracked as it happens rather than inferred afterwards.
    // Two writes and no transaction, and only two of the three ways this can
    // fail carry a status code: a refusal answers with a `Response` and the
    // branch that reads it knows exactly how far the save got, but a dropped
    // connection, an aborted request or an API restarted between the POSTs
    // throws, and the `catch` below is handed no response to reason from. It
    // used to assume nothing had landed, which is right for a throw on the
    // first write and a lie about the second — the planogram is stored, and
    // the operator is told the database is untouched. This flag is the only
    // thing either branch needs to tell those two apart.
    let planogramStored = false;

    try {
      const stored = await fetchImpl("/api/planograms", {
        method: "POST",
        headers: JSON_HEADERS,
        body: JSON.stringify(document),
      });
      if (!stored.ok) {
        inFlight.current = false;
        setSave({ status: "failed", detail: await detailOf(stored), planogramId: null });
        return;
      }
      planogramStored = true;

      const variant = await fetchImpl("/api/variants", {
        method: "POST",
        headers: JSON_HEADERS,
        body: JSON.stringify({
          variant_id: variantId,
          base_planogram_id: planogramId,
          name:
            patches.length === 0
              ? "As read from video — nothing moved"
              : `As read from video, with a shelf talker for ${advert.brand.trim()} placed ` +
                "by the operator — no signage was detected in the clip",
          patches,
        }),
      });
      if (!variant.ok) {
        // Two writes, no transaction. "Nothing was saved" would be a lie the
        // operator discovers the next time they list /planograms.
        inFlight.current = false;
        setSave({
          status: "failed",
          detail: await detailOf(variant),
          planogramId,
        });
        return;
      }

      setSave({ status: "saved", planogramId, variantId });
    } catch (error) {
      inFlight.current = false;
      setSave({
        status: "failed",
        detail: error instanceof Error ? error.message : String(error),
        // Not a hardcoded null. A throw out of the *second* fetch leaves the
        // planogram on disk under an id nothing else on this screen can now
        // reconstruct, and the half-state message below is the only place the
        // operator will ever see it.
        planogramId: planogramStored ? planogramId : null,
      });
    }
  };

  return (
    <div data-testid="vision-result" style={{ display: "grid", gap: 14, marginTop: 14 }}>
      <div style={style.panel}>
        <div style={style.panelHeading}>What was read</div>
        <div style={{ ...style.note, marginBottom: 12 }}>
          {reading.frames_sampled} frames · {shelves.length} shelves ·{" "}
          {shelves.reduce((n, shelf) => n + shelf.slots.length, 0)} facings · source{" "}
          <code style={style.monoStyle}>{reading.planogram.source}</code>
        </div>

        <div style={{ display: "grid", gap: 8 }}>
          {shelves.map((shelf) => (
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

      <div style={style.panel}>
        <div style={style.panelHeading}>What only you can say</div>
        <div data-testid="vision-category-note" style={{ ...style.note, marginBottom: 12, maxWidth: 900 }}>
          The camera measured where each pack is, how wide it is and what colour it is. It
          could not read what any of them <em>are</em>, so type it — this is the eight rows
          you already have in your ERP, not a guess anybody has to make.{" "}
          <strong>
            The categories offered are the only ones any persona is going after
          </strong>{" "}
          — the union of <code style={style.monoStyle}>goal_categories</code> over every
          committed policy. Give a facing anything else and no persona will walk to it:{" "}
          <code style={style.monoStyle}>sim/simulator.py</code> matches a shopper's goals
          against the store's own category list and stops shopping when none of them can be
          met. Leave a field blank and it stays as the camera left it — “unknown”, or 0 —
          which is what tells a later reader which half of this document a person wrote.
        </div>

        {!anyCategory && (
          <div data-testid="vision-unshopped" style={style.cautionBox}>
            Every category here is still <code style={style.monoStyle}>unknown</code>, and a
            store of unknowns is a store nobody shops. Measured against the four committed
            policies: loyalist, mission and switcher record{" "}
            <code style={style.monoStyle}>stations_mean 0.0</code> — they never take a step —
            and browser walks the bay but buys nothing, because a purchase needs a goal match
            too. Set at least one category and the panel starts moving.
          </div>
        )}

        <div style={{ display: "grid", gap: 8, marginTop: 12 }}>
          {facings.map(({ slot, level }) => {
            const skuId = slot.sku_id as string;
            const sku = skus.get(skuId);
            return (
              <LabelRow
                key={skuId}
                skuId={skuId}
                sku={sku}
                slot={slot}
                level={level}
                categories={categories}
                label={labels[skuId] ?? BLANK}
                disabled={save.status === "saving" || save.status === "saved"}
                onChange={(patch) => setLabel(skuId, patch)}
              />
            );
          })}
        </div>
      </div>

      <div style={style.panel}>
        <div style={style.panelHeading}>Advertising, if you are testing any</div>
        <div data-testid="vision-ad-note" style={{ ...style.note, marginBottom: 12, maxWidth: 900 }}>
          The camera detected <strong>no signage</strong>, and the pipeline will not invent any —
          placing a creative nobody filmed would fabricate the exact thing an ad test measures.
          So this is the other half of what only you can say: name the brand being advertised and
          the shelf its talker hangs on. Both are recorded as <em>operator-placed</em>, in the
          variant's own name, and the store is saved with a creative you declared rather than one
          anybody read off the clip. Leave the brand blank and nothing is added: the shelf is saved
          exactly as read, with no fixture and no ad lift to compute.
        </div>

        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "flex-end" }}>
          <label style={{ display: "grid", gap: 4 }}>
            <span style={style.note}>Brand advertised</span>
            <input
              data-testid="vision-ad-brand"
              style={{ ...style.tab, flex: "0 0 170px" }}
              value={advert.brand}
              placeholder="Crunch"
              disabled={save.status === "saving" || save.status === "saved"}
              onChange={(event) =>
                setAdvert((current) => ({ ...current, brand: event.target.value }))
              }
            />
          </label>
          <label style={{ display: "grid", gap: 4 }}>
            <span style={style.note}>Shelf talker on</span>
            <select
              data-testid="vision-ad-shelf"
              style={{ ...style.tab, flex: "0 0 170px" }}
              value={advert.shelf}
              disabled={save.status === "saving" || save.status === "saved"}
              onChange={(event) =>
                setAdvert((current) => ({ ...current, shelf: event.target.value }))
              }
            >
              <option value="">nowhere — save the creative only</option>
              {shelfIds.map((shelfId) => (
                <option key={shelfId} value={shelfId}>
                  {shelfId}
                </option>
              ))}
            </select>
          </label>
        </div>

        {advert.shelf !== "" && !advertised(advert) && (
          <div data-testid="vision-ad-nobrand" style={{ ...style.cautionBox, marginTop: 12 }}>
            A fixture with nothing in it is a holder, not an advertisement, and this screen has no
            way to know which brand you meant. Name the brand and the talker is installed and
            booked; leave it blank and neither is saved.
          </div>
        )}
      </div>

      {(save.status === "unsaved" ||
        save.status === "saving" ||
        (save.status === "failed" && save.planogramId === null)) && (
        /*
          Reading a video is not committing a store. Without this, a viewer would
          reasonably assume the shelf on screen is now part of the product and can
          be shopped, simulated and compared against the seed planogram.
        */
        <div data-testid="vision-not-saved" style={style.cautionBox}>
          This reading was <strong>not</strong> saved. Nothing in the database changed and the
          simulator is still running the committed planogram. Label the facings above, then{" "}
          <strong>Keep this reading</strong> — it writes a planogram of its own under{" "}
          <code style={style.monoStyle}>{planogramId}</code>, a <code style={style.monoStyle}>video_</code>{" "}
          id that can never be mistaken for, or overwrite, a store somebody measured by hand.
        </div>
      )}

      {save.status === "failed" && (
        <div data-testid="vision-save-error" style={style.alertBox}>
          {save.planogramId === null ? (
            <>Nothing was saved: {save.detail}</>
          ) : (
            <>
              The planogram <code style={style.monoStyle}>{save.planogramId}</code> was saved,
              but the variant on it was not, so there is nothing to shop yet: {save.detail}
            </>
          )}
        </div>
      )}

      {save.status === "saved" && (
        <div data-testid="vision-saved" style={style.panel}>
          <div style={style.panelHeading}>Saved</div>
          <div style={style.note}>
            Planogram <code style={style.monoStyle}>{save.planogramId}</code> and variant{" "}
            <code style={style.monoStyle}>{save.variantId}</code> are in the database. The
            variant changes nothing, so what gets shopped is exactly what the camera read and
            you labelled. It carries <code style={style.monoStyle}>source: "video"</code> and a
            name that counts how many facings a person described, so it never reads as a
            hand-measured store.
          </div>
          <div style={{ display: "flex", gap: 8, marginTop: 12, flexWrap: "wrap" }}>
            <a
              data-testid="vision-open-store"
              style={style.linkButton}
              href={`/?variant=${encodeURIComponent(save.variantId)}`}
            >
              Shop this shelf
            </a>
            <a
              data-testid="vision-rehearse-store"
              style={{ ...style.linkButton, borderColor: style.CHANGED, background: "#2a2415" }}
              href={`/?variant=${encodeURIComponent(save.variantId)}&skip_capture=1`}
            >
              Skip the webcam setup
            </a>
          </div>
        </div>
      )}

      {(save.status === "unsaved" || save.status === "failed") && (
        <div>
          <button
            data-testid="vision-keep"
            type="button"
            style={style.primaryButton}
            onClick={() => void keep()}
          >
            Keep this reading
          </button>
        </div>
      )}
      {save.status === "saving" && (
        <div>
          <button data-testid="vision-keeping" type="button" disabled style={style.disabledButton}>
            Saving…
          </button>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------

function LabelRow({
  skuId,
  sku,
  slot,
  level,
  categories,
  label,
  disabled,
  onChange,
}: {
  skuId: string;
  sku?: Sku;
  slot: Slot;
  level: string;
  categories: string[];
  label: Label;
  disabled: boolean;
  onChange: (patch: Partial<Label>) => void;
}) {
  const confidence = slot.confidence ?? 0;
  return (
    <div
      data-testid={`vision-label-${skuId}`}
      style={{
        display: "flex",
        gap: 10,
        flexWrap: "wrap",
        alignItems: "center",
        padding: "8px 10px",
        borderRadius: 7,
        border: `1px solid ${style.PANEL_BORDER}`,
        background: "#171c24",
      }}
    >
      <div
        style={{
          width: 12,
          height: 30,
          borderRadius: 3,
          // The measured colour, shown as itself: the one real thing about a
          // facing whose identity is about to be typed in from somewhere else.
          background: sku === undefined ? "#333" : labToCss(sku.color_lab),
          flex: "0 0 auto",
        }}
      />
      <div style={{ flex: "0 0 190px", minWidth: 0 }}>
        <div style={{ ...style.monoStyle, fontSize: 12.5 }}>{slot.slot_id}</div>
        <div style={{ ...style.note, fontSize: 11 }}>
          {level.replace(/_/g, " ")} · {slot.x_m.toFixed(2)} m across ·{" "}
          {/* A weak read and a strong one must not look alike while somebody is
              deciding how much to trust the row they are describing. */}
          confidence {(confidence * 100).toFixed(0)}%
        </div>
      </div>

      <select
        data-testid={`vision-category-${skuId}`}
        aria-label={`category for ${slot.slot_id}`}
        value={label.category}
        disabled={disabled}
        style={{ ...style.tab, flex: "0 0 170px" }}
        onChange={(event) => onChange({ category: event.target.value })}
      >
        <option value="">unknown — nobody shops it</option>
        {categories.map((category) => (
          <option key={category} value={category}>
            {category}
          </option>
        ))}
      </select>

      <input
        data-testid={`vision-brand-${skuId}`}
        aria-label={`brand for ${slot.slot_id}`}
        type="text"
        placeholder="brand"
        value={label.brand}
        disabled={disabled}
        style={{ ...style.tab, flex: "0 0 150px" }}
        onChange={(event) => onChange({ brand: event.target.value })}
      />

      <input
        data-testid={`vision-price-${skuId}`}
        aria-label={`price for ${slot.slot_id}`}
        type="number"
        min="0"
        step="0.01"
        placeholder="price"
        value={label.price}
        disabled={disabled}
        style={{ ...style.tab, flex: "0 0 110px" }}
        onChange={(event) => onChange({ price: event.target.value })}
      />

      <label style={{ ...style.note, display: "flex", gap: 6, alignItems: "center" }}>
        <input
          data-testid={`vision-promo-${skuId}`}
          aria-label={`on promotion at ${slot.slot_id}`}
          type="checkbox"
          checked={label.promo}
          disabled={disabled}
          onChange={(event) => onChange({ promo: event.target.checked })}
        />
        on promo
      </label>
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
