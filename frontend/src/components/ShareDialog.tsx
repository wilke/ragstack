import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import {
  ApiError,
  createShare,
  deleteShare,
  getShares,
  type ShareRecord,
  type SharesResponse,
} from "../api/client";
import {
  PUBLIC_GRANTEE,
  collectionShareMessage,
  collectionShareRevokeMessage,
  isPublicShare,
  normalizeGranteeSubject,
  shareGranteeLabel,
} from "../lib/collections";

// Inline "Share collection" panel (issue #244). There is no modal/overlay in this
// codebase — every "dialog" is an inline bordered panel — so this mirrors
// NewCollectionForm: a `rounded-lg border bg-gray-50 p-4` block with its own
// react-query read + mutations.
//
// Sharing is owner-or-admin (#243). The Share button that opens this panel shows
// for any selected collection because GET /v1/collections does not expose
// ownership; a non-owner therefore sees the panel but every action fails with a
// 403 that collectionShareMessage / collectionShareRevokeMessage explains.
//
// The model is read-only grants (ADR-0004): a user grant, or the single
// grant-to-everyone "public" row. "Make public" is GRANT read TO @public;
// "make private" is revoking that one public row. Revocation is soft AND cascades
// along the granted_by chain, so after any revoke the whole list is refetched
// (invalidate) rather than a row being optimistically dropped.

function grantErrorMessage(error: Error): string {
  const status = error instanceof ApiError ? error.status : null;
  return collectionShareMessage(status, error.message);
}

function revokeErrorMessage(error: Error): string {
  const status = error instanceof ApiError ? error.status : null;
  return collectionShareRevokeMessage(status, error.message);
}

