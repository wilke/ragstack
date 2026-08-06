import { useCallback, useState } from "react";
import { getStoredCredential, setStoredCredential } from "./api/config";
import type { Credential } from "./lib/auth";
import { BackendSwitcher } from "./components/BackendSwitcher";
import { CompareView } from "./components/CompareView";
import { ExploreView } from "./components/ExploreView";
import { CollectionView } from "./components/CollectionView";
import { OpsDashboard } from "./components/OpsDashboard";

// SPA shell: Explore (query console, #93) + Collection (upload -> ingest -> ask)
// + Compare (multi-collection/tenant A/B eval) + Ops (store stats / deep health /
// collection administration, a slice of #95). A lightweight state toggle rather
// than a router keeps the scaffold minimal. The in-memory API key is shared
// across modules.
//
// The "Collection" tab was called "Library" until it was renamed to match what it
// actually creates (POST /v1/collections). Per docs/adr/0003-access-control.md a
// library IS a collection, one-to-one — there is no separate concept, so
// "collection" is simply the right word and the old name does not come back.

type View = "explore" | "collection" | "compare" | "ops";

const TABS: { id: View; label: string }[] = [
  { id: "explore", label: "Explore" },
  { id: "collection", label: "Collection" },
  { id: "compare", label: "Compare" },
  { id: "ops", label: "Ops" },
];

const SUBTITLE: Record<View, string> = {
  explore: "Explore — ask the corpus, verify the sources",
  collection: "Collection — upload PDFs, watch them ingest, then ask",
  compare: "Compare — same query across collections, ranked side by side",
  ops: "Ops — stores, counts, and dependency health",
};

export function App() {
  // The app's ONE credential: a value plus the kind of header it becomes
  // (X-API-Key, or a bearer token pasted in the login panel). Seeded from
  // localStorage so it survives reloads, and persisted here — config.ts is the
  // single writer, so mode and value can never drift apart.
  //
  // Children still receive the opaque credential STRING as `apiKey` and forward
  // it to the client unchanged; api/client.ts resolves which header it becomes
  // from the stored mode. Typing into one of the transient key boxes therefore
  // means "I'm using an API key" and switches the mode accordingly — those boxes
  // hide themselves while a token is active (see SIGNED_IN_HINT).
  const [credential, setCredentialState] = useState<Credential>(getStoredCredential);
  // Stable identity: BackendSwitcher subscribes a `storage` listener keyed on
  // this, and a fresh function each render would re-subscribe every render.
  const setCredential = useCallback((c: Credential) => {
    setCredentialState(c);
    setStoredCredential(c);
  }, []);
  const apiKey = credential.value;
  const setApiKey = (v: string) => setCredential({ mode: "apikey", value: v });
  const [view, setView] = useState<View>("explore");

  // Compare needs width for side-by-side columns; the others read best narrow.
  const wide = view === "compare";

  return (
    <div className={`mx-auto px-4 py-8 ${wide ? "max-w-none" : "max-w-3xl"}`}>
      <header className="mb-6">
        <h1 className="text-2xl font-semibold">RAGStack Explorer</h1>
        <p className="text-sm text-gray-500">{SUBTITLE[view]}</p>

        <BackendSwitcher credential={credential} setCredential={setCredential} />

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
      ) : view === "collection" ? (
        <CollectionView apiKey={apiKey} setApiKey={setApiKey} />
      ) : view === "compare" ? (
        <CompareView apiKey={apiKey} setApiKey={setApiKey} />
      ) : (
        <OpsDashboard apiKey={apiKey} />
      )}
    </div>
  );
}
