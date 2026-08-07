import { useEffect, useRef, useState } from "react";
import { accountName, type Credential, type IdentitySummary } from "../lib/auth";
import { identityView } from "../lib/auth";

// Top-right account control. Signed out it is a single "Sign in" button; signed
// in it shows WHO THE SERVER SAYS YOU ARE and opens a small menu (account,
// preferences, sign out).
//
// The name comes from GET /v1/stats/tenants — never from the pasted token. A
// BV-BRC token carries `un=`, and showing that would mean the header displays a
// name the server never confirmed: with IDENTITY_PROVIDER=none a token is
// ignored entirely and every caller is the default tenant, so a token-derived
// name would claim a sign-in that did not happen. `identityView` encodes that
// distinction and this component only renders its verdict.

export function UserMenu({
  credential,
  identity,
  loading,
  onSignIn,
  onAccount,
  onSignOut,
}: {
  credential: Credential;
  identity: IdentitySummary | null;
  loading: boolean;
  onSignIn: () => void;
  onAccount: () => void;
  onSignOut: () => void;
}) {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);

  // Close on an outside click or Escape — a menu that can only be closed by the
  // button that opened it is a trap on touch.
  useEffect(() => {
    if (!open) return;
    function onDown(e: MouseEvent) {
      if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const view = identityView(credential.mode, identity);

  if (!view.signedIn) {
    return (
      <button
        type="button"
        onClick={onSignIn}
        className="rounded border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-800 hover:bg-gray-100"
      >
        Sign in
      </button>
    );
  }

  const name = accountName(identity?.tenant ?? "");
  const initial = (name.trim()[0] ?? "?").toUpperCase();

  return (
    <div className="relative" ref={wrap}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-haspopup="menu"
        className="flex items-center gap-2 rounded border border-gray-300 bg-white px-2 py-1.5 text-sm text-gray-800 hover:bg-gray-100"
      >
        <span
          aria-hidden="true"
          className="flex h-6 w-6 items-center justify-center rounded-full bg-gray-900 text-xs font-semibold text-white"
        >
          {initial}
        </span>
        <span className="max-w-[14rem] truncate font-medium">{name}</span>
        {/* The role is the consequential bit — an admin is a superuser over
            every collection in the deployment, so it is on screen, not buried. */}
        {identity?.role ? <span className="text-gray-500">· {identity.role}</span> : null}
        <span aria-hidden="true" className="text-gray-400">
          ▾
        </span>
      </button>

      {open ? (
        <div
          role="menu"
          className="absolute right-0 z-10 mt-1 w-64 rounded-md border border-gray-200 bg-white p-1 shadow-lg"
        >
          <div className="border-b border-gray-100 px-3 py-2">
            <p className="truncate text-sm font-medium text-gray-900">{name}</p>
            <p className="truncate text-xs text-gray-500">{identity?.tenant}</p>
            {loading ? <p className="text-xs text-gray-400">checking…</p> : null}
          </div>
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false);
              onAccount();
            }}
            className="w-full rounded px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-100"
          >
            Account &amp; preferences
          </button>
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false);
              onSignOut();
            }}
            className="w-full rounded px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-100"
          >
            Sign out
          </button>
        </div>
      ) : null}
    </div>
  );
}
