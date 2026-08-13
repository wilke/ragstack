import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createElement, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { TOKEN_STORAGE_HINT } from "../lib/auth";
import { AccountView } from "./AccountView";
import { LoginView } from "./LoginView";
import { UserMenu } from "./UserMenu";

// Static markup only: the help panels are closed, so these assert the triggers
// and their accessible names — and that adding help did not weaken any of the
// honesty copy the auth surfaces are required to show.

function render(node: ReactElement): string {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return renderToStaticMarkup(createElement(QueryClientProvider, { client: qc }, node));
}

const IDENTITY = { tenant: "bvbrc:alice@patricbrc.org", role: "admin", auth_enabled: true };

const account = (mode: "bearer" | "apikey") =>
  render(
    createElement(AccountView, {
      credential: { mode, value: "x" },
      identity: IDENTITY,
      checking: false,
      failure: null,
      onSignIn: () => {},
      onSignedOut: () => {},
      onCredentialChange: () => {},
      onBaseChange: () => {},
    }),
  );

describe("Account & auth help", () => {
  it("annotates credential type, token binding, vision mode and the backend", () => {
    const html = account("bearer");
    expect(html).toContain('aria-label="About credential type"');
    expect(html).toContain('aria-label="About token binding"');
    expect(html).toContain('aria-label="About accessible vision mode"');
    expect(html).toContain('aria-label="About deployment"');
    // Every tip starts closed.
    expect(html).not.toContain('role="tooltip"');
  });

  it("keeps the Session section (and its tip) bearer-only", () => {
    const html = account("apikey");
    expect(html).not.toContain('aria-label="About token binding"');
    expect(html).toContain('aria-label="About credential type"');
  });

  it("leaves the accessibility and preference copy intact", () => {
    const html = account("bearer");
    expect(html).toContain("Color-vision friendly colors");
    // The sub-label that restated the heading's tip is gone; the fact it carried
    // still ships, once, from the glossary entry that tip renders.
    expect(html).toContain("Kept per browser, not per account");
    expect(html).toContain("nothing on this page is stored against");
  });

  // The role + provenance tips live INSIDE the dropdown (the closed chip is a
  // button and cannot hold another one), so static markup can only assert the
  // chip is still exactly one button with the server-reported name and role.
  it("keeps the closed account chip a single button", () => {
    const html = render(
      createElement(UserMenu, {
        credential: { mode: "bearer" as const, value: "x" },
        identity: IDENTITY,
        checking: false,
        failure: null,
        loading: false,
        onSignIn: () => {},
        onAccount: () => {},
        onSignOut: () => {},
      }),
    );
    expect(html.match(/<button/g)).toHaveLength(1);
    expect(html).toContain("alice@patricbrc.org");
    expect(html).toContain("admin ▾");
  });

  it("adds an issuer tip without breaking the label binding or the warnings", () => {
    const html = render(createElement(LoginView, { setCredential: () => {}, onDone: () => {} }));
    expect(html).toContain('aria-label="About identity provider"');
    expect(html).toContain('for="login-provider"'); // the <label> still labels the select
    // Storage honesty, verbatim — an apostrophe-free span of it, because static
    // markup escapes ' as &#x27;.
    expect(html).toContain("exactly as XSS-exposed as the API key");
    expect(TOKEN_STORAGE_HINT).toContain("exactly as XSS-exposed as the API key");
    expect(html).toContain("never sent to RAGStack");
    expect(html).toContain("not available"); // the Google seam is still visible
  });
});
