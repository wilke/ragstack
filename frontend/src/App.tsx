import { useState } from "react";
import { ExploreView } from "./components/ExploreView";
import { OpsDashboard } from "./components/OpsDashboard";

// Two-module SPA shell: Explore (query console, #93) + Ops (store stats /
// deep health, a slice of #95). A lightweight state toggle rather than a router
// keeps the scaffold minimal. The in-memory API key is shared across modules.

type View = "explore" | "ops";

const TABS: { id: View; label: string }[] = [
  { id: "explore", label: "Explore" },
  { id: "ops", label: "Ops" },
];

export function App() {
  const [apiKey, setApiKey] = useState("");
  const [view, setView] = useState<View>("explore");

  return (
    <div className="mx-auto max-w-3xl px-4 py-8">
      <header className="mb-6">
        <h1 className="text-2xl font-semibold">RAGStack Explorer</h1>
        <p className="text-sm text-gray-500">
          {view === "explore"
            ? "Explore — ask the corpus, verify the sources"
            : "Ops — stores, counts, and dependency health"}
        </p>

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
      ) : (
        <OpsDashboard apiKey={apiKey} />
      )}
    </div>
  );
}
