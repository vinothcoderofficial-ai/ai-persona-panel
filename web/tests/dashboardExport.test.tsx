import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { act } from "react";
import { createRoot } from "react-dom/client";
import Experiment from "@/dashboard/Experiment";

/**
 * The export control, wired to the page it exports.
 *
 * `web/tests/dashboardReport.test.ts` owns the contents of the report -- what
 * it claims, what it refuses to claim. This file owns the one thing that file
 * cannot see: that the button exists, that it is fed the session's own lock
 * from `GET /sessions/{id}/prediction`, and that the object URL it mints is
 * revoked again. A report generator nothing on screen can reach is not a
 * feature.
 *
 * The two clicking tests print `Not implemented: navigation` to stderr, and
 * that is expected rather than a fault: `download()` saves the file the way a
 * browser saves one, by clicking an `<a download>`, and jsdom implements the
 * click but not the download attribute that stops it becoming a navigation.
 * The assertions are on the Blob, which is the part that carries the report.
 */

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

const EXPERIMENT = {
  experiment_id: "exp_20260904_1a2b3c",
  variant_id: "var_eye_level_shift",
  session_id: "sess_9f8e7d6c5b4a3928",
  mode: "cursor_only",
  n_synth: 10_000,
  seed: 42,
  slot_ids: ["B1S3P1", "B1S3P2"],
  real_attention: { B1S3P1: 0.41, B1S3P2: 0.22 },
  synth_attention: { B1S3P1: 0.37, B1S3P2: 0.28 },
  attention_spearman: 0.482,
  purchase_share_mae: 0.0134,
  real_purchase_share: { SKU_001: 1.0 },
  synth_purchase_share: { SKU_001: 0.55 },
};

const LOCK = {
  prediction_id: "pred_4c9a1f77b0e2",
  sim_run_id: "run_71b0c2d4",
  created_at: "2026-09-04T10:32:07.412Z",
  sha256_prefix: "9f3ab21c",
  population_fixation_prob: { B1S3P1: 0.2, B1S3P2: 0.3 },
};

class FakeResizeObserver {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

interface Mounted {
  container: HTMLDivElement;
  unmount: () => void;
}

function mount(node: ReactNode): Mounted {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => {
    root.render(node);
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

interface Captured {
  blobs: Blob[];
  created: string[];
  revoked: string[];
}

function stubDownloads(): Captured {
  const captured: Captured = { blobs: [], created: [], revoked: [] };
  let n = 0;
  Object.defineProperty(URL, "createObjectURL", {
    configurable: true,
    writable: true,
    value: (blob: Blob) => {
      captured.blobs.push(blob);
      n += 1;
      const url = `blob:shoppertwin/${n}`;
      captured.created.push(url);
      return url;
    },
  });
  Object.defineProperty(URL, "revokeObjectURL", {
    configurable: true,
    writable: true,
    value: (url: string) => {
      captured.revoked.push(url);
    },
  });
  return captured;
}

/**
 * jsdom's `Blob` has no `text()`, so the report comes back through
 * `FileReader`, which it does implement.
 */
function readBlob(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error);
    reader.readAsText(blob);
  });
}

function stubFetch(): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: unknown): Promise<Response> => {
      const url = String(input);
      const payload = url.includes("/prediction") ? LOCK : EXPERIMENT;
      return {
        ok: true,
        status: 200,
        statusText: "OK",
        json: async () => payload,
        text: async () => "",
      } as Response;
    }),
  );
}

async function settle(): Promise<void> {
  await act(async () => {
    for (let n = 0; n < 20; n += 1) await Promise.resolve();
  });
}

/**
 * Click, then flush both queues. The report is built after an `await` on the
 * prediction fetch (microtasks) and the object URL is revoked on the next
 * macrotask, deliberately -- `download()` in `Experiment.tsx` explains why
 * revoking on the next statement can cancel the download in a real browser.
 */
async function clickAndSettle(button: HTMLElement): Promise<void> {
  await act(async () => {
    button.click();
    for (let n = 0; n < 20; n += 1) await Promise.resolve();
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

async function renderDashboard(): Promise<Mounted> {
  window.history.replaceState({}, "", `/?experiment=${EXPERIMENT.experiment_id}`);
  const view = mount(<Experiment />);
  await settle();
  return view;
}

beforeEach(() => {
  vi.stubGlobal("ResizeObserver", FakeResizeObserver);
  stubFetch();
});

afterEach(() => {
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  window.history.replaceState({}, "", "/");
});

describe("the dashboard offers a session report", () => {
  it("renders an export control for both HTML and JSON", async () => {
    const view = await renderDashboard();

    expect(find(view.container, "experiment-export-html")).toBeTruthy();
    expect(find(view.container, "experiment-export-json")).toBeTruthy();

    view.unmount();
  });

  it("shows the session's capture mode on the page, not only in the report", async () => {
    const view = await renderDashboard();

    expect(find(view.container, "experiment-mode").textContent).toContain("cursor_only");

    view.unmount();
  });

  it("puts the fetched prediction lock into the exported HTML, then revokes the URL", async () => {
    const captured = stubDownloads();
    const view = await renderDashboard();

    await clickAndSettle(find(view.container, "experiment-export-html"));

    expect(captured.blobs).toHaveLength(1);
    const html = await readBlob(captured.blobs[0]);
    expect(html).toContain(LOCK.sha256_prefix);
    expect(html).toContain(LOCK.created_at);
    expect(html).toContain(EXPERIMENT.session_id);
    expect(captured.revoked).toEqual(captured.created);

    view.unmount();
  });

  it("exports the same session as JSON", async () => {
    const captured = stubDownloads();
    const view = await renderDashboard();

    await clickAndSettle(find(view.container, "experiment-export-json"));

    expect(captured.blobs).toHaveLength(1);
    const body = JSON.parse(await readBlob(captured.blobs[0]));
    expect(body.session_id).toBe(EXPERIMENT.session_id);
    expect(body.capture).toEqual({ mode: "cursor_only", gaze_measured: false });
    expect(body.pre_registration.sha256_prefix).toBe(LOCK.sha256_prefix);
    expect(captured.revoked).toEqual(captured.created);

    view.unmount();
  });
});
