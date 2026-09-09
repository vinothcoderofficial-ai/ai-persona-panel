import { describe, expect, it } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { FetchLike } from "@/ai/client";
import { VisionView } from "@/vision/VisionView";

/**
 * `#/vision` — drop in a clip, get a shelf.
 *
 * `vision/` did not exist at all: the directory held two empty `__init__.py`
 * files, so "video → 3D store" was a claim with no code behind it. The pipeline
 * now exists and this is the screen that makes it usable without a terminal.
 *
 * Everything asserted below is about **not over-reading the result**, because
 * a planogram rendered on screen looks exactly as authoritative whether it came
 * from a careful hand-authored file or from six frames of a phone video:
 *
 *  * the products are shown as unidentified, because the pipeline cannot read
 *    brands, names or prices, and the screen must not let a viewer forget it;
 *  * per-slot confidence is visible, because a slot seen once and a slot seen
 *    twenty times must not look alike;
 *  * "this was not saved" is stated, not implied, and stays stated until a
 *    person deliberately saves it;
 *  * a clip with no shelves in it produces the server's refusal, never an
 *    empty store.
 *
 * The labelling step and the save that follows it live in
 * `visionSave.test.tsx`. This file stays about the reading itself: what the
 * screen draws, what it refuses to claim about it, and what it does when the
 * server says no.
 */

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

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
          height_m: 0.4,
          level: "top",
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
        { shelf_id: "V1S2", height_m: 0.4, level: "bottom", slots: [] },
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
    "no promotional signs are detected, so the planogram carries no ad slots",
  ],
  saved: false,
};

