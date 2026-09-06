import { readFileSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it, vi } from "vitest";
import { GazeTracker, type WebGazerLike } from "@/capture/GazeTracker";

/**
 * The eye tracker's runtime assets have to exist where the app serves them.
 *
 * This test exists because of a total, silent outage. WebGazer 3.5.x replaced
 * the TensorFlow FaceMesh backend with MediaPipe's local-WASM runtime:
 * `node_modules/webgazer/src/facemesh.mjs` calls `createDetector` with
 * `runtime: 'mediapipe'` and a `solutionPath`, and MediaPipe then fetches ~17 MB
 * of WASM, model and packed-asset files **from the host app's own web root**.
 * `web/public/` held nothing but `textures/`, so every one of those requests
 * 404ed. MediaPipe's script injector resolves its load promise on the `error`
 * event as well as on `load`, so the 404 was swallowed and the next statement
 * called `window.createMediapipeSolutionsWasm` - still the `{locateFile}`
 * placeholder object rather than the real Emscripten factory. That threw
 * "z2 is not a function", `GazeTracker.start()` rejected, and every webcam
 * session fell back to `mode: "cursor_only"`. A project whose central claim is
 * that it measures gaze was measuring mouse pointers.
 *
 * `web/tests/gazeTracker.test.ts` stayed green throughout, because it injects a
 * fake WebGazer whose `begin` resolves immediately - the real library, the real
 * bundle and the real asset load are never exercised in jsdom, and cannot be
 * (no WebGL, no getUserMedia). So the honest check is not "does the tracker
 * start" but "are the bytes the runtime will ask for present, and identical to
 * the ones webgazer shipped". That is a filesystem property, it is fast, and it
 * is red on any clone where `scripts/copy_mediapipe_assets.py` has not run.
 *
 * `web/tests/spectatorIsolation.test.ts` checks an import-graph rule the same
 * way, for the same reason: some rules are properties of the repository rather
 * than of a function.
 */

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, "..", "..");
/** Vite's `root` is `web/`, so `web/public/` is what the dev server serves at `/`. */
const PUBLIC = join(ROOT, "web", "public");
const SERVED_ASSETS = join(PUBLIC, "mediapipe", "face_mesh");
/** webgazer ships these itself; `@mediapipe/face_mesh` is not a declared dependency. */
const SHIPPED_ASSETS = join(
  ROOT,
  "node_modules",
  "webgazer",
  "dist",
  "mediapipe",
  "face_mesh",
);

/**
 * Every file MediaPipe's `locateFile` asks for, by the name it asks for. The
 * two `_bin.js` loaders are the ones that define `createMediapipeSolutionsWasm`;
 * the browser picks the `simd` pair or the plain pair at runtime, so both have
 * to be there. `face_mesh_solution_simd_wasm_bin.data` is a zero-byte file
 * upstream - it is listed because MediaPipe still requests it, and a size
 * comparison rather than a "not empty" assertion is what lets it be listed.
 */
const REQUIRED_ASSETS = [
  "face_mesh.binarypb",
  "face_mesh_solution_packed_assets.data",
  "face_mesh_solution_packed_assets_loader.js",
  "face_mesh_solution_simd_wasm_bin.data",
  "face_mesh_solution_simd_wasm_bin.js",
  "face_mesh_solution_simd_wasm_bin.wasm",
  "face_mesh_solution_wasm_bin.js",
  "face_mesh_solution_wasm_bin.wasm",
];

const RUN_THE_COPY =
  "Run `python scripts/copy_mediapipe_assets.py` (it is part of `make setup`).";

function sizeOf(path: string): number | null {
  try {
    const stat = statSync(path);
    return stat.isFile() ? stat.size : null;
  } catch {
    return null;
  }
}

describe("MediaPipe face_mesh assets", () => {
  it("are all present under the directory the dev server serves", () => {
    const missing = REQUIRED_ASSETS.filter(
      (name) => sizeOf(join(SERVED_ASSETS, name)) === null,
    );

    expect(
      missing,
      `WebGazer's MediaPipe runtime will 404 on these and the eye tracker will ` +
        `not start (the failure surfaces as "z2 is not a function"). ${RUN_THE_COPY}`,
    ).toEqual([]);
  });

  it("are byte-for-byte the size of the ones webgazer ships", () => {
    // A partial copy, or a copy left behind by an older webgazer, is worse than
    // no copy: the files exist, the requests return 200, and the failure moves
    // somewhere much harder to read. Sizes are compared rather than contents
    // because 17 MB of hashing per test run buys nothing here - a truncated or
    // stale asset differs in length.
    const wrong: string[] = [];
    for (const name of REQUIRED_ASSETS) {
      const shipped = sizeOf(join(SHIPPED_ASSETS, name));
      if (shipped === null) {
        wrong.push(`${name}: missing from node_modules/webgazer - run \`npm install\``);
        continue;
      }
      const served = sizeOf(join(SERVED_ASSETS, name));
      if (served !== shipped) {
        wrong.push(`${name}: served ${served ?? "absent"} bytes, webgazer ships ${shipped}`);
      }
    }

    expect(wrong, `The copied assets do not match the installed webgazer. ${RUN_THE_COPY}`).toEqual(
      [],
    );
  });
});

