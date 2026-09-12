import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import ReactDOM from "react-dom/client";
import { AdminApp } from "./AdminApp";
import { applyVisionMode } from "../lib/vision";
import "../index.css";

// Stamp the stored accessible-vision preference on <html> before the first
// paint, so a viewer who needs the higher-contrast palette does not watch the
// default one flash past. The tenant app does the same at its own mount.
applyVisionMode();

// The admin bundle's entry. Separate from src/main.tsx (and mounting a
// differently-named root) so the two builds can never be mistaken for each
// other in a dist directory.
//
// `retry: false`: the control plane's failures are decisions — 401 the session
// is gone, 403 the role is too low, 409 refused — and retrying one only delays
// the honest message. `refetchOnWindowFocus` stays ON here, unlike the tenant
// app: coming back to this tab is exactly when a stale fleet reading matters.
const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, refetchOnWindowFocus: true } },
});

ReactDOM.createRoot(document.getElementById("admin-root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <AdminApp />
    </QueryClientProvider>
  </React.StrictMode>,
);