interface Harness {
  container: HTMLDivElement;
  settle: () => Promise<void>;
  unmount: () => void;
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

async function mount(
  opts: { status?: number; detail?: string; body?: unknown } = {},
): Promise<Harness> {
  const container = document.createElement("div");
  document.body.appendChild(container);

  const fetchImpl: FetchLike = async () => {
    if (opts.status !== undefined && opts.status !== 200) {
      return json(opts.status, { detail: opts.detail ?? "refused" });
    }
    return json(200, opts.body ?? RESPONSE);
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
    settle,
    unmount: () => {
      act(() => root?.unmount());
      container.remove();
    },
  };
}

function text(harness: Harness, testId: string): string {
  return harness.container.querySelector(`[data-testid="${testId}"]`)?.textContent ?? "";
}

function node(harness: Harness, testId: string): HTMLElement {
  const found = harness.container.querySelector<HTMLElement>(`[data-testid="${testId}"]`);
  if (found === null) throw new Error(`no [data-testid="${testId}"]`);
  return found;
}

/** Hand the file input a file, the way a person dropping a clip on it would. */
async function chooseFile(harness: Harness, name = "aisle.mp4"): Promise<void> {
  const input = node(harness, "vision-file") as HTMLInputElement;
  const file = new File([new Uint8Array([0, 1, 2, 3])], name, { type: "video/mp4" });
  Object.defineProperty(input, "files", { value: [file], configurable: true });
  await act(async () => {
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await harness.settle();
}

describe("before a clip is chosen", () => {
  it("says what kind of shot the pipeline reads", async () => {
    // The pipeline is classical CV and has real limits. Stating them up front
    // is cheaper for everyone than a refusal after the upload.
    const harness = await mount();
    try {
      expect(text(harness, "vision-intro").toLowerCase()).toContain("front-on");
    } finally {
      harness.unmount();
    }
  });

  it("shows no planogram yet", async () => {
    const harness = await mount();
    try {
      expect(harness.container.querySelector('[data-testid="vision-result"]')).toBeNull();
    } finally {
      harness.unmount();
    }
  });
});

describe("after a clip is read", () => {
  it("draws the shelves it found", async () => {
    const harness = await mount();
    await chooseFile(harness);
    try {
      expect(node(harness, "vision-shelf-V1S1")).not.toBeNull();
      expect(node(harness, "vision-shelf-V1S2")).not.toBeNull();
    } finally {
      harness.unmount();
    }
  });

  it("shows an empty shelf as empty rather than omitting it", async () => {
    // An empty shelf is a real planogram state and the thing "move a SKU to
    // eye level" moves something into.
    const harness = await mount();
    await chooseFile(harness);
    try {
      expect(text(harness, "vision-shelf-V1S2").toLowerCase()).toContain("empty");
    } finally {
      harness.unmount();
    }
  });

  it("says the products were not identified", async () => {
    const harness = await mount();
    await chooseFile(harness);
    try {
      const result = text(harness, "vision-result").toLowerCase();
      expect(result).toContain("unidentified");
    } finally {
      harness.unmount();
    }
  });

  it("shows each slot's confidence, so a weak read does not look like a strong one", async () => {
    const harness = await mount();
    await chooseFile(harness);
    try {
      const shelf = text(harness, "vision-shelf-V1S1");
      expect(shelf).toContain("91");
      expect(shelf).toContain("42");
    } finally {
      harness.unmount();
    }
  });

  it("prints the pipeline's notes about what it could not observe", async () => {
    const harness = await mount();
    await chooseFile(harness);
    try {
      const notes = text(harness, "vision-notes").toLowerCase();
      expect(notes).toContain("not identified");
      expect(notes).toContain("no promotional signs");
    } finally {
      harness.unmount();
    }
  });

  it("states that nothing was saved, and points at the button that would save it", async () => {
    // Reading a video is not committing a store. Left implicit, a viewer would
    // reasonably assume the shelf they are looking at is now in the product.
    //
    // This used to end by telling the operator to go and run
    // `python -m vision.pipeline` in a terminal, which was true and useless:
    // the screen had read the clip already, and the only thing standing between
    // that reading and a shoppable store was a person deciding to keep it. The
    // caution now names the act and the id it would land under, and it stays
    // on screen until that act succeeds — `visionSave.test.tsx` holds the rest.
    const harness = await mount();
    await chooseFile(harness);
    try {
      expect(text(harness, "vision-not-saved").toLowerCase()).toContain("not");
      expect(text(harness, "vision-not-saved")).toContain("Keep this reading");
      expect(text(harness, "vision-not-saved")).toContain("video_");
    } finally {
      harness.unmount();
    }
  });

  it("still labels and saves against an API that has no category vocabulary", async () => {
    // `shoppable_categories` arrived with the labelling step; the fixture above
    // deliberately does not carry it. An older API answers a reading this
    // screen can still draw, still label by brand and price, and still keep.
    // Losing the dropdown's options must cost the operator the dropdown, not
    // the feature.
    const harness = await mount();
    await chooseFile(harness);
    try {
      const select = node(harness, "vision-category-V_001") as HTMLSelectElement;
      expect(Array.from(select.options).map((option) => option.value)).toEqual([""]);
      expect(node(harness, "vision-keep")).not.toBeNull();
    } finally {
      harness.unmount();
    }
  });

  it("says how many frames it read", async () => {
    const harness = await mount();
    await chooseFile(harness);
    try {
      expect(text(harness, "vision-result")).toContain("6");
    } finally {
      harness.unmount();
    }
  });
});

describe("a clip the pipeline cannot read", () => {
  it("shows the server's refusal, never an empty store", async () => {
    const detail =
      "no shelf edges were found in any sampled frame. This pipeline reads a roughly front-on shot";
    const harness = await mount({ status: 422, detail });
    await chooseFile(harness);
    try {
      expect(text(harness, "vision-error")).toContain("no shelf edges");
      expect(harness.container.querySelector('[data-testid="vision-result"]')).toBeNull();
    } finally {
      harness.unmount();
    }
  });

  it("survives a 200 that is not a reading", async () => {
    // The fourth component in this codebase to meet this; it is guarded here
    // before it can happen rather than after.
    const harness = await mount({ body: {} });
    await chooseFile(harness);
    try {
      expect(harness.container.querySelector('[data-testid="vision-view"]')).not.toBeNull();
      expect(text(harness, "vision-error")).toBeTruthy();
    } finally {
      harness.unmount();
    }
  });
});

describe("the screen is a way back, not a dead end", () => {
  it("links to the operator launcher", async () => {
    const harness = await mount();
    try {
      expect(node(harness, "vision-home-link").getAttribute("href")).toBe("#/home");
    } finally {
      harness.unmount();
    }
  });
});
