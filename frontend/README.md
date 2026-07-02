# RAGStack frontend

Dashboard & explorer SPA — **React + Vite + TypeScript**, calling the RAGStack
REST API. Part of the monorepo (`frontend/`, peer to `python/`, `go/`,
`contracts/`). See the tracking issue and `reports/`/the plan for the full
persona-driven design (Explore / Eval / Ops / Overview, role-gated).

This is the **Phase-1a scaffold**: a minimal query console against the existing
`/v1/query`, no new backend required.

## Prerequisites
- Node 20+ and npm.
- A running RAGStack API on `http://localhost:8000` (`make run-python`), or set
  `VITE_API_TARGET` to another host.

## Develop
```bash
cd frontend
npm install
npm run gen:api     # generate typed client from ../contracts/openapi.yaml (optional)
npm run dev         # Vite dev server on http://localhost:5173, proxies /v1 + /health
```
Enter an `X-API-Key` (or leave blank if the API is keyless in dev), type a query,
and Search.

## Scripts
- `npm run dev` — dev server (`:5173`, same-origin proxy to the API).
- `npm run build` — type-check + production build to `dist/`.
- `npm run preview` — serve the production build locally.
- `npm run typecheck` — TypeScript only.
- `npm run gen:api` — regenerate `src/api/schema.d.ts` from the OpenAPI contract.

## Deployment
Same-origin is recommended: the API serves the built `dist/` (FastAPI
`StaticFiles`) or a shared reverse proxy fronts both — one origin, no CORS, and
the future session cookie "just works". Dev uses the Vite proxy to mirror that.

## Stack (planned as modules land)
Tailwind (styling), TanStack Query (data), TanStack Router (routing), a graph
engine (Cytoscape.js / Sigma.js) for the KG explorer, and charts for the
reporting modules. The generated OpenAPI client keeps types in sync with the
contract.
