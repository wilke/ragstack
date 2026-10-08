// The one place in the admin UI where a secret VALUE is put on screen.
//
// `GET /v1/jobs/{id}/secrets` is the only response in the contract that carries
// one: a minted key, delivered once — the first successful read destroys the
// envelope (every later read is 410), and the envelope itself lives 15 minutes.
// So the screen has two states and the order between them is the whole point:
//
//   1. BEFORE the click: an explanation and a "Reveal once" button. No value
//      exists on the client yet; the static render of this state contains
//      none, which the secrets canary asserts.
//   2. AFTER: each `{label, role, value}` with a copy button, under a
//      "shown once — store it now" banner.
//
// The values are deliberately NOT routed through `SecretSafeValue`: its guard is
// by field NAME, and the field here is called `value`, so it would pass them
// through anyway — while suggesting a protection that is not there. The
// protection is structural instead: this component is the only renderer of a
// `secrets_response`, and it renders only what an explicit click fetched.
//
// `RevealOnce` is pure (the parent owns the fetch: PR-G2.1's `fetchSecrets`,
// which sends the key as `X-API-Key`). `RevealOnceCard` is the thin stateful
// wrapper: it asks for the key, fetches, holds its OWN copy of the response, and
// wipes that copy on unmount. A parent that uses `RevealOnce` directly must hold
// the response in component state only (never a query cache, a ref that
// outlives the view, or storage) and drop it when the view goes away.

import { useEffect, useRef, useState, type ReactNode } from "react";
import { CtlError } from "../api/http";
import { clipboardAvailable, copyToClipboard } from "../lib/clipboard";
import { ErrorBanner } from "./ErrorBanner";
import { KeyPrompt } from "./KeyPrompt";
import type { SecretsResponse } from "../api/types";

const EYEBROW = "font-mono text-[10px] font-medium uppercase tracking-[.12em] text-muted";

/** The envelope's lifetime, from the contract (`secrets_response`). */
export const SECRETS_WINDOW_MINUTES = 15;

function CopyButton({ value }: { value: string }) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  const available = clipboardAvailable();
  return (
    <button
      type="button"
      disabled={!available}
      title={available ? "Copy to the clipboard" : "Clipboard unavailable here — select the value"}
      onClick={() => {
        void copyToClipboard(value).then((ok) => setState(ok ? "copied" : "failed"));
      }}
      className="shrink-0 rounded-row border border-line bg-white px-2 py-0.5 text-[11.5px] font-medium text-strong hover:bg-paper disabled:opacity-40"
    >
      {state === "copied" ? "Copied" : state === "failed" ? "Copy failed" : "Copy"}
    </button>
  );
}

function RevealError({ error }: { error: CtlError | null }) {
  if (!error) return null;
  // 410 arrives as code `not_found` (its `detail` says when it was delivered —
  // server prose, so not printed). Say what it means for THIS screen.
  if (error.code === "not_found") {
    return (
      <div role="alert" className="mt-3 rounded-card bg-rustSoft p-3 text-[12.5px] text-rust">
        These values were already delivered, or the {SECRETS_WINDOW_MINUTES}-minute window has
        closed. A lost value cannot be recovered: mint a new key and revoke this one.
        {error.requestId && (
          <div className="mt-1 font-mono text-[11px] opacity-80">Reference: {error.requestId}</div>
        )}
      </div>
    );
  }
  return <ErrorBanner error={error} />;
}

export interface RevealOnceProps {
  /** null until the explicit reveal has fetched them. */
  secrets: SecretsResponse | null;
  onReveal: () => void;
  revealing?: boolean;
  error?: CtlError | null;
  /** When the envelope expires, if the caller knows (e.g. job finish + 15 min). */
  expiresAt?: string;
  /** Pre-reveal slot, below the button — e.g. the KeyPrompt the reveal needs. */
  children?: ReactNode;
}

