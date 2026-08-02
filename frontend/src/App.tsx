import { useState } from "react";
import { getStoredApiKey, setStoredApiKey } from "./api/config";
import { BackendSwitcher } from "./components/BackendSwitcher";
import { CompareView } from "./components/CompareView";
import { ExploreView } from "./components/ExploreView";
import { LibraryView } from "./components/LibraryView";
import { OpsDashboard } from "./components/OpsDashboard";

// SPA shell: Explore (query console, #93) + Compare (multi-collection/tenant A/B
// eval) + Ops (store stats / deep health, a slice of #95). A lightweight state
// toggle rather than a router keeps the scaffold minimal. The in-memory API key
// is shared across modules.

type View = "explore" | "library" | "compare" | "ops";

const TABS: { id: View; label: string }[] = [
  { id: "explore", label: "Explore" },
  { id: "library", label: "Library" },
  { id: "compare", label: "Compare" },
  { id: "ops", label: "Ops" },
];

const SUBTITLE: Record<View, string> = {
  explore: "Explore — ask the corpus, verify the sources",
  library: "Library — upload PDFs, watch them ingest, then ask",
  compare: "Compare — same query across collections, ranked side by side",
  ops: "Ops — stores, counts, and dependency health",
};

export function App() {
  // Seed the key from localStorage (set via the backend switcher) so it survives
  // reloads; persist every change so all tabs share one admin key.
  const [apiKey, setApiKeyState] = useState(getStoredApiKey);
  const setApiKey = (v: string) => {
    setApiKeyState(v);
    setStoredApiKey(v);
  };
  const [view, setView] = useState<View>("explore");

  // Compare needs width for side-by-side columns; the others read best narrow.
  const wide = view === "compare";

  return (
    <div className={`mx-auto px-4 py-8 ${wide ? "max-w-none" : "max-w-3xl"}`}>
      <header className="mb-6">
        <h1 className="text-2xl font-semibold">RAGStack Explorer</h1>
        <p className="text-sm text-gray-500">{SUBTITLE[view]}</p>

        <BackendSwitcher apiKey={apiKey} setApiKey={setApiKey} />

        <nav className="mt-4 flex gap-1 border-b border-gray-200" aria-label="Modules">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => setView(t.id)}
              aria-current={view === t.id ? "page" : undefined}
              className={`-mb-px border-b-2 px-3 py-2 text-sm font-medium ${
                view === t.id
                  ? "border-gray-900 text-gray-900"
                  : "border-transparent text-gray-500 hover:text-gray-800"
              }`}
            >
              {t.label}
            </button>
          ))}
        </nav>
      </header>

      {view === "explore" ? (
        <ExploreView apiKey={apiKey} setApiKey={setApiKey} />
      ) : view === "library" ? (
        <LibraryView apiKey={apiKey} setApiKey={setApiKey} />
      ) : view === "compare" ? (
        <CompareView apiKey={apiKey} setApiKey={setApiKey} />
      ) : (
        <OpsDashboard apiKey={apiKey} />
      )}
    </div>
  );
}
