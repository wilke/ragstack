import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it } from "vitest";
import { App } from "./App";
import { ApiError } from "./api/client";

// APP WIRING. Everything else in this suite renders a component with props
// handed to it directly, which proves the component behaves — and proves nothing
// about whether App passes it the right values. It didn't: the header was fed a
// hardcoded `false` for the identity-check flag, and every one of those
// component tests stayed green while the shipped header told a freshly
// signed-in user they were signed out.
//
// `renderToStaticMarkup` runs no effects, so react-query never fetches; its
// optimistic first result is exactly the state the browser is in for the seconds
// whoami takes to answer. That makes the in-flight header assertable without a
// DOM, a fetch mock or a timer — and it is the state the bug lived in.
//
// The cache can also be SEEDED, which is how the answered states are reached
// here: `setQueryData` for a successful check, a built cache entry for a failed
// one. The query key must match App's exactly:
//   ["whoami", credential.mode, credential.value, apiBase]

const WHOAMI = (mode: string, value: string, base = "") => ["whoami", mode, value, base];

function fakeStorage(seed: Record<string, string> = {}) {
  const store = new Map(Object.entries(seed));
  const orig = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
  Object.defineProperty(globalThis, "localStorage", {
    value: {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => void store.set(k, v),
      removeItem: (k: string) => void store.delete(k),
    },
    configurable: true,
  });
  return () => {
    if (orig) Object.defineProperty(globalThis, "localStorage", orig);
    else delete (globalThis as Record<string, unknown>).localStorage;
  };
}

/** A whoami that answered once and is now failing — the expired-token shape. */
function seedFailedWhoami(qc: QueryClient) {
  const key = WHOAMI("apikey", "");
  qc.setQueryData(key, { tenant: "asm", role: "admin", auth_enabled: true, tenants: [] });
  qc.getQueryCache()
    .find({ queryKey: key })
    ?.setState({
      status: "error",
      error: new ApiError(401, ""),
      fetchStatus: "idle",
      errorUpdatedAt: Date.now(),
    });
}

let restore: (() => void) | null = null;
afterEach(() => {
  restore?.();
  restore = null;
});

function renderApp(
  seed?: (qc: QueryClient) => void,
  props: { initialView?: "explore" | "account" } = {},
): string {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  seed?.(qc);
  return renderToStaticMarkup(
    createElement(QueryClientProvider, { client: qc }, createElement(App, props)),
  );
}

describe("App identity wiring", () => {
  // THE REGRESSION GUARD. Reverting the header to `checking={false}` (or to any
  // constant) puts a definitive "Sign in" button back on screen during the whole
  // whoami window — and since sign-in lands on Explore, which shows no verdict
  // of its own, that button is the only identity control the user can see and it
  // leads straight back to the login form they just completed.
  it("threads the in-flight identity check into the header, not a constant", () => {
    const html = renderApp();
    expect(html).toContain("Checking…");
    // Not merely "no Sign in text" — no CONTROL. The tab strip's buttons are
    // still there, so this looks for the account corner's button specifically.
    expect(html).not.toMatch(/<button[^>]*>Sign in<\/button>/);
  });

  // The SAME flag, at the other consumer. Account is not on the default screen,
  // so a constant here was invisible to every test in this suite — and Account is
  // where the original login-loop bug was found.
  it("threads the in-flight identity check into Account, not a constant", () => {
    const html = renderApp(undefined, { initialView: "account" });
    expect(html).toContain("Checking sign-in");
    expect(html).not.toContain("You are not signed in");
  });

  it("threads a failed check into Account, not a constant", () => {
    restore = fakeStorage();
    const html = renderApp(seedFailedWhoami, { initialView: "account" });
    expect(html).toContain("Not confirmed"); // the verdict, not a stale assertion
    expect(html).toContain("credential was rejected"); // signInMessage(401)
    expect(html).not.toContain("You are not signed in");
    expect(html).toContain("Sign out"); // still escapable
  });

  // The other direction: a hardcoded `checking={true}` would pass the test above
  // and leave the header stuck on "Checking…" forever.
  it("shows the answered identity in the header once whoami has resolved", () => {
    restore = fakeStorage();
    const html = renderApp((qc) => {
      qc.setQueryData(WHOAMI("apikey", ""), {
        tenant: "asm",
        role: "admin",
        auth_enabled: true,
        tenants: [],
      });
    });
    expect(html).not.toContain("Checking…");
    expect(html).toContain("asm"); // the chip names the server-reported tenant
    expect(html).not.toMatch(/<button[^>]*>Sign in<\/button>/);
  });

  // A failed check must reach the header too. Without this wiring an expired
  // token leaves the chip asserting "Signed in as alice · role admin" (query-core
  // keeps `data` across a failed refetch) while every request 401s.
  it("threads a failed check into the header as unconfirmed, keeping the menu", () => {
    restore = fakeStorage();
    const html = renderApp(seedFailedWhoami);
    expect(html).toContain("unconfirmed");
    expect(html).not.toContain("admin ▾"); // the stale role is not re-asserted
    // The chip still opens the account menu, so Sign out stays reachable for a
    // credential that is still in localStorage and still going out.
    expect(html).toMatch(/aria-haspopup="menu"/);
  });
});