export function RevealOnce({
  secrets,
  onReveal,
  revealing = false,
  error = null,
  expiresAt,
  children,
}: RevealOnceProps) {
  if (!secrets) {
    return (
      <section aria-label="secrets" className="rounded-card border border-line bg-paper p-4">
        <div className={`mb-1 ${EYEBROW}`}>secrets · reveal once</div>
        <p className="text-[12.5px] leading-relaxed text-body">
          This job minted credentials. The control plane holds them for{" "}
          {SECRETS_WINDOW_MINUTES} minutes and hands them out <strong>exactly once</strong>: the
          first reveal destroys them on the server, and they are never written to the job, the
          audit log or this browser. Have somewhere safe to put them before you click.
        </p>
        {expiresAt && (
          <p className="mt-1 font-mono text-[11px] text-dim">expires {expiresAt}</p>
        )}
        <button
          type="button"
          onClick={onReveal}
          disabled={revealing}
          className="mt-3 rounded-panel bg-ink-900 px-4 py-2 text-sm font-semibold text-white disabled:opacity-40"
        >
          {revealing ? "Revealing…" : "Reveal once"}
        </button>
        {children && <div className="mt-3">{children}</div>}
        <RevealError error={error} />
      </section>
    );
  }
  return (
    <section aria-label="secrets" className="rounded-card border border-line bg-white p-4">
      <div
        role="status"
        className="mb-3 rounded-card border-l-4 border-rust bg-rustSoft p-3 text-[12.5px] font-semibold text-rust"
      >
        Shown once — store it now. Leaving this view discards the values; they cannot be fetched
        again.
      </div>
      <div className="mb-2 font-mono text-[11px] text-dim">
        job {secrets.job_id} · delivered {secrets.delivered_at} · expires{" "}
        {secrets.expires_at}
      </div>
      <ul className="space-y-2">
        {secrets.secrets.map((s) => (
          <li key={s.id} className="rounded-row border border-lineSoft p-2">
            <div className="mb-1 flex items-center gap-2 text-[12px]">
              <span className="font-medium text-strong">{s.label}</span>
              <span className="font-mono text-[11px] text-dim">{s.role}</span>
              <span className="font-mono text-[11px] text-faint">{s.id}</span>
            </div>
            <div className="flex items-center gap-2">
              <code className="min-w-0 flex-1 select-all break-all rounded-row bg-paper px-2 py-1 font-mono text-[11.5px] text-strong">
                {s.value}
              </code>
              <CopyButton value={s.value} />
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

/**
 * Drop every value from a response the wrapper owns. Strings cannot be zeroed
 * in JS; what can be done is to leave no reachable reference, so the rows are
 * blanked and the array emptied in place.
 */
export function wipeSecrets(owned: SecretsResponse | null): void {
  if (!owned) return;
  for (const s of owned.secrets) s.value = "";
  owned.secrets.length = 0;
}

/** A deep-enough copy that `wipeSecrets` never touches the caller's object. */
export function ownSecrets(res: SecretsResponse): SecretsResponse {
  return { ...res, secrets: res.secrets.map((s) => ({ ...s })) };
}

/**
 * Key prompt → fetch → reveal, holding the values in this component only.
 *
 * `load` receives the ctl key and performs the one read (PR-G2.1's
 * `fetchSecrets(jobId, key)`). The key is spent by KeyPrompt (wiped before the
 * call); the response is copied into state and wiped on unmount.
 */
export function RevealOnceCard({
  load,
  expiresAt,
}: {
  load: (key: string) => Promise<SecretsResponse>;
  expiresAt?: string;
}) {
  const [asking, setAsking] = useState(false);
  const [revealing, setRevealing] = useState(false);
  const [error, setError] = useState<CtlError | null>(null);
  const [secrets, setSecrets] = useState<SecretsResponse | null>(null);
  const owned = useRef<SecretsResponse | null>(null);

  useEffect(
    () => () => {
      wipeSecrets(owned.current);
      owned.current = null;
    },
    [],
  );

  async function reveal(key: string) {
    setRevealing(true);
    setError(null);
    try {
      const mine = ownSecrets(await load(key));
      owned.current = mine;
      setSecrets(mine);
      setAsking(false);
    } catch (err) {
      setError(
        err instanceof CtlError
          ? err
          : new CtlError({ status: 0, code: null, detail: "", requestId: null }),
      );
      // Back to the button: the failure is shown by RevealError, whose copy
      // knows what a 410 means here.
      setAsking(false);
    } finally {
      setRevealing(false);
    }
  }

  return (
    <RevealOnce
      secrets={secrets}
      onReveal={() => setAsking(true)}
      revealing={revealing}
      error={error}
      expiresAt={expiresAt}
    >
      {asking && (
        <KeyPrompt onSubmit={reveal} busy={revealing} title="ctl API key to reveal">
          The reveal is a credentialed read: present a control-plane key.
        </KeyPrompt>
      )}
    </RevealOnce>
  );
}
