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
import { EvidenceView } from "./components/EvidenceView";
import { ExploreView } from "./components/ExploreView";
import { CollectionView } from "./components/CollectionView";
import { OpsDashboard } from "./components/OpsDashboard";
import type { RunRecord } from "./lib/run";
import { applyVisionMode } from "./lib/vision";

// SPA shell: Explore (ask the corpus) + Collections (select/create -> upload)
// + Compare (multi-collection/tenant A/B eval) + Evidence (one answer taken
// apart) + Ops (store stats / deep health / collection administration, a slice
// of #95). A lightweight state toggle rather than a router keeps the scaffold
// minimal. The in-memory API key is shared across modules.
//
// The upload tab's LABEL is "Collections" — it matches what the API creates
// (POST /v1/collections; per docs/adr/0003-access-control.md a library IS a
// collection, one-to-one), where "Upload" named just one action inside a tab
// that also creates, configures and monitors. The view id stays "collection".

// `login` and `account` are reachable from the header's user menu rather than
// the tab bar: they are about WHO you are, not what you are working on, and
// putting them in the tab strip would imply they are a sixth workspace.
type View = "explore" | "collection" | "compare" | "evidence" | "ops" | "login" | "account";

const TABS: { id: View; label: string }[] = [
  { id: "explore", label: "Explore" },
  { id: "collection", label: "Collections" },
  { id: "compare", label: "Compare" },
  { id: "evidence", label: "Evidence" },
  { id: "ops", label: "Ops" },
];

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
  // Shared run state: the most recent completed Explore query. Explore writes
  // it (via onRun) on every successful /v1/query; Evidence reads it. Held here
  // so the tabs share one record without a store or router.
  const [run, setRun] = useState<RunRecord | null>(null);
  // Query text Compare picks up on next mount — set by "Send to Compare".
  const [compareSeed, setCompareSeed] = useState<string | null>(null);
  // Which source Evidence should preselect, carried alongside the run: a
  // per-source "Evidence →" passes its 0-based index; whole-run entries pass
  // nothing and Evidence falls back to the first cited source.
  const [evidenceSource, setEvidenceSource] = useState<number | null>(null);

  // The typeof guard matters: some call sites hand this straight to onClick,
  // where the first argument is a MouseEvent, not an index.
  const openEvidence = useCallback((sourceIndex?: number) => {
    setEvidenceSource(typeof sourceIndex === "number" ? sourceIndex : null);
    setView("evidence");
  }, []);
  // Seeds Compare and navigates. Callers viewing a run other than the live one
  // (Evidence's saved-run selector) pass that run's query explicitly; otherwise
  // the current run's query is used. Same typeof guard as openEvidence.
  const sendToCompare = useCallback(
    (query?: string) => {
      setCompareSeed((prev) => (typeof query === "string" ? query : run?.query) ?? prev);
      setView("compare");
    },
    [run],
  );
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

  // Accessible-vision mode is a data attribute on <html>, so it must be stamped
  // at mount (before the user ever opens Preferences) for the stored choice to
  // apply; the Account toggle re-stamps it on change.
  useEffect(() => {
    applyVisionMode();
  }, []);

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

  // Compare and Evidence need width for side-by-side columns; Explore needs it
  // for its 660px column + 300px run rail (it caps itself at 1004px). The rest
  // read best on a narrow measure.
  const wide = view === "compare" || view === "evidence" || view === "explore";
  // Ops is full-bleed: its navy status band runs edge-to-edge flush under the
  // header, so the view owns all of its padding.
  const bleed = view === "ops";
  // Evidence is the one dark screen: page #071b2f, dark header chrome (5b).
  const dark = view === "evidence";

  return (
    <div className={`min-h-screen ${dark ? "bg-ink-700" : "bg-white"}`}>
      {/* 58px app header: wordmark · tab strip · account chip. The active tab's
          3px yellow underline sits flush on the header's bottom border, so the
          tab buttons stretch the full header height. */}
      <header
        className={`flex h-[58px] items-center gap-[26px] border-b px-[34px] ${
          dark ? "border-white/10" : "border-line"
        }`}
      >
        <div
          className={`font-display text-xl font-extrabold tracking-[-0.02em] ${
            dark ? "text-white" : "text-ink-900"
          }`}
        >
          RAG<span className={dark ? "text-accent" : "text-link"}>stack</span>
        </div>

        <nav className="flex h-full items-center gap-6 self-stretch" aria-label="Modules">
          {TABS.map((t) => {
            const active = view === t.id;
            return (
              <button
                key={t.id}
                type="button"
                onClick={() => setView(t.id)}
                aria-current={active ? "page" : undefined}
                className={`relative flex h-full items-center text-sm font-medium ${
                  active
                    ? dark
                      ? "text-white"
                      : "text-ink-900"
                    : dark
                      ? "text-[#7fa4c6] hover:text-white"
                      : "text-muted hover:text-ink-900"
                }`}
              >
                {t.label}
                {active ? (
                  <span
                    aria-hidden="true"
                    className="absolute inset-x-0 bottom-0 h-[3px] bg-accent"
                  />
                ) : null}
              </button>
            );
          })}
        </nav>

        {/* The backend selector lives in Account & preferences, not here:
            picking a deployment is a setting, and carrying it on every screen
            put a second sign-in entry point beside the first. */}
        <div className="ml-auto">
          <UserMenu
            dark={dark}
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
      </header>

      {/* Wide screens use the mockup's 30px/34px/44px page padding; the narrow
          measure keeps its original comfortable gutter. */}
      <main
        className={
          bleed ? "" : wide ? "px-[34px] pb-11 pt-[30px]" : "mx-auto max-w-3xl px-4 py-8"
        }
      >
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
          <ExploreView
            apiKey={apiKey}
            setApiKey={setApiKey}
            run={run}
            onRun={setRun}
            onOpenEvidence={openEvidence}
            onSendToCompare={sendToCompare}
          />
        ) : view === "collection" ? (
          <CollectionView apiKey={apiKey} setApiKey={setApiKey} />
        ) : view === "compare" ? (
          <CompareView apiKey={apiKey} setApiKey={setApiKey} seedQuery={compareSeed} />
        ) : view === "evidence" ? (
          <EvidenceView
            run={run}
            apiKey={apiKey}
            initialSourceIndex={evidenceSource}
            onSendToCompare={sendToCompare}
          />
        ) : (
          <OpsDashboard apiKey={apiKey} />
        )}
      </main>
    </div>
  );
}
