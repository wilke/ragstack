import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Dev server proxies the API surface to the FastAPI app on :8000, so the browser
// sees a single origin (no CORS) in dev — matching the same-origin production
// deployment (SPA served by the API / a shared reverse proxy). Override the
// target with VITE_API_TARGET.
const API_TARGET = process.env.VITE_API_TARGET ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/v1": { target: API_TARGET, changeOrigin: true },
      "/health": { target: API_TARGET, changeOrigin: true },
    },
  },
  build: { outDir: "dist", sourcemap: true },
});
