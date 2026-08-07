import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createElement, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { DEFAULT_CHUNK_FORM } from "../lib/chunkers";
import { ChunkStrategyPicker } from "./ChunkStrategyPicker";
import { CollectionView } from "./CollectionView";
import { AccountView } from "./AccountView";
import { LoginView } from "./LoginView";
import { UserMenu } from "./UserMenu";
import { NewCollectionForm } from "./NewCollectionForm";
import { OpsDashboard, PurgeConfirm } from "./OpsDashboard";

// Render smoke tests: no DOM, no fetch — `renderToStaticMarkup` just proves each
// screen mounts and produces the text it promises. Cheap insurance for the parts
// TypeScript can't check (a hook called conditionally, a missing Fragment around
// the multi-row table body, a component renamed in one place only).

function render(node: ReactElement): string {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return renderToStaticMarkup(createElement(QueryClientProvider, { client: qc }, node));
}

describe("static render", () => {
  it("mounts the Ops dashboard with its Collections section", () => {
    const html = render(createElement(OpsDashboard, { apiKey: "" }));
    expect(html).toContain("Collections");
    expect(html).toContain("New collection"); // the admin create control
  });

  it("mounts the demo Collection view and says 'collection', never 'library'", () => {
    const html = render(createElement(CollectionView, { apiKey: "", setApiKey: () => {} }));
    expect(html).toContain("New collection");
    expect(html.toLowerCase()).not.toContain("librar");
  });

  // The login page must never render a credential into the DOM, must say out
  // loud where the token is stored, and must not claim a sign-in it has not
  // confirmed with the server.
  it("mounts the login page: provider dropdown, password fields, honest storage copy", () => {
    const html = render(
      createElement(LoginView, { setCredential: () => {}, onDone: () => {} }),
    );
    expect(html).toContain("Identity provider");
    expect(html).toContain("BV-BRC");
    expect(html).toContain("Username");
    expect(html).toContain("localStorage"); // the XSS-exposure warning
    expect(html).toMatch(/<input[^>]*type="password"/);
    // The password must never reach RAGStack, and the page says so.
    expect(html).toContain("never sent to RAGStack");
  });

  it("lists Google as unavailable rather than hiding the seam", () => {
    const html = render(
      createElement(LoginView, { setCredential: () => {}, onDone: () => {} }),
    );
    expect(html).toContain("Google");
    expect(html).toContain("not available");
    // MG-RAST is deliberately absent until it can actually work.
    expect(html).not.toContain("MG-RAST");
  });

  it("does NOT render a password field on an insecure page", () => {
    // A warning beside a working password field is a suggestion, not a control.
    // On a plaintext page the form itself can be rewritten before it renders, so
    // "we post it over TLS" is no defence — the field must not exist.
    // The component reads window.isSecureContext; vitest's node env has no
    // window at all, so stub the object itself.
    const orig = Object.getOwnPropertyDescriptor(globalThis, "window");
    try {
      Object.defineProperty(globalThis, "window", {
        value: { isSecureContext: false },
        configurable: true,
      });
      const html = render(
        createElement(LoginView, { setCredential: () => {}, onDone: () => {} }),
      );
      expect(html).toContain("Password sign-in is unavailable here");
      expect(html).not.toMatch(/id="login-password"/);
      expect(html).not.toContain("<form");
      // ...and the alternative that still works is still offered.
      expect(html).toContain("token");
    } finally {
      if (orig) Object.defineProperty(globalThis, "window", orig);
      else delete (globalThis as Record<string, unknown>).window;
    }
  });

  it("renders the password field on a secure page", () => {
    const orig = Object.getOwnPropertyDescriptor(globalThis, "window");
    try {
      Object.defineProperty(globalThis, "window", {
        value: { isSecureContext: true },
        configurable: true,
      });
      const html = render(
        createElement(LoginView, { setCredential: () => {}, onDone: () => {} }),
      );
      expect(html).toMatch(/id="login-password"/);
      expect(html).toContain("never sent to RAGStack");
    } finally {
      if (orig) Object.defineProperty(globalThis, "window", orig);
      else delete (globalThis as Record<string, unknown>).window;
    }
  });

  // Re-established after LoginPanel was deleted: the components that RECEIVE a
  // credential must never render it. Nothing asserted this for a while.
  it("never renders a credential into the markup", () => {
    const SECRET = "un=alice@patricbrc.org|tokenid=t|sig=deadbeef";
    const identity = { tenant: "bvbrc:alice@patricbrc.org", role: "admin", auth_enabled: true };
    const menu = render(
      createElement(UserMenu, {
        credential: { mode: "bearer" as const, value: SECRET },
        identity,
        loading: false,
        onSignIn: () => {},
        onAccount: () => {},
        onSignOut: () => {},
      }),
    );
    expect(menu).not.toContain(SECRET);
    expect(menu).toContain("alice@patricbrc.org"); // the server-reported name is fine

    const account = render(
      createElement(AccountView, {
        credential: { mode: "bearer" as const, value: SECRET },
        identity,
        onSignIn: () => {},
        onSignedOut: () => {},
        onCredentialChange: () => {},
        onBaseChange: () => {},
      }),
    );
    expect(account).not.toContain(SECRET);
  });

  it("mounts the new-collection form", () => {
    const html = render(
      createElement(NewCollectionForm, {
        onCreate: () => {},
        onCancel: () => {},
        pending: false,
        error: null,
      }),
    );
    expect(html).toContain("Create collection");
  });

  it("labels size/overlap with the unit the chosen method counts in", () => {
    const chars = render(
      createElement(ChunkStrategyPicker, {
        idPrefix: "t",
        form: { ...DEFAULT_CHUNK_FORM, method: "sentence" },
        onChange: () => {},
      }),
    );
    expect(chars).toContain("Chunk size (characters)");
    expect(chars).toContain("Overlap (characters)");

    const toks = render(
      createElement(ChunkStrategyPicker, {
        idPrefix: "t",
        form: DEFAULT_CHUNK_FORM, // fixed_token
        onChange: () => {},
      }),
    );
    expect(toks).toContain("Chunk size (tokens)");
  });

  it("swaps size/overlap for the semantic tunables on a semantic method", () => {
    const html = render(
      createElement(ChunkStrategyPicker, {
        idPrefix: "t",
        form: { ...DEFAULT_CHUNK_FORM, method: "semantic_pooled" },
        onChange: () => {},
      }),
    );
    expect(html).not.toContain("Chunk size (");
    expect(html).toContain("Buffer size");
    expect(html).toContain("Breakpoint percentile");
    expect(html).toContain("Min chunk length");
  });

  // The only irreversible control in the UI. Assert its confirmation actually
  // spells out what dies and that the button starts locked — a purge gate that
  // renders enabled, or that omits the store name, is the bug that matters.
  it("spells out what the permanent delete destroys and starts locked", () => {
    const html = render(
      createElement(PurgeConfirm, {
        c: {
          id: "throwaway",
          label: "Throwaway",
          model: "test/sfr",
          dim: 8,
          default: false,
          count: 1234,
          text_count: 1234,
          provenance: { collection: "ragstack_sfr_tok256_ab12cd34" },
        },
        onCancel: () => {},
        onPurged: () => {},
      }),
    );
    expect(html).toContain("ragstack_sfr_tok256_ab12cd34"); // the physical store, by name
    expect(html).toContain("Elasticsearch index");
    expect(html).toContain("provenance manifest");
    expect(html).toContain("1,234"); // the chunk count that will be lost
    expect(html).toContain("Type "); // the typed-id gate, not just an OK button
    expect(html).toMatch(/<button[^>]*disabled[^>]*>Delete permanently<\/button>/);
  });
});
