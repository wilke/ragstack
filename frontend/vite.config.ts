import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

// Dev server proxies the API surface so the browser sees a single origin (no
// CORS) — matching same-origin production. Two layers:
//
//  * "/v1" + "/health" go to VITE_API_TARGET (default :8000) — the plain default.
//  * "/be/<name>/..." are per-backend prefixes, each proxied (prefix stripped) to
//    a specific API. The in-app backend switcher (see api/config.ts) selects a
//    prefix so ALL traffic stays same-origin and reaches the chosen backend
//    THROUGH this proxy — which is what makes switching work over an SSH/port
//    forward, where only the Vite port is reachable and a bare localhost:<api> in
//    the browser would resolve to the viewer's own machine.
//
// Targets are env-overridable (VITE_BE_UNIFIED / _ASM / _LUCID) for non-default
// ports. loadEnv (not process.env) so frontend/.env keys are read before config.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const apiTarget = env.VITE_API_TARGET || "http://localhost:8000";
  const backends: Record<string, string> = {
    unified: env.VITE_BE_UNIFIED || "http://localhost:8020",
    asm: env.VITE_BE_ASM || "http://localhost:8000",
    lucid: env.VITE_BE_LUCID || "http://localhost:8010",
  };
  const bePrefix = (name: string, target: string) => ({
    [`/be/${name}`]: {
      target,
      changeOrigin: true,
      rewrite: (p: string) => p.replace(new RegExp(`^/be/${name}`), ""),
    },
  });
  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        ...bePrefix("unified", backends.unified),
        ...bePrefix("asm", backends.asm),
        ...bePrefix("lucid", backends.lucid),
        "/v1": { target: apiTarget, changeOrigin: true },
        "/health": { target: apiTarget, changeOrigin: true },
      },
    },
    build: { outDir: "dist", sourcemap: true },
  };
});
