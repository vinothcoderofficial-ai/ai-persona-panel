/**
 * The only place WebGazer is touched.
 *
 * Privacy contract (CLAUDE.md, SPEC M2), enforced here and asserted in
 * `web/tests/gazeTracker.test.ts`:
 *   - `showVideo(false)` and `showPredictionPoints(false)` before the camera
 *     opens: the shopper never sees the webcam feed, and never sees a gaze dot.
 *     People stare at the dot, and a stared-at dot is not shopping data.
 *   - `saveDataAcrossSessions(false)`. WebGazer's default is *true* and it
 *     persists training data to localforage. Not on this machine.
 *   - Nothing from the camera leaves this module. WebGazer's prediction object
 *     carries `eyeFeatures`, which are image patches of the shopper's eyes.
 *     Subscribers get a freshly built `{x, y, conf, t}` and nothing else.
 *
 * WebGazer is imported lazily inside `start()`. A top-level import would drag
 * TensorFlow.js into every module that touches capture, and jsdom (no WebGL, no
 * getUserMedia) cannot load it at all - every other test file would die with it.
 */

/** SPEC M2, first filter step: anything less confident than this is not a sample. */
export const MIN_GAZE_CONF = 0.5;

/** The only thing that ever leaves the browser. `t` is a `performance.now()` ms stamp. */
export interface GazeSample {
  x: number;
  y: number;
  conf: number;
  t: number;
}

export type GazeListener = (sample: GazeSample) => void;

/** The slice of WebGazer's API this project uses. */
export interface WebGazerLike {
  showVideo(on: boolean): unknown;
  showVideoPreview?(on: boolean): unknown;
  showFaceOverlay?(on: boolean): unknown;
  showFaceFeedbackBox?(on: boolean): unknown;
  showPredictionPoints(on: boolean): unknown;
  saveDataAcrossSessions(on: boolean): unknown;
  setGazeListener(listener: (data: unknown, elapsedMs: number) => void): unknown;
  clearGazeListener(): unknown;
  removeMouseEventListeners?(): unknown;
  recordScreenPosition(x: number, y: number, eventType?: string): unknown;
  begin(onFail?: () => void): unknown;
  end(): unknown;
  stopVideo?(): unknown;
  params?: Record<string, unknown>;
}

export interface GazeTrackerOptions {
  /** Injectable so tests can supply a fake; production loads the real library. */
  load?: () => Promise<WebGazerLike>;
  now?: () => number;
  minConf?: number;
}

const DEFAULT_VIDEO_ELEMENT_ID = "webgazerVideoFeed";

/**
 * Where the browser fetches WebGazer's MediaPipe FaceMesh runtime from.
 *
 * WebGazer 3.5.x runs FaceMesh through MediaPipe's local WASM build, and
 * MediaPipe fetches ~17 MB of WASM, model and packed-asset files from this
 * app's own web root at `params.faceMeshSolutionPath`. When they are not there
 * the failure is silent and misleading: MediaPipe's script injector resolves
 * its load promise on the `error` event as well as on `load`, so the 404 is
 * swallowed and the next call lands on a placeholder object - "z2 is not a
 * function" - `start()` rejects, and the session quietly becomes cursor_only.
 * `scripts/copy_mediapipe_assets.py` is what puts them there, on `make setup`.
 *
 * Stated here rather than left to WebGazer's default `'./mediapipe/face_mesh'`,
 * which is *document-relative*: MediaPipe resolves it against the current page
 * URL. This app's routes are hash-based (`#/whatif`, `#/spectator`), so the
 * document path is `/` and the default happens to work - but Vite serves
 * `index.html` for any path, and a single URL with a segment in it would
 * resolve the assets one directory deeper and bring the outage back with the
 * same unreadable error. `web/public/` is served at `/` in both dev and build
 * (no `base` is configured in `web/vite.config.ts`), so a root-relative path
 * means the same thing from every URL the app can be on.
 *
 * `web/tests/mediapipeAssets.test.ts` pins this constant to the directory the
 * copy script fills, so the two cannot drift apart.
 */
export const FACE_MESH_SOLUTION_PATH = "/mediapipe/face_mesh";

