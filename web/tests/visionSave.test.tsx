import { describe, expect, it } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { FetchLike } from "@/ai/client";
import { VisionView } from "@/vision/VisionView";

/**
 * `#/vision` — labelling a reading, and keeping it.
 *
 * Until this file existed, `#/vision` ended in a dead end: it drew the shelf it
 * had read, said "this was not saved", and told the operator to go and run
 * `python -m vision.pipeline` in a terminal. So "upload a video, shop the shelf
 * it read" was true of the repository and false of the product. Two things were
 * missing, and they are separate problems that had to be solved together.
 *
 * **One: a video reading is unshoppable, and no amount of saving fixes that.**
 * `vision/planogram.py` writes every SKU as brand "unknown", category
 * "unknown", price 0 — correctly, because a classical CV pipeline cannot read
 * any of the four off a frame. The cost is that the synthetic panel does
 * literally nothing on a video store. Measured, not assumed: running the four
 * committed policies in `data/cache/policies/` against a nine-facing reading
 * gives `path.stations_mean` 0.0 for loyalist, mission and switcher — they
 * never take a single step, because `sim/simulator.py` keeps a shopper active
 * only while their goals are unmet and every goal category is matched against
 * the store's category list, which on a video reading holds one string,
 * "unknown". Browser walks the bay (the one archetype allowed to shop with no
 * goals) and buys nothing, because a purchase needs a goal match too. Label the
 * same nine facings with categories from those policies and all four walk and
 * all four buy.
 *
 * The honest fix is not a model guessing brands off a blurry pack; it is the
 * operator typing the eight rows they already have in their ERP. Hence the
 * labelling step asserted below, and hence the category vocabulary coming from
 * the server rather than from a list in this file — a word outside what the
 * personas are going after is a word the simulator will ignore, and the screen
 * has to say so *before* someone types it.
 *
 * **Two: saving must stay a deliberate, labelled act.** `POST /vision/planogram`
 * still returns `saved: false` and still writes nothing; the reason it does is
 * still good. An upload that quietly added a store would put a video's guesses
 * next to hand-authored evidence with nothing telling them apart. So what these
 * tests pin is not "uploading saves" but "a person can save, and what lands is
 * marked": a `video_`-prefixed id that cannot collide with the seed store, the
 * `source: "video"` the pipeline set, a document name that counts how many rows
 * a human typed, and per-SKU names that distinguish a facing the camera merely
 * measured from one an operator described. The "not saved" caution stays on
 * screen, word for word, until a save actually succeeds.
 */

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

/** Three facings across two shelves — enough for "1 of 3" to mean something. */
const PLANOGRAM = {
  planogram_id: "video_aisle",
  name: "Aisle read from video — products not identified, prices unknown",
  source: "video",
  bays: [
    {
      bay_id: "V1",
      type: "shelf",
      width_m: 1.2,
      height_m: 1.8,
      station: { camera_pos: [0, 1.5, 2.2], look_at: [0, 1.2, 0] },
      ad_slots: [],
      shelves: [
        {
          shelf_id: "V1S1",
          height_m: 1.4,
          level: "eye",
          slots: [
            {
              slot_id: "V1S1P1",
              sku_id: "V_001",
              facings: 1,
              x_m: 0.05,
              width_m: 0.3,
              height_m: 0.24,
              confidence: 0.91,
            },
            {
              slot_id: "V1S1P2",
              sku_id: "V_002",
              facings: 1,
              x_m: 0.45,
              width_m: 0.3,
              height_m: 0.24,
              confidence: 0.42,
            },
          ],
        },
        {
          shelf_id: "V1S2",
          height_m: 0.9,
          level: "below_eye",
          slots: [
            {
              slot_id: "V1S2P1",
              sku_id: "V_003",
              facings: 1,
              x_m: 0.1,
              width_m: 0.34,
              height_m: 0.3,
              confidence: 0.77,
            },
          ],
        },
      ],
    },
  ],
  skus: [
    {
      sku_id: "V_001",
      name: "unidentified product 1",
      brand: "unknown",
      category: "unknown",
      price: 0,
      promo: false,
      texture_url: "",
      color_lab: [55, 12, -8],
    },
    {
      sku_id: "V_002",
      name: "unidentified product 2",
      brand: "unknown",
      category: "unknown",
      price: 0,
      promo: false,
      texture_url: "",
      color_lab: [70, -20, 30],
    },
    {
      sku_id: "V_003",
      name: "unidentified product 3",
      brand: "unknown",
      category: "unknown",
      price: 0,
      promo: false,
      texture_url: "",
      color_lab: [41, 3, 22],
    },
  ],
  creatives: [],
};

