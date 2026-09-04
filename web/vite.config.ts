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
  test: { environment: "jsdom", include: ["tests/**/*.test.ts", "tests/**/*.test.tsx"] },
});
