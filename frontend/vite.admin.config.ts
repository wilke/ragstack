import react from "@vitejs/plugin-react";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { defineConfig, loadEnv } from "vite";

// The ADMIN bundle — `ragstack-ctl`'s dashboard, built separately from the
// tenant SPA (`vite.config.ts`).
//
// Why a second config rather than a second entry in the first one: Vite's
// multi-entry build emits every input into ONE outDir with ONE shared chunk
// graph, so `admin.html` (and the fleet/gateway views behind it) would ship
// inside every tenant's `dist/`. Tenant bundles are served from origins the
// control plane does not own; its screens have no business being there. Two
// configs, two outDirs, two `--base` values.
//
// Production: `npm run build:admin -- --base /ragstack/admin/ui/` →
// `dist-admin/`, which nginx aliases with `admin.html` as the SPA fallback.
// Under that base the app talks to `/ragstack/admin/api` (see
// src/admin/api/http.ts, which derives it from BASE_URL exactly as the tenant
// UI derives its own sibling API).
//
// Dev: `npm run dev:admin` serves the ADMIN app at "/" — `index.html` is the
// TENANT app's entry and sits in the same project root, so Vite's own root
// resolution served that instead and `dev:admin` opened the explorer. The
// `admin-root` plugin below rewrites "/" (and "/index.html") to "/admin.html",
// and `server.open` points the browser there. It proxies `/v1` + `/health` to
// VITE_CTL_TARGET so the browser sees one origin, the same shape production
// has. The default target is the daemon's conventional loopback bind; point it
// at a throwaway `--fake-drivers` daemon (see conformance/run_ctl_local.sh)
// rather than the live control plane when you are developing:
//
//   VITE_CTL_TARGET=http://127.0.0.1:23995 npm run dev:admin
const { version: uiVersion } = createRequire(import.meta.url)("./package.json");

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const ctlTarget = env.VITE_CTL_TARGET || "http://127.0.0.1:23990";

  // Same DNS-rebinding posture as the tenant config: empty by default,
  // per-deployment via VITE_ALLOWED_HOSTS. localhost/127.0.0.1 always pass.
  const allowedRaw = env.VITE_ALLOWED_HOSTS ?? "";
  const allowedHosts =
    allowedRaw.trim() === "true"
      ? true
      : allowedRaw.split(",").map((h) => h.trim()).filter(Boolean);

  return {
    plugins: [
      react(),
      {
        // DEV ONLY — the built bundle has admin.html as its single input and
        // nginx aliases it with `try_files … /ragstack/admin/ui/admin.html`.
        // This exists so the dev server's "/" is the same app the production
        // mount serves, instead of the tenant explorer that happens to share
        // this project root. Registered in `configureServer`, which runs before
        // Vite's SPA fallback resolves index.html.
        name: "admin-root",
        configureServer(server) {
          server.middlewares.use((req, _res, next) => {
            const [path, query] = (req.url ?? "/").split("?");
            if (path === "/" || path === "/index.html") {
              req.url = "/admin.html" + (query ? `?${query}` : "");
            }
            next();
          });
        },
      },
    ],
    define: { __APP_VERSION__: JSON.stringify(uiVersion) },
    server: {
      port: 5299,
      allowedHosts,
      // `--open` lands on the admin entry even if the rewrite above is ever
      // removed; the two agree deliberately.
      open: "/admin.html",
      proxy: {
        "/v1": { target: ctlTarget, changeOrigin: true },
        "/health": { target: ctlTarget, changeOrigin: true },
      },
    },
    build: {
      outDir: "dist-admin",
      sourcemap: true,
      rollupOptions: {
        // Absolute: a relative input is resolved against the CWD, which is only
        // `frontend/` when the script happens to be run from there.
        input: fileURLToPath(new URL("./admin.html", import.meta.url)),
      },
    },
  };
});
