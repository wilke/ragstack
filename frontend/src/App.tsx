import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useState } from "react";
import {
  clearStoredToken,
  getApiBase,
  getStoredApiKey,
  getStoredCredential,
  setStoredCredential,
} from "./api/config";
import { getTenants } from "./api/client";
import type { Credential } from "./lib/auth";
import { AccountView } from "./components/AccountView";
import { LoginView } from "./components/LoginView";
import { UserMenu } from "./components/UserMenu";
import { CompareView } from "./components/CompareView";
import { ExploreView } from "./components/ExploreView";
import { CollectionView } from "./components/CollectionView";
import { OpsDashboard } from "./components/OpsDashboard";

// SPA shell: Explore (query console, #93) + Collection (select/create -> upload)
// + Compare (multi-collection/tenant A/B eval) + Ops (store stats / deep health /
// collection administration, a slice of #95). A lightweight state toggle rather
// than a router keeps the scaffold minimal. The in-memory API key is shared
// across modules.
//
// The upload tab's LABEL is "Upload" — it names the activity, now that querying
// moved out and ingest is all the tab does. The view id and every user-facing
// string for the THING still say "collection" (the tab was called "Library"
// until renamed to match what it actually creates, POST /v1/collections; per
// docs/adr/0003-access-control.md a library IS a collection, one-to-one, so
// neither "library" nor a third name comes back).

// `login` and `account` are reachable from the header's user menu rather than
// the tab bar: they are about WHO you are, not what you are working on, and
// putting them in the tab strip would imply they are a fifth workspace.
type View = "explore" | "collection" | "compare" | "ops" | "login" | "account";

const TABS: { id: View; label: string }[] = [
  { id: "explore", label: "Explore" },
  { id: "collection", label: "Upload" },
  { id: "compare", label: "Compare" },
  { id: "ops", label: "Ops" },
];

const SUBTITLE: Record<View, string> = {
  explore: "Explore — ask the corpus, verify the sources",
  collection: "Upload — pick or create a collection, then upload PDFs",
  compare: "Compare — same query across collections, ranked side by side",
  ops: "Ops — stores, counts, and dependency health",
  login: "Sign in",
  account: "Account & preferences",
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
  const queryClient = useQueryClient();
  // The selected backend, held in App state so the whoami key below genuinely
  // tracks it. Reading getApiBase() inline would only be re-evaluated when
  // something else re-rendered App — which happens to work for a same-tab
  // switch (the switcher also sets the credential) but is an accident of object
  // identity, not reactivity.
  const [apiBase, setApiBaseState] = useState(getApiBase);

  // Cross-tab resync — MUST live here, not in the backend switcher. localStorage
  // is shared between tabs, so another tab can change the backend or sign out
  // from under this one; the listener therefore has to be mounted for as long
  // as the app is, not only while the settings screen happens to be open.
  // Without it a background tab keeps showing "signed in as …" for a backend
  // that never confirmed it, and — because no query key contains the base —
  // keeps rendering the previous deployment's collections while addressing the
  // new one. Requests still fail closed; the UI is what lies.
  useEffect(() => {
    function resync() {
      setApiBaseState(getApiBase());
      setCredentialState(getStoredCredential());
      void queryClient.invalidateQueries();
    }
    window.addEventListener("storage", resync);
    return () => window.removeEventListener("storage", resync);
  }, [queryClient]);

  // WHOAMI — the one source of truth for the header's identity. Keyed on the
  // credential AND the base, because either changing means the answer may be a
  // different person; without the base in the key, switching backends would
  // leave the previous deployment's identity on screen.
  //
  // /v1/stats/tenants is the de-facto whoami (tenant + role + auth_enabled).
  // There is no /v1/me, and inventing one would be a contract change plus both
  // implementations.
  const whoami = useQuery({
    queryKey: ["whoami", credential.mode, credential.value, apiBase],
    queryFn: () => getTenants(credential.value || undefined),
    retry: false,
  });
  const identity = whoami.data
    ? {
        tenant: whoami.data.tenant,
        role: whoami.data.role,
        auth_enabled: whoami.data.auth_enabled,
      }
    : null;

  // A credential change must invalidate everything: one principal's cached
  // collection list must never be shown to another.
  const applyCredential = (c: Credential) => {
    setCredential(c);
    void queryClient.invalidateQueries();
  };

  // Compare needs width for side-by-side columns; the others read best narrow.
  const wide = view === "compare";

  return (
    <div className={`mx-auto px-4 py-8 ${wide ? "max-w-none" : "max-w-3xl"}`}>
      <header className="mb-6">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <h1 className="text-2xl font-semibold">RAGStack Explorer</h1>
            <p className="text-sm text-gray-500">{SUBTITLE[view]}</p>
          </div>
          <UserMenu
            credential={credential}
            identity={identity}
            loading={whoami.isFetching}
            onSignIn={() => setView("login")}
            onAccount={() => setView("account")}
            onSignOut={() => {
              clearStoredToken();
              // Restore the saved API key rather than clearing it: the Account page
              // promises sign-out deletes the TOKEN, and silently dropping an
              // operator's key as a side effect is a surprise, not a safeguard.
              applyCredential({ mode: "apikey", value: getStoredApiKey() });
              setView("explore");
            }}
          />
        </div>

        {/* The backend selector lives in Account & preferences, not here:
            picking a deployment is a setting, and carrying it on every screen
            put a second sign-in entry point beside the first. */}

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

      {view === "login" ? (
        <LoginView setCredential={applyCredential} onDone={() => setView("account")} />
      ) : view === "account" ? (
        <AccountView
          credential={credential}
          identity={identity}
          onSignIn={() => setView("login")}
          onSignedOut={(c) => {
            applyCredential(c);
            setView("explore");
          }}
          onCredentialChange={setCredential}
          onBaseChange={setApiBaseState}
        />
      ) : view === "explore" ? (
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
