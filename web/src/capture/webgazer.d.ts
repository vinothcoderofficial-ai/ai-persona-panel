/**
 * WebGazer ships no type declarations. The shape we actually use is declared,
 * and checked, as `WebGazerLike` in GazeTracker.ts; this only tells TypeScript
 * that the module exists, so the dynamic import in `GazeTracker.start()` is not
 * an unresolved-module error.
 */
declare module "webgazer" {
  const webgazer: unknown;
  export default webgazer;
}
