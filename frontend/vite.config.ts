import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

// Dev server proxies the API surface to the FastAPI app on :8000, so the browser
// sees a single origin (no CORS) in dev — matching the same-origin production
// deployment (SPA served by the API / a shared reverse proxy). Override the
// target with VITE_API_TARGET.
//
// Read the env via loadEnv (not process.env): Vite does NOT populate process.env
// from .env files before the config is evaluated, so a VITE_API_TARGET set in
// frontend/.env would otherwise be ignored. loadEnv reads .env[.mode] for the
// current mode; the "" prefix lets it also pick up non-VITE_-prefixed keys.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const apiTarget = env.VITE_API_TARGET || "http://localhost:8000";
  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        "/v1": { target: apiTarget, changeOrigin: true },
        "/health": { target: apiTarget, changeOrigin: true },
      },
    },
    build: { outDir: "dist", sourcemap: true },
  };
});