async function loadWebGazer(): Promise<WebGazerLike> {
  const module = await import("webgazer");
  return module.default as WebGazerLike;
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

/**
 * WebGazer's prediction carries no confidence of its own, so one is derived,
 * in this order:
 *   1. a numeric `confidence` on the prediction wins (clamped to [0, 1]);
 *   2. an `eyeFeatures` object missing either eye patch scores 0 - the
 *      regression is extrapolating from a face it has lost;
 *   3. anything else scores 1.
 * A null prediction never reaches here: it is dropped before this.
 */
function confidenceOf(prediction: Record<string, unknown>): number {
  const stated = prediction.confidence;
  if (isFiniteNumber(stated)) return Math.min(1, Math.max(0, stated));

  const eyes = prediction.eyeFeatures;
  if (typeof eyes === "object" && eyes !== null) {
    const { left, right } = eyes as { left?: unknown; right?: unknown };
    const bothEyes =
      typeof left === "object" &&
      left !== null &&
      typeof right === "object" &&
      right !== null;
    return bothEyes ? 1 : 0;
  }
  return 1;
}

/** Teardown must never throw: a half-initialised WebGazer still has to let go. */
function safely(step: () => unknown): void {
  try {
    step();
  } catch {
    // Nothing to do here - the point of the call was to release something.
  }
}

/**
 * WebGazer's own `end()` leaves the MediaStream running (its `stopVideo()` call
 * is commented out upstream), and its `stopVideo()` stops only the first track,
 * so the camera is released by hand: every track, before the video element is
 * removed from the page.
 */
function releaseCameraTracks(webgazer: WebGazerLike): void {
  const configured = webgazer.params?.videoElementId;
  const id = typeof configured === "string" ? configured : DEFAULT_VIDEO_ELEMENT_ID;
  const element = document.getElementById(id);
  if (!(element instanceof HTMLVideoElement)) return;

  const source: unknown = element.srcObject;
  if (typeof source !== "object" || source === null) return;
  if (typeof (source as { getTracks?: unknown }).getTracks !== "function") return;

  for (const track of (source as MediaStream).getTracks()) track.stop();
  element.srcObject = null;
}

export class GazeTracker {
  private readonly loadLibrary: () => Promise<WebGazerLike>;
  private readonly now: () => number;
  private readonly minConf: number;
  private readonly listeners = new Set<GazeListener>();

  private webgazer: WebGazerLike | null = null;
  private starting: Promise<void> | null = null;
  private stopRequested = false;

  constructor(options: GazeTrackerOptions = {}) {
    this.loadLibrary = options.load ?? loadWebGazer;
    this.now = options.now ?? (() => performance.now());
    this.minConf = options.minConf ?? MIN_GAZE_CONF;
  }

  get running(): boolean {
    return this.webgazer !== null;
  }

  /** Subscribe to samples. The returned function unsubscribes. */
  subscribe(listener: GazeListener): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  /**
   * Load WebGazer, apply the privacy settings, open the camera. Calling it
   * again while it is already running, or still starting, is a no-op.
   */
  async start(): Promise<void> {
    if (this.webgazer !== null) return;
    if (this.starting !== null) {
      await this.starting;
      return;
    }

    this.stopRequested = false;
    this.starting = this.begin();
    try {
      await this.starting;
    } finally {
      this.starting = null;
    }
  }

  private async begin(): Promise<void> {
    const webgazer = await this.loadLibrary();
    // stop() during the load: the camera was never opened, so there is nothing
    // to release and nothing to start.
    if (this.stopRequested) return;

    // Said before begin() for the same reason the switches below are: MediaPipe
    // builds its detector on the first frame, and begin() is what starts
    // producing frames. `params` is WebGazer's own live settings object - the
    // module reads it at use, so writing it here is what the next read sees.
    if (webgazer.params !== undefined) {
      webgazer.params.faceMeshSolutionPath = FACE_MESH_SOLUTION_PATH;
    }

    // Every one of these defaults to true in WebGazer, so all of them are
    // turned off before begin() is allowed to open the camera.
    webgazer.saveDataAcrossSessions(false);
    webgazer.showVideoPreview?.(false);
    webgazer.showVideo(false);
    webgazer.showFaceOverlay?.(false);
    webgazer.showFaceFeedbackBox?.(false);
    webgazer.showPredictionPoints(false);

    await webgazer.begin();

    // stop() while the camera was opening - the shopper closed the tab, or the
    // flow was unmounted mid-calibration. Hand the camera straight back rather
    // than leaving a live stream behind an unmounted component.
    if (this.stopRequested) {
      this.release(webgazer);
      return;
    }

    // WebGazer trains itself on every click and mouse move by default. That
    // would keep retraining the model during shopping, long after the
    // calibration error was measured and written into the session. Training is
    // explicit instead: through record(), on the calibration screen only.
    webgazer.removeMouseEventListeners?.();

    webgazer.setGazeListener((data) => this.onPrediction(data));
    this.webgazer = webgazer;
  }

  /** One calibration click: "the shopper was looking here". */
  record(x: number, y: number): void {
    this.webgazer?.recordScreenPosition(x, y, "click");
  }

  /**
   * Stop predicting and release the camera. Safe to call when never started,
   * and safe to call while start() is still running: the start finishes by
   * releasing the camera it just opened.
   */
  stop(): void {
    this.stopRequested = true;
    const webgazer = this.webgazer;
    this.webgazer = null;
    if (webgazer === null) return;
    this.release(webgazer);
  }

  private release(webgazer: WebGazerLike): void {
    safely(() => webgazer.clearGazeListener());
    safely(() => releaseCameraTracks(webgazer));
    safely(() => webgazer.stopVideo?.());
    safely(() => webgazer.end());
  }

  private onPrediction(data: unknown): void {
    // A sample after stop() is a straggler from WebGazer's last loop.
    if (this.webgazer === null) return;
    if (typeof data !== "object" || data === null) return;

    const prediction = data as Record<string, unknown>;
    const { x, y } = prediction;
    if (!isFiniteNumber(x) || !isFiniteNumber(y)) return;

    const conf = confidenceOf(prediction);
    if (conf < this.minConf) return;

    // Built field by field on purpose: `data` also carries eyeFeatures, images
    // of the shopper's eyes. Nothing but these four values exists past here.
    const sample: GazeSample = { x, y, conf, t: this.now() };
    for (const listener of this.listeners) listener(sample);
  }
}