const RESPONSE = {
  planogram: PLANOGRAM,
  frames_sampled: 6,
  bands: [{ top: 20, bottom: 150 }],
  notes: [
    "6 frames sampled at 2 fps from aisle.mp4",
    "products are not identified: brand, name, price and promotion are not observable from video and are written as unknown rather than guessed",
  ],
  // The union of goal_categories over the four committed policies.
  shoppable_categories: ["biscuits", "chips", "cola", "nuts"],
  saved: false,
};

interface Call {
  url: string;
  method: string;
  body: unknown;
}

interface Harness {
  container: HTMLDivElement;
  calls: Call[];
  settle: () => Promise<void>;
  unmount: () => void;
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/**
 * What each POST should answer with. Absent means 201 and an echo.
 *
 * An `Error` rather than a `Response` makes that fetch *throw*, which is the
 * other half of how a save fails and is not the same code path as a refusal: a
 * dropped connection, an aborted request or an API restarted mid-save never
 * produces a `Response` at all, so the component learns nothing from a status
 * line and everything from which writes it had already got through.
 */
interface Outcomes {
  planograms?: Response | Error;
  variants?: Response | Error;
}

/** A stubbed outcome, delivered the way the real `fetch` would deliver it. */
function deliver(outcome: Response | Error): Response {
  if (outcome instanceof Error) throw outcome;
  return outcome;
}

async function mount(outcomes: Outcomes = {}): Promise<Harness> {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const calls: Call[] = [];

  const fetchImpl: FetchLike = async (input, init) => {
    const method = init?.method ?? "GET";
    let body: unknown = undefined;
    if (typeof init?.body === "string") body = JSON.parse(init.body);
    calls.push({ url: input, method, body });

    if (input.endsWith("/vision/planogram")) return json(200, RESPONSE);
    if (input.endsWith("/planograms")) {
      return outcomes.planograms === undefined
        ? json(201, body)
        : deliver(outcomes.planograms);
    }
    if (input.endsWith("/variants")) {
      return outcomes.variants === undefined ? json(201, {}) : deliver(outcomes.variants);
    }
    throw new Error(`unexpected request to ${input}`);
  };

  let root: Root | null = null;
  await act(async () => {
    root = createRoot(container);
    root.render(<VisionView fetchImpl={fetchImpl} />);
  });

  const settle = async () => {
    await act(async () => {
      for (let n = 0; n < 8; n += 1) await Promise.resolve();
    });
  };
  await settle();

  return {
    container,
    calls,
    settle,
    unmount: () => {
      act(() => root?.unmount());
      container.remove();
    },
  };
}

function node(harness: Harness, testId: string): HTMLElement {
  const found = harness.container.querySelector<HTMLElement>(`[data-testid="${testId}"]`);
  if (found === null) throw new Error(`no [data-testid="${testId}"]`);
  return found;
}

function maybe(harness: Harness, testId: string): HTMLElement | null {
  return harness.container.querySelector<HTMLElement>(`[data-testid="${testId}"]`);
}

function text(harness: Harness, testId: string): string {
  return maybe(harness, testId)?.textContent ?? "";
}

async function chooseFile(harness: Harness, name = "aisle-3.mp4"): Promise<void> {
  const input = node(harness, "vision-file") as HTMLInputElement;
  const file = new File([new Uint8Array([0, 1, 2, 3])], name, { type: "video/mp4" });
  Object.defineProperty(input, "files", { value: [file], configurable: true });
  await act(async () => {
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await harness.settle();
}

/**
 * Type into a controlled field the way a person does.
 *
 * React tracks the last value it wrote to a DOM node and swallows a change
 * event whose value it believes it already knows, so assigning `.value`
 * directly is silently ignored. Going through the prototype's setter updates
 * the node without touching React's tracker, which is what makes the dispatched
 * event look like a real keystroke.
 */
async function fill(
  harness: Harness,
  testId: string,
  value: string,
): Promise<void> {
  const element = node(harness, testId) as HTMLInputElement | HTMLSelectElement;
  const proto =
    element instanceof HTMLSelectElement
      ? HTMLSelectElement.prototype
      : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
  if (setter === undefined) throw new Error("no value setter on the prototype");
  await act(async () => {
    setter.call(element, value);
    element.dispatchEvent(new Event("input", { bubbles: true }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await harness.settle();
}

async function click(harness: Harness, testId: string): Promise<void> {
  const element = node(harness, testId);
  await act(async () => {
    element.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
  await harness.settle();
}

function posted(harness: Harness, path: string): Call {
  const call = harness.calls.find((c) => c.url.endsWith(path) && c.method === "POST");
  if (call === undefined) {
    throw new Error(
      `nothing was POSTed to ${path}; saw ${harness.calls
        .map((c) => `${c.method} ${c.url}`)
        .join(", ")}`,
    );
  }
  return call;
}

interface SavedSku {
  sku_id: string;
  name: string;
  brand: string;
  category: string;
  price: number;
  promo: boolean;
}

interface SavedPlanogram {
  planogram_id: string;
  name: string;
  source: string;
  skus: SavedSku[];
  bays: Array<{ shelves: Array<{ slots: Array<{ slot_id: string }> }> }>;
}

function savedPlanogram(harness: Harness): SavedPlanogram {
  return posted(harness, "/planograms").body as SavedPlanogram;
}

function sku(harness: Harness, skuId: string): SavedSku {
  const found = savedPlanogram(harness).skus.find((s) => s.sku_id === skuId);
  if (found === undefined) throw new Error(`no sku ${skuId} in the saved planogram`);
  return found;
}

/** The one full journey: label two of three facings, then keep the reading. */
async function labelAndSave(harness: Harness): Promise<void> {
  await chooseFile(harness);
  await fill(harness, "vision-category-V_001", "chips");
  await fill(harness, "vision-brand-V_001", "Crunch");
  await fill(harness, "vision-price-V_001", "2.49");
  await click(harness, "vision-promo-V_001");
  await fill(harness, "vision-category-V_002", "cola");
  await click(harness, "vision-keep");
}

// ---------------------------------------------------------------------------

describe("the labelling step", () => {
  it("offers a row for every facing the camera found", async () => {
    const harness = await mount();
    await chooseFile(harness);
    try {
      expect(maybe(harness, "vision-label-V_001")).not.toBeNull();
      expect(maybe(harness, "vision-label-V_002")).not.toBeNull();
      expect(maybe(harness, "vision-label-V_003")).not.toBeNull();
    } finally {
      harness.unmount();
    }
  });

  it("shows each row beside where the facing was and how well it was seen", async () => {
    // The operator is typing what a pack *is* while looking at a chip on a
    // screen. The slot and its confidence are the only things linking that row
    // back to the shelf they filmed.
    const harness = await mount();
    await chooseFile(harness);
    try {
      const row = text(harness, "vision-label-V_002");
      expect(row).toContain("V1S1P2");
      expect(row).toContain("42");
    } finally {
      harness.unmount();
    }
  });

  it("offers only the categories the personas actually shop", async () => {
    // Not a list in this file and not a list in the component: the server
    // unions goal_categories over the committed policies, so the dropdown
    // cannot drift away from what sim/simulator.py will honour.
    const harness = await mount();
    await chooseFile(harness);
    try {
      const select = node(harness, "vision-category-V_001") as HTMLSelectElement;
      const values = Array.from(select.options).map((option) => option.value);
      expect(values).toContain("chips");
      expect(values).toContain("nuts");
      expect(values.filter((v) => v.length > 0).sort()).toEqual([
        "biscuits",
        "chips",
        "cola",
        "nuts",
      ]);
    } finally {
      harness.unmount();
    }
  });

  it("says that a category outside that set will not be shopped by anyone", async () => {
    const harness = await mount();
    await chooseFile(harness);
    try {
      const said = text(harness, "vision-category-note").toLowerCase();
      expect(said).toContain("no persona");
    } finally {
      harness.unmount();
    }
  });

  it("warns while nothing is labelled that the panel will not move", async () => {
    // The measured failure this whole step exists for: three of four personas
    // record stations_mean 0.0 on an all-"unknown" store.
    const harness = await mount();
    await chooseFile(harness);
    try {
      expect(text(harness, "vision-unshopped").toLowerCase()).toContain("unknown");
    } finally {
      harness.unmount();
    }
  });

  it("stops warning once a category has been chosen", async () => {
    const harness = await mount();
    await chooseFile(harness);
    await fill(harness, "vision-category-V_001", "chips");
    try {
      expect(maybe(harness, "vision-unshopped")).toBeNull();
    } finally {
      harness.unmount();
    }
  });
});

describe("keeping the reading", () => {
  it("posts the planogram before the variant", async () => {
    const harness = await mount();
    await labelAndSave(harness);
    try {
      const posts = harness.calls.filter((c) => c.method === "POST").map((c) => c.url);
      expect(posts).toEqual([
        "/api/vision/planogram",
        "/api/planograms",
        "/api/variants",
      ]);
    } finally {
      harness.unmount();
    }
  });

  it("saves what the operator typed", async () => {
    const harness = await mount();
    await labelAndSave(harness);
    try {
      const labelled = sku(harness, "V_001");
      expect(labelled.category).toBe("chips");
      expect(labelled.brand).toBe("Crunch");
      expect(labelled.price).toBe(2.49);
      expect(labelled.promo).toBe(true);
    } finally {
      harness.unmount();
    }
  });

  it("leaves a facing nobody described exactly as the camera left it", async () => {
    // The distinction the whole feature rests on. An untouched row keeps the
    // pipeline's "unknown"/0/false, which is what a reader looks for to tell a
    // measurement from a person's typing.
    const harness = await mount();
    await labelAndSave(harness);
    try {
      const untouched = sku(harness, "V_003");
      expect(untouched.category).toBe("unknown");
      expect(untouched.brand).toBe("unknown");
      expect(untouched.price).toBe(0);
      expect(untouched.promo).toBe(false);
      expect(untouched.name).toBe("unidentified product 3");
    } finally {
      harness.unmount();
    }
  });

  it("keeps a field the operator skipped unknown even on a row they did touch", async () => {
    // V_002 got a category and nothing else. Filling its brand in with anything
    // would be the pipeline's original sin committed one layer higher up.
    const harness = await mount();
    await labelAndSave(harness);
    try {
      const partial = sku(harness, "V_002");
      expect(partial.category).toBe("cola");
      expect(partial.brand).toBe("unknown");
      expect(partial.price).toBe(0);
    } finally {
      harness.unmount();
    }
  });

  it("renames a labelled facing so a reader can see a person described it", async () => {
    const harness = await mount();
    await labelAndSave(harness);
    try {
      expect(sku(harness, "V_001").name).toBe("operator-labelled product 1");
    } finally {
      harness.unmount();
    }
  });

  it("says in the document's own name how many rows a person typed", async () => {
    // The one line a reader sees months later, beside the hand-authored seed,
    // in `GET /planograms` and in the launcher. It has to carry the ratio.
    const harness = await mount();
    await labelAndSave(harness);
    try {
      const name = savedPlanogram(harness).name;
      expect(name).toContain("2 of 3");
      expect(name.toLowerCase()).toContain("operator");
      expect(name.toLowerCase()).toContain("video");
    } finally {
      harness.unmount();
    }
  });

  it("keeps source video, so the document never stops declaring where it came from", async () => {
    const harness = await mount();
    await labelAndSave(harness);
    try {
      expect(savedPlanogram(harness).source).toBe("video");
    } finally {
      harness.unmount();
    }
  });

  it("saves under an id that cannot be mistaken for a hand-authored store", async () => {
    const harness = await mount();
    await labelAndSave(harness);
    try {
      const id = savedPlanogram(harness).planogram_id;
      expect(id.startsWith("video_")).toBe(true);
      expect(id).not.toBe("demo_aisle");
    } finally {
      harness.unmount();
    }
  });

  it("saves under an id that cannot overwrite a previous reading either", async () => {
    // POST /planograms upserts. A fixed id would mean the second clip anybody
    // uploads silently replaces the first, including the labels typed into it.
    const first = await mount();
    await labelAndSave(first);
    const firstId = savedPlanogram(first).planogram_id;
    first.unmount();

    const second = await mount();
    await labelAndSave(second);
    try {
      expect(savedPlanogram(second).planogram_id).not.toBe(firstId);
    } finally {
      second.unmount();
    }
  });

  it("keeps every slot the reading found", async () => {
    const harness = await mount();
    await labelAndSave(harness);
    try {
      const slots = savedPlanogram(harness)
        .bays.flatMap((bay) => bay.shelves)
        .flatMap((shelf) => shelf.slots)
        .map((slot) => slot.slot_id);
      expect(slots).toEqual(["V1S1P1", "V1S1P2", "V1S2P1"]);
    } finally {
      harness.unmount();
    }
  });

  it("creates a variant that changes nothing, on the store it just saved", async () => {
    // The store route resolves a variant, never a planogram. A zero-patch
    // variant is the shortest honest bridge: what gets shopped is exactly what
    // was read and labelled, with nothing moved on top of it.
    const harness = await mount();
    await labelAndSave(harness);
    try {
      const variant = posted(harness, "/variants").body as {
        variant_id: string;
        base_planogram_id: string;
        patches: unknown[];
      };
      expect(variant.base_planogram_id).toBe(savedPlanogram(harness).planogram_id);
      expect(variant.patches).toEqual([]);
      expect(variant.variant_id.length).toBeGreaterThan(0);
    } finally {
      harness.unmount();
    }
  });
});

describe("what the screen says about saving", () => {
  it("warns that nothing is saved until it is", async () => {
    const harness = await mount();
    await chooseFile(harness);
    try {
      expect(text(harness, "vision-not-saved").toLowerCase()).toContain("not");
      expect(maybe(harness, "vision-saved")).toBeNull();
    } finally {
      harness.unmount();
    }
  });

  it("replaces the warning with what was saved and under what id", async () => {
    const harness = await mount();
    await labelAndSave(harness);
    try {
      expect(maybe(harness, "vision-not-saved")).toBeNull();
      expect(text(harness, "vision-saved")).toContain(
        savedPlanogram(harness).planogram_id,
      );
    } finally {
      harness.unmount();
    }
  });

  it("links into the store for the variant it just created", async () => {
    const harness = await mount();
    await labelAndSave(harness);
    try {
      const variantId = (posted(harness, "/variants").body as { variant_id: string })
        .variant_id;
      const href = node(harness, "vision-open-store").getAttribute("href") ?? "";
      expect(href).toContain(`variant=${variantId}`);
    } finally {
      harness.unmount();
    }
  });

  it("keeps the warning, and shows the server's own words, when the save is refused", async () => {
    const harness = await mount({
      planograms: new Response(
        JSON.stringify({ detail: "invalid planogram: skus/0/price: 0 is not of type" }),
        { status: 422, headers: { "Content-Type": "application/json" } },
      ),
    });
    await labelAndSave(harness);
    try {
      expect(text(harness, "vision-save-error")).toContain("invalid planogram");
      expect(maybe(harness, "vision-not-saved")).not.toBeNull();
      expect(maybe(harness, "vision-saved")).toBeNull();
    } finally {
      harness.unmount();
    }
  });

  it("admits the half-done state when the store saved but the variant did not", async () => {
    // Two writes, no transaction. Reporting "nothing was saved" here would be a
    // lie the operator finds out about the next time they list /planograms, and
    // reporting success would send them to a store route that 404s.
    const harness = await mount({
      variants: new Response(JSON.stringify({ detail: "unknown base_planogram_id" }), {
        status: 404,
        headers: { "Content-Type": "application/json" },
      }),
    });
    await labelAndSave(harness);
    try {
      const said = text(harness, "vision-save-error");
      expect(said).toContain("unknown base_planogram_id");
      expect(said).toContain(savedPlanogram(harness).planogram_id);
      expect(maybe(harness, "vision-open-store")).toBeNull();
    } finally {
      harness.unmount();
    }
  });

  it("admits the same half-done state when the variant request never completes", async () => {
    // The identical half-state, arriving down the other path. A refusal comes
    // back as a `Response` and carries a status line; a dropped connection, an
    // aborted request or an API restarted between the two POSTs throws, and
    // there is no response to read anything off. What the operator is owed is
    // the same in both cases, because what is on disk is the same in both
    // cases: the planogram landed, the variant did not, and the id of the
    // planogram they now own is the one thing they cannot reconstruct from the
    // screen. Reporting this as "nothing was saved" is a lie they find out
    // about the next time they list /planograms — with a store they cannot
    // name sitting in it.
    const harness = await mount({
      variants: new TypeError("Failed to fetch"),
    });
    await labelAndSave(harness);
    try {
      const said = text(harness, "vision-save-error");
      expect(said).toContain("Failed to fetch");
      expect(said).toContain(savedPlanogram(harness).planogram_id);
      expect(said).not.toContain("Nothing was saved");
      // The caution that says the database is untouched is now false, so it
      // must be gone: half of this write is committed.
      expect(maybe(harness, "vision-not-saved")).toBeNull();
      expect(maybe(harness, "vision-open-store")).toBeNull();
      expect(maybe(harness, "vision-saved")).toBeNull();
    } finally {
      harness.unmount();
    }
  });

  it("still says nothing was saved when the first request never completes", async () => {
    // The other side of the same coin, and the reason the flag has to be set
    // from what actually happened rather than assumed either way: when the
    // planograms POST itself throws, nothing reached the database and the
    // "not saved" caution is exactly right. A fix that reported the half-state
    // unconditionally would send this operator hunting for a planogram that
    // does not exist.
    const harness = await mount({
      planograms: new TypeError("Failed to fetch"),
    });
    await labelAndSave(harness);
    try {
      const said = text(harness, "vision-save-error");
      expect(said).toContain("Nothing was saved");
      expect(said).toContain("Failed to fetch");
      expect(maybe(harness, "vision-not-saved")).not.toBeNull();
      expect(maybe(harness, "vision-saved")).toBeNull();
    } finally {
      harness.unmount();
    }
  });

  it("cannot be saved twice by a double click", async () => {
    // The button posts two documents. A second press mid-flight would create a
    // second planogram from the same clip and leave two ids on screen.
    const harness = await mount();
    await chooseFile(harness);
    await fill(harness, "vision-category-V_001", "chips");
    await click(harness, "vision-keep");
    const saveCalls = () =>
      harness.calls.filter((c) => c.url.endsWith("/planograms")).length;
    const after = saveCalls();
    try {
      expect(maybe(harness, "vision-keep")).toBeNull();
      expect(after).toBe(1);
    } finally {
      harness.unmount();
    }
  });
});

// ---------------------------------------------------------------------------
// The creative, and the fixture that carries it
//
// The labelling step closed the gap that stopped a video-read shelf being
// shopped. It left a second one: the shelf could be shopped and could sell
// things, and still could not produce an *ad* lift, because
// `vision/planogram.py` emits `creatives: []` next to `ad_slots: []` and
// refuses to invent either. `schemas/variant.schema.json` gained `add_ad_slot`,
// which builds the fixture — but `set_ad_creative` books it against a
// `creative_id` the planogram has to already carry, and nothing on this screen
// could put one there. So the end-to-end run needed a creative pasted into the
// document by hand, which is not a product.
//
// Both halves are operator-supplied and both say so. The camera detected no
// signage; a person said "there is a shelf talker here and it advertises
// Crunch". That is the same division of labour as the labels, and it is the
// honest one: a retailer knows where their own fixtures hang.
// ---------------------------------------------------------------------------

interface SavedCreative {
  creative_id: string;
  brand: string;
  texture_url: string;
}

function creatives(harness: Harness): SavedCreative[] {
  return (posted(harness, "/planograms").body as { creatives: SavedCreative[] }).creatives;
}

function patches(harness: Harness): Array<Record<string, unknown>> {
  return (posted(harness, "/variants").body as { patches: Array<Record<string, unknown>> })
    .patches;
}

/** Label as usual, then declare a creative and hang it on a shelf. */
async function labelAdvertiseAndSave(harness: Harness, shelf = "V1S1"): Promise<void> {
  await chooseFile(harness);
  await fill(harness, "vision-category-V_001", "chips");
  await fill(harness, "vision-brand-V_001", "Crunch");
  await fill(harness, "vision-ad-brand", "Crunch");
  await fill(harness, "vision-ad-shelf", shelf);
  await click(harness, "vision-keep");
}

describe("declaring a creative", () => {
  it("saves no creative and no fixture when the operator names neither", async () => {
    const harness = await mount();
    await labelAndSave(harness);
    try {
      expect(creatives(harness)).toEqual([]);
      expect(patches(harness)).toEqual([]);
    } finally {
      harness.unmount();
    }
  });

  it("puts the named brand on the planogram as a creative", async () => {
    const harness = await mount();
    await labelAdvertiseAndSave(harness);
    try {
      const [only, ...rest] = creatives(harness);
      expect(rest).toEqual([]);
      expect(only.brand).toBe("Crunch");
      expect(only.creative_id).toBeTruthy();
      // No pack shot was filmed and none is invented, exactly as for the SKUs.
      expect(only.texture_url).toBe("");
    } finally {
      harness.unmount();
    }
  });

  it("installs the fixture and books it, in that order", async () => {
    const harness = await mount();
    await labelAdvertiseAndSave(harness, "V1S2");
    try {
      const [install, book, ...rest] = patches(harness);
      expect(rest).toEqual([]);
      expect(install.op).toBe("add_ad_slot");
      expect(install.attached_to).toBe("V1S2");
      expect(install.type).toBe("shelf_talker");
      expect(book.op).toBe("set_ad_creative");
      expect(book.ad_slot_id).toBe(install.ad_slot_id);
      expect(book.creative_id).toBe(creatives(harness)[0].creative_id);
    } finally {
      harness.unmount();
    }
  });

  it("offers every shelf the camera actually found, and no others", async () => {
    const harness = await mount();
    await chooseFile(harness);
    try {
      const options = Array.from(
        node(harness, "vision-ad-shelf").querySelectorAll("option"),
      )
        .map((o) => (o as HTMLOptionElement).value)
        .filter((v) => v !== "");
      expect(options).toEqual(["V1S1", "V1S2"]);
    } finally {
      harness.unmount();
    }
  });

  it("declares in the variant's name that a person placed the ad", async () => {
    const harness = await mount();
    await labelAdvertiseAndSave(harness);
    try {
      const name = (posted(harness, "/variants").body as { name: string }).name;
      expect(name.toLowerCase()).toContain("operator");
    } finally {
      harness.unmount();
    }
  });

  it("will not book a fixture the operator did not name a brand for", async () => {
    const harness = await mount();
    await chooseFile(harness);
    await fill(harness, "vision-ad-shelf", "V1S1");
    await click(harness, "vision-keep");
    try {
      expect(creatives(harness)).toEqual([]);
      expect(patches(harness)).toEqual([]);
    } finally {
      harness.unmount();
    }
  });
});
