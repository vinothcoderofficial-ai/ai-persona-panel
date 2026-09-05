import { readFileSync, readdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { Launcher } from "@/launcher/Launcher";
import type { LastSession } from "@/session/lastSession";

/**
 * `#/home`: the operator's launcher.
 *
 * There are four screens and, before this, the only way to reach any of them
 * was to type a URL - the dashboard's included a raw uuid, typed by hand, on
 * camera. This page is the fix, and it is an *operator* screen: CLAUDE.md
 * forbids navigation chrome on the shopper's store screen and on the filmed
 * spectator screen, so the links live here instead.
 *
 * The variant chooser is checked against `data/variants/` **as it is on disk**,
 * not against a list copied into this file: every variant that has a document
 * has to be offered, and under the name that document gives it. A launcher that
 * described D as anything but the no-creative control arm - or that silently
 * omitted a variant somebody added - would send an operator to collect the
 * wrong arm of the between-variant Brand Lift, and nothing downstream would
 * notice.
 */

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

const HERE = dirname(fileURLToPath(import.meta.url));
const VARIANTS_DIR = resolve(HERE, "..", "..", "data", "variants");

interface VariantDocument {
  variant_id: string;
  name: string;
}

/** Every `data/variants/*.json`, the same set `collect_link.py` deals links from. */
const VARIANT_DOCUMENTS: VariantDocument[] = readdirSync(VARIANTS_DIR)
  .filter((name) => name.endsWith(".json"))
  .map(
    (name) =>
      JSON.parse(readFileSync(join(VARIANTS_DIR, name), "utf8")) as VariantDocument,
  );

const STORED: LastSession = {
  session_id: "3f6b1c2e-9a44-4d0e-8c11-77a0b5e2d913",
  variant_id: "C",
  started_at: "2026-09-14T10:32:07.412Z",
};

interface Mounted {
  container: HTMLElement;
  unmount: () => void;
}

function render(stored: LastSession | null = null): Mounted {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => {
    root.render(<Launcher readStoredSession={() => stored} />);
  });
  return {
    container,
    unmount: () => {
      act(() => root.unmount());
      container.remove();
    },
  };
}

function find(container: HTMLElement, testId: string): HTMLElement {
  const element = container.querySelector<HTMLElement>(`[data-testid="${testId}"]`);
  if (element === null) throw new Error(`no element with data-testid="${testId}"`);
  return element;
}

function has(container: HTMLElement, testId: string): boolean {
  return container.querySelector(`[data-testid="${testId}"]`) !== null;
}

function href(container: HTMLElement, testId: string): string {
  return find(container, testId).getAttribute("href") ?? "";
}

function text(element: HTMLElement): string {
  return (element.textContent ?? "").toLowerCase();
}

afterEach(() => {
  document.body.innerHTML = "";
});

describe("the four destinations", () => {
  it("offers every screen in the demo, so none of them needs a typed URL", () => {
    const view = render();
    for (const testId of [
      "launcher-store",
      "launcher-whatif",
      "launcher-spectator",
      "launcher-dashboard",
    ]) {
      expect(has(view.container, testId), `${testId} is missing`).toBe(true);
    }
    view.unmount();
  });

  it("links the three hash routes exactly as main.tsx routes them", () => {
    const view = render();
    expect(href(view.container, "launcher-whatif-link")).toBe("#/whatif");
    expect(href(view.container, "launcher-spectator-link")).toBe("#/spectator");
    expect(href(view.container, "launcher-dashboard-link")).toBe("#/dashboard");
    view.unmount();
  });

  it("says what each screen is, not just where it goes", () => {
    const view = render();
    // The spectator is the second-monitor screen and the description has to say
    // so: opening it on the shopper's own machine is the one thing CLAUDE.md
    // forbids outright, because people stare at their own gaze dot.
    expect(text(find(view.container, "launcher-spectator"))).toContain("second monitor");
    expect(text(find(view.container, "launcher-whatif"))).toContain("shelf");
    expect(text(find(view.container, "launcher-dashboard"))).toContain("session");
    view.unmount();
  });
});