export function ShareDialog({
  collectionId,
  collectionLabel,
  apiKey,
  onClose,
}: {
  collectionId: string;
  collectionLabel?: string;
  apiKey: string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const shareKey = ["collection-shares", collectionId, apiKey];

  const [grantee, setGrantee] = useState("");

  // retry:false so a 401/403/404 fails fast (owner-or-admin) rather than a retry
  // storm against an endpoint the caller isn't allowed to read.
  const shares = useQuery<SharesResponse, Error>({
    queryKey: shareKey,
    queryFn: () => getShares(collectionId, apiKey || undefined),
    retry: false,
  });

  const refetchShares = () => queryClient.invalidateQueries({ queryKey: shareKey });

  // Grant to a user (or, via the public toggle, to @public). The server resolves
  // the raw grantee; we send it verbatim and only block an empty one client-side.
  const grant = useMutation<ShareRecord, Error, string>({
    mutationFn: (who) => createShare(collectionId, { grantee: who }, apiKey || undefined),
    onSuccess: async () => {
      await refetchShares();
      setGrantee("");
    },
  });

  const revoke = useMutation<void, Error, string>({
    mutationFn: (shareId) => deleteShare(collectionId, shareId, apiKey || undefined),
    // Soft revoke cascades along granted_by — refetch the whole list; never drop
    // a single row optimistically (revoking one grant may revoke several).
    onSuccess: () => refetchShares(),
  });

  const rows: ShareRecord[] = shares.data?.shares ?? [];
  const activeShares = rows.filter((s) => s.active);
  const publicRow = activeShares.find((s) => isPublicShare(s)) ?? null;
  const owner = shares.data?.owner ?? null;

  // Rows the operator can act on: user + public grants, but NOT the owner row
  // (the server 409s on revoking it — ownership is transferred, not revoked).
  const revocable = activeShares.filter((s) => s.permission !== "owner");

  const previewSubject = normalizeGranteeSubject(grantee);
  const canGrant = previewSubject !== "" && !grant.isPending;

  const submitGrant = () => {
    if (!canGrant) return;
    grant.mutate(grantee.trim());
  };

  const togglePublic = () => {
    if (grant.isPending || revoke.isPending) return;
    if (publicRow) {
      revoke.mutate(publicRow.id);
    } else {
      grant.mutate(PUBLIC_GRANTEE);
    }
  };

  const listBlocked = shares.isError ? (shares.error as Error) : null;

  return (
    <div className="mb-3 rounded-lg border border-gray-200 bg-gray-50 p-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-800">
          Share {collectionLabel ? `“${collectionLabel}”` : "collection"}
        </h3>
        <button
          type="button"
          onClick={onClose}
          className="text-xs text-gray-400 hover:text-gray-700"
          aria-label="Close share panel"
        >
          Close
        </button>
      </div>

      {/* Owner-or-admin: a non-owner can open this panel but every read/action
          401/403/404s. Show the read failure once, up top, verbatim-safe. */}
      {listBlocked ? (
        <p role="alert" className="mb-3 rounded bg-red-50 p-2 text-sm text-red-700">
          {collectionShareMessage(
            listBlocked instanceof ApiError ? listBlocked.status : null,
            listBlocked.message,
          )}
        </p>
      ) : null}

      {/* Make public / make private — a grant/revoke of the one @public row. */}
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={togglePublic}
          disabled={shares.isLoading || grant.isPending || revoke.isPending || listBlocked != null}
          className={`rounded-md border px-3 py-1.5 text-sm transition-colors disabled:opacity-50 ${
            publicRow
              ? "border-green-300 bg-green-50 text-green-700 hover:bg-green-100"
              : "border-gray-300 text-gray-700 hover:bg-white"
          }`}
        >
          {publicRow ? "Public ✓ — make private" : "Make public"}
        </button>
        <span className="text-xs text-gray-400">
          {publicRow
            ? "Everyone can read this collection. Making it private revokes the public grant."
            : "Grant read access to everyone (a grant to the public group)."}
        </span>
      </div>

      {/* Grant to a user. */}
      <div className="mb-4">
        <label htmlFor="share-grantee" className="mb-1 block text-xs font-medium text-gray-500">
          Share with a user
        </label>
        <div className="flex flex-wrap items-center gap-2">
          <input
            id="share-grantee"
            type="text"
            value={grantee}
            onChange={(e) => setGrantee(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") submitGrant();
            }}
            placeholder="e.g. alice, bvbrc:alice, or @public"
            className="min-w-[16rem] flex-1 rounded-md border border-gray-300 bg-white px-2 py-1 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
          <button
            type="button"
            onClick={submitGrant}
            disabled={!canGrant || listBlocked != null}
            className="rounded bg-blue-600 px-3 py-1.5 text-sm text-white transition-opacity hover:bg-blue-700 disabled:opacity-50"
          >
            {grant.isPending ? "Granting…" : "Grant (read)"}
          </button>
        </div>
        <p className="mt-1 text-[11px] leading-snug text-gray-400">
          A BV-BRC username, a full subject like <span className="font-mono">bvbrc:alice</span>, or{" "}
          <span className="font-mono">@public</span> for everyone.
          {grantee.trim() && previewSubject !== PUBLIC_GRANTEE ? (
            <>
              {" "}
              Grants read to <span className="font-mono text-gray-500">{previewSubject}</span>.
            </>
          ) : null}
        </p>
        {grant.isError && grant.error ? (
          <p role="alert" className="mt-2 rounded bg-red-50 p-2 text-sm text-red-700">
            {grantErrorMessage(grant.error)}
          </p>
        ) : null}
      </div>

      {/* Current shares. */}
      <div>
        <h4 className="mb-1 text-xs font-medium text-gray-500">Current shares</h4>
        {owner ? (
          <p className="mb-2 text-[11px] text-gray-400">
            Owner: <span className="font-mono text-gray-500">{owner}</span>
          </p>
        ) : null}
        {shares.isLoading ? (
          <p className="text-sm text-gray-400">Loading…</p>
        ) : revocable.length === 0 && !listBlocked ? (
          <p className="text-sm text-gray-400">
            Not shared with anyone yet — grant a user above, or make it public.
          </p>
        ) : (
          <ul className="space-y-1">
            {revocable.map((s) => (
              <li
                key={s.id}
                className="flex items-center justify-between rounded border border-gray-200 bg-white px-3 py-1.5 text-sm"
              >
                <span className="truncate">
                  <span className={isPublicShare(s) ? "font-medium text-green-700" : "text-gray-800"}>
                    {shareGranteeLabel(s)}
                  </span>
                  <span className="ml-2 text-xs text-gray-400">{s.permission}</span>
                </span>
                <button
                  type="button"
                  onClick={() => revoke.mutate(s.id)}
                  disabled={revoke.isPending}
                  className="ml-3 shrink-0 text-xs text-gray-400 hover:text-red-600 disabled:opacity-50"
                  aria-label={`Revoke ${shareGranteeLabel(s)}`}
                >
                  Revoke
                </button>
              </li>
            ))}
          </ul>
        )}
        {revoke.isError && revoke.error ? (
          <p role="alert" className="mt-2 rounded bg-red-50 p-2 text-sm text-red-700">
            {revokeErrorMessage(revoke.error)}
          </p>
        ) : null}
      </div>
    </div>
  );
}
