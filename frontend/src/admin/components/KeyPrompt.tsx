// The per-mutation credential prompt.
//
// A session authenticates READS only. Every mutation re-presents a ctl API key
// in its body (`ctl_api_key`), typed for that one request — so the key is asked
// for here, handed to the caller, and dropped. The rules are LoginView's:
//
//   * `type="password"`, `autoComplete="off"`, `name="ctl-api-key"` — no
//     browser autofill, no password-manager capture under a site login;
//   * the input state is wiped SYNCHRONOUSLY, before the callback is even
//     called, let alone awaited — so a slow or throwing submit cannot leave a
//     re-submittable form holding a live credential (LoginView.tsx's comment on
//     why the wipe stays before the handoff applies verbatim);
//   * the key is never stored: not in a ref, not in a context, not in storage.
//
// `KeyPromptView` is the props-only seam the string-render tests use;
// `spendKey` is the submit logic, exported so the wipe-before-callback order is
// testable without a DOM.

import { useState, type FormEvent, type ReactNode } from "react";
import type { CtlError } from "../api/http";
import { ErrorBanner } from "./ErrorBanner";

const INPUT =
  "w-full rounded-panel border border-line bg-white px-3 py-2 font-mono text-sm focus:border-ink-900 focus:outline-none";

export interface KeyPromptProps {
  /** Receives the trimmed key once. It must not keep it beyond the request it sends. */
  onSubmit: (key: string) => void | Promise<void>;
  busy?: boolean;
  /** The failure of the request the key was spent on; rendered by code only. */
  error?: CtlError | null;
  title?: string;
  /** Context above the input — what the key is about to authorize. */
  children?: ReactNode;
}

/**
 * Hand `raw` to `onSubmit` exactly once, clearing the caller's copy first.
 *
 * `clear` runs before `onSubmit` is invoked. A rejection from `onSubmit` is
 * swallowed here on purpose: the caller owns the request and reports its
 * failure through the prompt's `error` prop; letting it escape would only be an
 * unhandled rejection.
 */
export function spendKey(
  raw: string,
  clear: () => void,
  onSubmit: (key: string) => void | Promise<void>,
): Promise<void> | null {
  const key = raw.trim();
  clear();
  if (!key) return null;
  try {
    return Promise.resolve(onSubmit(key)).catch(() => {});
  } catch {
    return Promise.resolve();
  }
}

export function KeyPromptView({
  value,
  onChange,
  onFormSubmit,
  busy = false,
  error = null,
  title = "Control-plane API key",
  children,
}: {
  value: string;
  onChange: (v: string) => void;
  onFormSubmit: (e: FormEvent) => void;
  busy?: boolean;
  error?: CtlError | null;
  title?: string;
  children?: ReactNode;
}) {
  return (
    <form onSubmit={onFormSubmit} className="rounded-card border border-line bg-paper p-4">
      <div className="mb-1 font-mono text-[10px] font-medium uppercase tracking-[.12em] text-muted">
        authorize
      </div>
      {children && <div className="mb-3 text-[12.5px] text-body">{children}</div>}
      <label className="mb-1.5 block text-[12px] font-medium text-strong" htmlFor="ctl-api-key-prompt">
        {title}
      </label>
      <input
        id="ctl-api-key-prompt"
        name="ctl-api-key"
        type="password"
        autoComplete="off"
        spellCheck={false}
        className={INPUT}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="ctl API key"
        disabled={busy}
      />
      <p className="mt-2 text-[11.5px] leading-relaxed text-dim">
        Sent once, in the body of this request. It is not stored; the next change asks again.
      </p>
      <button
        type="submit"
        disabled={busy || !value.trim()}
        className="mt-3 rounded-panel bg-ink-900 px-4 py-2 text-sm font-semibold text-white disabled:opacity-40"
      >
        {busy ? "Submitting…" : "Submit"}
      </button>
      {error && <ErrorBanner error={error} />}
    </form>
  );
}

export function KeyPrompt({ onSubmit, busy = false, error = null, title, children }: KeyPromptProps) {
  const [key, setKey] = useState("");
  function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    void spendKey(key, () => setKey(""), onSubmit);
  }
  return (
    <KeyPromptView
      value={key}
      onChange={setKey}
      onFormSubmit={submit}
      busy={busy}
      error={error}
      title={title}
    >
      {children}
    </KeyPromptView>
  );
}
