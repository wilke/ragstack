import { useRef } from "react";
import { useDismissable } from "./useDismiss";
import {
  accountName,
  type Credential,
  type IdentityFailure,
  type IdentitySummary,
} from "../lib/auth";
import { identityView } from "../lib/auth";
import { HelpTip } from "./HelpTip";
import { lookupTerm } from "../lib/glossary";

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
//
// THIS IS THE ONLY IDENTITY SURFACE ON MOST SCREENS, which is why it renders all
// four states rather than the two it used to. Sign-in lands on Explore, and
// Explore shows no verdict of its own — so a header that spells "still checking"
// as a definitive "Sign in" button hands a user who has just signed in the exact
// control that restarts the login loop, for the several seconds whoami takes.

export function UserMenu({
  credential,
  identity,
  checking,
  failure,
  loading,
  onSignIn,
  onAccount,
  onSignOut,
  dark = false,
}: {
  credential: Credential;
  identity: IdentitySummary | null;
  /** The whoami answer has not arrived yet — there is no verdict to render. */
  checking: boolean;
  /** The whoami request failed, or null when it did not. */
  failure: IdentityFailure | null;
  loading: boolean;
  onSignIn: () => void;
  onAccount: () => void;
  onSignOut: () => void;
  // Evidence's dark header chrome — flips the chip to the on-dark palette.
  dark?: boolean;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useDismissable(wrap);

  const view = identityView(credential, identity, checking, failure);

  // NOT A BUTTON, deliberately — a <span>. There is nothing to act on until the
  // check answers, and the only action this corner could offer is the one that
  // sends a freshly signed-in user back to the login form.
  if (view.state === "checking") {
    return (
      <span
        role="status"
        className={`inline-block rounded-full border px-4 py-1.5 text-sm font-medium ${
          dark ? "border-white/10 text-[#7fa4c6]" : "border-line bg-paper text-dim"
        }`}
      >
        Checking…
      </span>
    );
  }

  if (view.state === "signed-out") {
    return (
      <button
        type="button"
        onClick={onSignIn}
        className={`rounded-full border px-4 py-1.5 text-sm font-medium ${
          dark
            ? "border-white/20 text-white hover:bg-white/5"
            : "border-line bg-white text-strong hover:bg-paper"
        }`}
      >
        Sign in
      </button>
    );
  }

  // signed-in or unconfirmed. Both open the menu, because both need Sign out:
  // in the unconfirmed case the credential is still in localStorage and still
  // going out on every request, so "the check is failing" must not be a state a
  // user can only leave by clearing storage by hand.
  const unconfirmed = view.state === "unconfirmed";
  const name = accountName(identity?.tenant ?? "") || (unconfirmed ? "Not confirmed" : "");
  const initial = unconfirmed ? "!" : (name.trim()[0] ?? "?").toUpperCase();

  return (
    <div className="relative" ref={wrap}>
      {/* Closed state is the account pill-chip from the mockup header: 26px
          avatar disc (navy/yellow, inverted on dark), name, mono role + caret. */}
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-haspopup="menu"
        className={`flex items-center gap-2.5 rounded-[22px] border py-[5px] pl-[5px] pr-3 ${
          dark ? "border-white/20 hover:bg-white/5" : "border-line bg-white hover:bg-paper"
        }`}
      >
        <span
          aria-hidden="true"
          className={`flex h-[26px] w-[26px] items-center justify-center rounded-full text-[11px] font-semibold ${
            unconfirmed
              ? "bg-amber-400 text-ink-900"
              : dark
                ? "bg-accent text-ink-600"
                : "bg-ink-900 text-accent"
          }`}
        >
          {initial}
        </span>
        <span
          className={`max-w-[14rem] truncate text-[12.5px] font-medium ${
            dark ? "text-white" : "text-strong"
          }`}
        >
          {name}
        </span>
        {/* The role is the consequential bit — an admin is a superuser over
            every collection in the deployment, so it is on screen, not buried.
            While unconfirmed it is replaced rather than shown: a role read from
            a check that just failed is not a fact to put in a header. */}
        <span
          aria-hidden={unconfirmed || identity?.role ? undefined : "true"}
          className={`font-mono text-[11px] ${
            unconfirmed ? "text-amber-700" : dark ? "text-[#7fa4c6]" : "text-dim"
          }`}
        >
          {unconfirmed ? "unconfirmed " : identity?.role ? `${identity.role} ` : ""}▾
        </span>
      </button>

      {open ? (
        <div className="absolute right-0 z-10 mt-1 w-64 rounded-md border border-gray-200 bg-white p-1 shadow-lg">
          {/* The identity header sits OUTSIDE role="menu": it now holds help
              triggers, and a menu may contain only menuitems. */}
          <div className="border-b border-gray-100 px-3 py-2">
            <p className="truncate text-sm font-medium text-gray-900">{name}</p>
            <p className="truncate text-xs text-gray-500">{identity?.tenant}</p>
            {/* The role is the consequential fact and the name's provenance is the
                one that is easy to get wrong — both explained here rather than on
                the closed chip, which is a button and cannot hold another one. */}
            {/* Dropped while unconfirmed: "server-confirmed" would be a claim
                the current state contradicts. The warning below says what the
                name actually is — the last answer, not this one. */}
            {unconfirmed ? null : (
              <p className="mt-1 flex flex-wrap items-center gap-x-2 text-[11px]">
                <HelpTip label="server-confirmed" side="bottom">
                  Name and owner scope are what GET /v1/stats/tenants answered for the
                  credential this browser is sending. They are never read out of a
                  pasted token: with no identity provider enabled the server ignores
                  the token entirely and answers as the default tenant, so a
                  token-derived name would claim a sign-in that did not happen.
                </HelpTip>
                {identity?.role ? (
                  // The trigger names the CURRENT role, so the panel leads with
                  // that rather than opening on the definition of admin — which,
                  // for a non-admin, reads as a mislabelled tip.
                  <HelpTip term="admin role" label={`role ${identity.role}`} side="bottom">
                    The role the server derived from this credential is{" "}
                    <span className="font-medium">{identity.role}</span>.{" "}
                    {lookupTerm("admin role")}
                  </HelpTip>
                ) : null}
              </p>
            )}
            {/* Both the failed-check sentence and the signed-in caveat land here:
                the header is the only identity surface most screens have, so a
                fact that changes what the credential MEANS has to be readable
                without navigating to Account. */}
            {view.warning ? (
              <p className="mt-1.5 rounded border border-amber-300 bg-amber-50 p-1.5 text-[11px] text-amber-800">
                {view.warning}
              </p>
            ) : null}
            {loading ? <p className="text-xs text-gray-400">checking…</p> : null}
          </div>
          <div role="menu">
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
        </div>
      ) : null}
    </div>
  );
}
