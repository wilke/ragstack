// The admin shell: who you are, which of the three screens you are on, and
// nothing else.
//
// No router. Three views and two deep links (`#/tenant/<name>`, `#/gateway`)
// do not justify a dependency, and the hash is what an operator pastes into a
// ticket. The hash is the single source of truth for the view: a click writes
// `location.hash` and the listener below turns it back into state, so Back
// works without any extra bookkeeping.

import { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { getServerSession, getSession, signOut, subscribeSession } from "./auth/session";
import { FleetView } from "./components/FleetView";
import { GatewayView } from "./components/GatewayView";
import { LoginView } from "./components/LoginView";
import { TenantView } from "./components/TenantView";
import { StateChip } from "./components/StateChip";
import { ctlKeys, useCtlQuery } from "./api/queries";
import type { CtlMe, CtlRole, CtlVersion } from "./api/types";
// A viewer PREFERENCE, not a credential — see the localStorage paragraph in
// auth/session.ts. It is the only localStorage this bundle contains.
import { getAccessibleVision, setAccessibleVision } from "../lib/vision";

export type View = { kind: "fleet" } | { kind: "tenant"; name: string } | { kind: "gateway" };

/** Tenant names are `^[a-z][a-z0-9-]{0,31}$` — anything else is not a deep link. */
const TENANT_HASH = /^#\/tenant\/([a-z][a-z0-9-]{0,31})$/;

export function parseHash(hash: string): View {
  if (hash === "#/gateway") return { kind: "gateway" };
  const m = TENANT_HASH.exec(hash);
  if (m) return { kind: "tenant", name: m[1] };
  return { kind: "fleet" };
}

export function hashFor(view: View): string {
  if (view.kind === "gateway") return "#/gateway";
  if (view.kind === "tenant") return `#/tenant/${view.name}`;
  return "#/";
}

function subscribeHash(cb: () => void): () => void {
  window.addEventListener("hashchange", cb);
  return () => window.removeEventListener("hashchange", cb);
}

function useHash(): string {
  return useSyncExternalStore(
    subscribeHash,
    () => window.location.hash,
    () => "",
  );
}

function useSession() {
  return useSyncExternalStore(subscribeSession, getSession, getServerSession);
}

/**
 * Which role the screens act on.
 *
 * `/v1/me` is the ONLY place a client learns its role — the credential carries
 * no role claim, and the session response's `role` is the server's answer AT
 * SIGN-IN. Those disagree whenever the key's role changed since (a key
 * downgraded to viewer, an operator's admin subject removed), and the session
 * copy is the stale one. The role chip already read `/v1/me`; the Logs gate and
 * the rail hint read `session.role`, so one screen could show `viewer` while
 * the other still fetched an operator-only log.
 *
 * The session copy survives only as the value to use BEFORE `/v1/me` resolves —
 * on that first render there is no fresher answer, and it is the same claim the
 * server made moments ago. It is a display/gating hint either way: the daemon
 * answers 403 on its own authority, and TenantView renders the refusal from
 * both the role it was told and the status it got back.
 */
export function effectiveRole(
  me: Pick<CtlMe, "role"> | undefined,
  session: { role: CtlRole },
): CtlRole {
  return me?.role ?? session.role;
}

function Identity({ me, onSignOut }: { me: CtlMe | undefined; onSignOut: () => void }) {
  const version = useCtlQuery<CtlVersion>(ctlKeys.version(), "/v1/version", {
    staleTime: Infinity,
  });
  return (
    <div className="flex items-center gap-2.5">
      {me && (
        <>
          <span className="font-mono text-[11.5px] text-ink-body">{me.principal}</span>
          <StateChip kind="role" value={me.role} />
        </>
      )}
      {version.data && (
        <span className="hidden font-mono text-[10.5px] text-ink-dim md:inline">
          ctl {version.data.version}
        </span>
      )}
      <button
        type="button"
        onClick={onSignOut}
        className="rounded-panel border border-white/20 px-2.5 py-1 text-[11.5px] font-medium text-white hover:bg-white/10"
      >
        Sign out
      </button>
    </div>
  );
}

/**
 * The accessible-vision switch.
 *
 * The mode existed (src/lib/vision.ts, the `data-vision="accessible"` palette
 * in index.css) but the only control for it lived on the tenant UI's Account
 * screen, which this bundle does not ship — so on the admin pages it was
 * unreachable: the colour-vision-safe state chips and the raised contrast floor
 * could be switched on everywhere EXCEPT the dashboard whose bands and log
 * panes have the worst contrast in the app.
 *
 * `aria-pressed` rather than a checkbox: this toggles how the page looks, it
 * does not submit anything, and the pressed state is what a screen reader
 * should announce. The preference is read once into state at mount and written
 * through `setAccessibleVision`, which stamps the attribute on <html> for the
 * whole document — chips inside any view included.
 */
export function VisionToggle() {
  const [on, setOn] = useState(getAccessibleVision);
  return (
    <button
      type="button"
      aria-pressed={on}
      title="Color-vision-safe state colours and a raised small-text contrast floor. Saved in this browser."
      onClick={() => {
        const next = !on;
        setOn(next);
        setAccessibleVision(next);
      }}
      className={`rounded-panel border px-2.5 py-1 text-[11.5px] font-medium ${
        on
          ? "border-accent bg-accent/20 text-white"
          : "border-white/20 text-ink-body hover:bg-white/10"
      }`}
    >
      High contrast
    </button>
  );
}

export function AdminApp() {
  const session = useSession();
  const hash = useHash();
  const view = parseHash(hash);
  const qc = useQueryClient();

  // Hoisted out of `Identity`: the role chip, the Logs gate and the rail hint
  // must all read the SAME answer, and that answer is `/v1/me` — see
  // `effectiveRole`. `enabled` keeps it from firing on the login screen.
  const me = useCtlQuery<CtlMe>(ctlKeys.me(), "/v1/me", {
    staleTime: 60_000,
    enabled: session !== null,
  });

  const go = useCallback((next: View) => {
    window.location.hash = hashFor(next);
  }, []);

  // A sign-out (or an expired session) must not leave the previous operator's
  // fleet in the cache for whoever signs in next on this tab.
  useEffect(() => {
    if (!session) qc.clear();
  }, [session, qc]);

  if (!session) return <LoginView />;

  const role = effectiveRole(me.data, session);

  const tab = (label: string, active: boolean, onClick: () => void) => (
    <button
      key={label}
      type="button"
      onClick={onClick}
      aria-current={active ? "page" : undefined}
      className={`border-b-2 px-1 pb-1.5 text-[13px] font-medium ${
        active ? "border-accent text-white" : "border-transparent text-ink-dim hover:text-white"
      }`}
    >
      {label}
    </button>
  );

  return (
    <div className="min-h-screen bg-paper font-sans text-body">
      <header className="flex flex-wrap items-center gap-x-6 gap-y-2 bg-ink-900 px-5 pb-0 pt-3 md:px-8">
        <div className="font-display text-[14px] font-extrabold uppercase tracking-[.1em] text-white">
          ragstack <span className="text-accent">control plane</span>
        </div>
        <nav aria-label="Views" className="flex items-end gap-4">
          {tab("Fleet", view.kind === "fleet" || view.kind === "tenant", () => go({ kind: "fleet" }))}
          {tab("Gateway", view.kind === "gateway", () => go({ kind: "gateway" }))}
        </nav>
        <div className="ml-auto flex items-center gap-2.5 pb-2">
          <VisionToggle />
          <Identity me={me.data} onSignOut={() => void signOut()} />
        </div>
      </header>

      {view.kind === "fleet" && (
        <FleetView onSelectTenant={(name) => go({ kind: "tenant", name })} />
      )}
      {view.kind === "tenant" && (
        <TenantView name={view.name} role={role} onBack={() => go({ kind: "fleet" })} />
      )}
      {view.kind === "gateway" && <GatewayView />}

      <footer className="px-5 py-6 text-[11px] text-faint md:px-8">
        Read-only. Mutations re-present a control-plane key per request and land with the job engine
        (PR-C).
      </footer>
    </div>
  );
}
