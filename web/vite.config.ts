import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "path";

export default defineConfig({
  root: resolve(__dirname),
  plugins: [react()],
  server: {
    port: 5173,
    // `root` above is `web/`, so `data/models/WaterBottle.glb` — the CC0
    // Khronos sample model `AisleDisplay.tsx` renders — sits outside it and
    // the dev server will not serve it. The alternative is a second copy of an
    // 8.6 MB binary under `web/public/`, and this repo is not carrying the
    // same asset twice: `data/models/` is where the layout puts data, it is
    // where the provenance README lives, and that README documents that path.
    //
    // Allowing the repository root instead means the `?url` import in
    // `AisleDisplay.tsx` resolves the same way in both modes without any
    // copying: in `npm run dev` Vite serves it through `/@fs/`, and in
    // `npm run build` Rollup emits it into `dist/assets/` with a content hash
    // (8.6 MB is far over `assetsInlineLimit`, so it is always a file, never
    // a data URI) and rewrites the import to that path. Neither needs the file
    // to be under `web/`.
    //
    // Vite's default here is the workspace root it infers from the lockfile,
    // which happens to be this same directory — but that is inference about a
    // file layout, and this asset is load-bearing enough to say out loud.
    fs: { allow: [resolve(__dirname, "..")] },
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
      "/ws": { target: "ws://localhost:8000", ws: true },
    },
  },
  resolve: { alias: { "@": resolve(__dirname, "src") } },
  test: {
    environment: "jsdom",
    include: ["tests/**/*.test.ts", "tests/**/*.test.tsx"],
    // vitest defaults to 5s. `devSessionFinish.test.tsx` drives a whole
    // consent-and-finish flow through jsdom and spends ~2.5s of that budget on
    // an idle machine, which leaves no headroom. `make test` runs pytest first,
    // so vitest always starts on a machine warm from a three-minute run, and
    // the same test has been measured at 16s there. The work is real and it
    // completes -- the default was timing the machine rather than the code, and
    // a green suite that goes red because something else ran first teaches
    // people to re-run rather than to read.
    //
    // 30s rather than 20s: the same test was measured at 18.5s here, which
    // leaves 8% headroom against 20s and would flake again on anything slower.
    // A timeout is there to catch a hang, and 30s still catches one.
    testTimeout: 30_000,
  },
});
