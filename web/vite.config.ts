import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "path";

export default defineConfig({
  root: resolve(__dirname),
  plugins: [react()],
  server: {
    port: 5173,
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