describe("webgazer's version", () => {
  it("is pinned exactly, with no range operator", () => {
    // CLAUDE.md rule 5: "Dependencies are pinned". `"webgazer": "^3.3.0"` was
    // not a pin - the caret floated the install to 3.5.3, which is the release
    // that swapped the FaceMesh backend for the MediaPipe one and needed 17 MB
    // of assets nobody had been told about. The outage arrived without a single
    // line of this repository changing.
    const manifest = JSON.parse(readFileSync(join(ROOT, "package.json"), "utf8")) as {
      dependencies: Record<string, string>;
    };
    const declared = manifest.dependencies.webgazer;

    expect(declared).toMatch(/^\d+\.\d+\.\d+$/);
  });

  it("is the version that is actually installed", () => {
    const manifest = JSON.parse(readFileSync(join(ROOT, "package.json"), "utf8")) as {
      dependencies: Record<string, string>;
    };
    const installed = JSON.parse(
      readFileSync(join(ROOT, "node_modules", "webgazer", "package.json"), "utf8"),
    ) as { version: string };

    // An exact pin that disagrees with node_modules is a pin nobody is testing:
    // the assets copied above came out of the installed copy, not the declared
    // one.
    expect(manifest.dependencies.webgazer).toBe(installed.version);
  });
});

describe("GazeTracker's solution path", () => {
  /**
   * WebGazer's default `faceMeshSolutionPath` is `'./mediapipe/face_mesh'`,
   * which MediaPipe hands to `new URL(path, document.baseURI)` - it is
   * *document-relative*. This app's routes are hash-based (`#/whatif`), so the
   * document path is `/` today and the default happens to work; but Vite's dev
   * server falls back to `index.html` for any path, so a single URL with a
   * segment in it would silently resolve the assets one directory deeper and
   * bring the whole outage back. The tracker states the path instead, and this
   * test is what ties that statement to the directory the copy script fills.
   */
  it("points at the copied assets, from the site root, before begin()", async () => {
    const params: Record<string, unknown> = { videoElementId: "webgazerVideoFeed" };
    // What the path was at the moment the camera opened, which is the only
    // ordering that matters: a path written afterwards is a path MediaPipe may
    // already have read past.
    let pathWhenBegun: unknown = "begin() was never called";
    const begin = vi.fn(async (): Promise<void> => {
      pathWhenBegun = params.faceMeshSolutionPath;
    });
    const fake = {
      showVideo: vi.fn(),
      showVideoPreview: vi.fn(),
      showFaceOverlay: vi.fn(),
      showFaceFeedbackBox: vi.fn(),
      showPredictionPoints: vi.fn(),
      saveDataAcrossSessions: vi.fn(),
      removeMouseEventListeners: vi.fn(),
      setGazeListener: vi.fn(),
      clearGazeListener: vi.fn(),
      recordScreenPosition: vi.fn(),
      begin,
      end: vi.fn(),
      stopVideo: vi.fn(),
      params,
    };

    const tracker = new GazeTracker({ load: async () => fake as unknown as WebGazerLike });
    await tracker.start();
    tracker.stop();

    const configured = params.faceMeshSolutionPath;
    expect(typeof configured).toBe("string");
    // Root-relative, so it means the same thing from every URL the app can be on.
    expect(configured as string).toMatch(/^\//);
    // And that root-relative path, resolved inside the served directory, is
    // exactly where scripts/copy_mediapipe_assets.py puts the files.
    expect(resolve(PUBLIC, `.${configured as string}`)).toBe(SERVED_ASSETS);

    // MediaPipe reads the path when the detector is created, which happens on
    // the first frame - but begin() is what starts producing frames, so the
    // path has to be in place before it, exactly like the privacy switches.
    expect(begin).toHaveBeenCalledTimes(1);
    expect(pathWhenBegun).toBe(configured);
  });
});
