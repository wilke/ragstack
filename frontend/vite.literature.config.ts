import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

// The LITERATURE DEMO bundle — a structured-query console over a literature
// corpus, built separately from the tenant SPA (`vite.config.ts`) and the
// control plane (`vite.admin.config.ts`).
//
// Why a third config rather than a tab in the tenant SPA: this app is
// DOMAIN-SPECIFIC (organism / genes / assertion types) where the tenant
// explorer is deliberately domain-neutral, and it talks to a SECOND origin
// (BV-BRC's Copilot API) for generation. Vite's multi-entry build emits every
// input into one outDir with one shared chunk graph, so a shared config would
// ship this — and the Copilot client with it — into every tenant's `dist/`.
// Three configs, three outDirs, three `--base` values. Same reasoning as the
// admin bundle; see the comment at the top of vite.admin.config.ts.
//
// Production: `npm run build:literature` → `dist-literature/`, deployed to
// /rag/data/apps/litdemo/dist/ on coconut, which nginx aliases at
// /ragstack/litdemo/ (a hand-written pair in the coconut-proxy repo's
// snippets/routes.conf — NOT the ctl-generated tenants-ui-static include).
//
// THE ORIGIN STORY, which is the whole reason this config looks like it does:
//
//   * Served at https://www.bv-brc.org/ragstack/litdemo/, the ragstack API is
//     SAME-ORIGIN at /ragstack/<tenant>/api/... — a relative URL, no CORS, no
//     mixed content. The BV-BRC front proxy terminates TLS and forwards to
//     coconut:9000 over plain http, so the page must NEVER name coconut:9000
//     itself: an https page calling http is blocked as mixed content.
//   * In dev the same relative URL is proxied below to the gateway, so the
//     browser sees one origin in BOTH environments and the app's fetch code has
//     no environment branch at all.
//
// The Copilot generation endpoint is cross-origin in both environments and
// answers `Access-Control-Allow-Origin: *`, so it is called directly.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");

  // Which tenant's API the demo reads. Baked in at build time; the app also
  // accepts a `?tenant=` override at runtime so one bundle can be pointed at
  // another tenant on stage without a rebuild.
  const tenant = env.VITE_RAGSTACK_TENANT || "dev";

  // The gateway the DEV proxy forwards to. Plain http on purpose — in dev the
  // browser talks to Vite over http, so there is no mixed-content rule to break
  // and coconut:9443 is a self-signed cert.
  const gateway = env.VITE_GATEWAY_TARGET || "http://coconut.cels.anl.gov:9000";

  // Same DNS-rebinding posture as the other two configs: empty by default,
  // per-deployment via VITE_ALLOWED_HOSTS. localhost/127.0.0.1 always pass.
  const allowedRaw = env.VITE_ALLOWED_HOSTS ?? "";
  const allowedHosts =
    allowedRaw.trim() === "true"
      ? true
      : allowedRaw.split(",").map((h) => h.trim()).filter(Boolean);

  return {
    // Matches the nginx mount. Vite rewrites asset URLs against it, and the app
    // reads it back via import.meta.env.BASE_URL.
    base: env.VITE_BASE || "/ragstack/litdemo/",
    plugins: [
      react(),
      {
        // DEV ONLY. `index.html` (the tenant explorer) sits in this same project
        // root, so Vite's root resolution would serve that instead of this app.
        // Rewrite the base path to literature.html, exactly as vite.admin.config
        // does for the control plane. Registered in `configureServer` so it runs
        // before Vite's SPA fallback resolves index.html.
        name: "literature-root",
        configureServer(server) {
          server.middlewares.use((req, _res, next) => {
            const [path, query] = (req.url ?? "/").split("?");
            const base = "/ragstack/litdemo/";
            if (path === "/" || path === "/index.html" || path === base || path === base + "index.html") {
              req.url = base + "literature.html" + (query ? `?${query}` : "");
            }
            next();
          });
        },
      },
    ],
    server: {
      allowedHosts,
      // No `open`: coconut is headless, and Vite's browser launch throws an
      // uncaught `spawn xdg-open ENOENT` there. The startup banner prints the
      // URL (with the base already applied), which is all that was for.
      proxy: {
        // Mirror production's same-origin shape: the app always fetches
        // /ragstack/<tenant>/api/..., and only this proxy knows where that is.
        [`/ragstack/${tenant}/api`]: {
          target: gateway,
          changeOrigin: true,
        },
      },
    },
    // No sourcemap: this bundle is served from a PUBLIC mount. Emitting one and
    // then not deploying the .map file leaves a dangling `sourceMappingURL` in
    // the shipped JS — the browser fetches it, the SPA fallback answers with
    // index.html, and every page load logs a parse error. Not generating it is
    // the honest version; `npm run dev:literature` has full maps regardless.
    build: { outDir: "dist-literature", sourcemap: false, rollupOptions: { input: "literature.html" } },
  };
});