describe("the variant chooser", () => {
  it("offers every variant that has a document in data/variants/", () => {
    // Read from disk, so adding a fifth arm without describing it here fails.
    expect(VARIANT_DOCUMENTS.length).toBeGreaterThanOrEqual(4);
    const view = render();
    for (const variant of VARIANT_DOCUMENTS) {
      expect(
        has(view.container, `launcher-variant-${variant.variant_id}`),
        `variant ${variant.variant_id} is missing from the launcher`,
      ).toBe(true);
    }
    view.unmount();
  });

  it("opens the store at the participant-link shape scripts/collect_link.py generates", () => {
    // collect_link.py's build_url() adds `variant` to the query and nothing
    // else - no hash. A bare URL must stay the store, and these links are the
    // same URL an emailed participant gets.
    const view = render();
    expect(href(view.container, "launcher-open-A")).toBe("/?variant=A");
    expect(href(view.container, "launcher-open-D")).toBe("/?variant=D");
    view.unmount();
  });

  it("keeps the rehearsal shortcut separate and says what it costs", () => {
    const view = render();
    expect(href(view.container, "launcher-rehearse-B")).toBe("/?variant=B&skip_capture=1");
    // main.tsx's DEV_SKIP_FIELDS record consent: false, and SessionGate rejects
    // such a session with `no_consent`. A launcher that offered this as an
    // equal option would quietly produce a panel of unusable sessions.
    const rehearsal = text(find(view.container, "launcher-rehearse-note"));
    expect(rehearsal).toContain("consent: false");
    expect(rehearsal).toContain("rehearsal");
    view.unmount();
  });

  it("describes each variant from its own document, not from memory", () => {
    const view = render();

    // A: data/variants/A.json is `"patches": []`, so it is the base planogram,
    // whose B3_ENDCAP carries AD_1.
    const a = text(find(view.container, "launcher-variant-A"));
    expect(a).toContain("baseline");
    expect(a).toContain("b3_endcap");

    // B: one patch, move_sku SKU_008 -> B1S3P2. B1S5 is `level: "bottom"` and
    // B1S3 is `level: "eye"` in data/planograms/demo_aisle.json.
    const b = text(find(view.container, "launcher-variant-B"));
    expect(b).toContain("eye level");
    expect(b).toContain("sku_008");

    // C: clears B3_ENDCAP and puts AD_1 on B1_TALKER.
    const c = text(find(view.container, "launcher-variant-C"));
    expect(c).toContain("b1_talker");
    expect(c).toContain("ad_1");

    // D: three set_ad_creative patches, every creative_id null. This is the
    // control arm the between-variant Brand Lift is measured against, and it is
    // the only variant carrying no creative at all.
    const d = text(find(view.container, "launcher-variant-D"));
    expect(d).toContain("control");
    expect(d).toContain("no ad creative");
    expect(d).toContain("brand lift");

    view.unmount();
  });

  it("names each variant with the name in its own file", () => {
    const view = render();
    for (const variant of VARIANT_DOCUMENTS) {
      const card = text(find(view.container, `launcher-variant-${variant.variant_id}`));
      expect(card, `variant ${variant.variant_id}`).toContain(variant.name.toLowerCase());
    }
    view.unmount();
  });
});

describe("the session the store last opened", () => {
  it("shows it, so the operator never reads a uuid off a network tab", () => {
    const view = render(STORED);
    expect(text(find(view.container, "launcher-last-session"))).toContain(
      STORED.session_id,
    );
    view.unmount();
  });

  it("names that session in the spectator and dashboard links, rather than leaning on the fallback", () => {
    const view = render(STORED);
    expect(href(view.container, "launcher-last-spectator")).toBe(
      `#/spectator?session=${STORED.session_id}`,
    );
    // Experiment reads location.search, never the hash, so the dashboard's ids
    // must be written before the `#`.
    expect(href(view.container, "launcher-last-dashboard")).toBe(
      `/?session=${STORED.session_id}&variant=${STORED.variant_id}#/dashboard`,
    );
    view.unmount();
  });

  it("says plainly that no session has been opened in this browser yet", () => {
    const view = render(null);
    expect(has(view.container, "launcher-last-session")).toBe(false);
    expect(text(find(view.container, "launcher-no-last-session"))).toContain(
      "no session",
    );
    view.unmount();
  });
});
