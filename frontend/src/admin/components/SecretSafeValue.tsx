// The last line of defence against rendering a secret.
//
// The daemon already redacts: `GET /v1/tenants/{n}/env` answers `<redacted>`
// for every non-public class, logs are scrubbed before they leave, and a
// conformance test walks every read response refusing a field name that matches
// `(?i)(api_key|password|secret|token|dsn)`. This component assumes NONE of
// that. It is the client half of the same rule, and it exists because the two
// halves fail independently: a daemon bug, a proxy that serves a stale body, a
// future endpoint that forgets — any of those turns into a rendered credential
// unless the renderer itself refuses.
//
// So: the UI decides what to print from the field's NAME, never from the
// server's promise about the value.

const SECRET_NAME = /(api_key|password|secret|token|dsn)/i;

/**
 * Names that CONTAIN a secret word but are not secrets — the same allowlist the
 * contract documents. A fingerprint, a digest and a count are derived values
 * that exist precisely so the real one never has to be shown; refusing them
 * would blank the only identifying information an operator has about a key.
 */
const DERIVED = /(_fingerprint|_fingerprints|_sha256|_refs|_count|_id|_ids|_label)$/i;

/** True when a field with this name must never have its value rendered. */
export function isSecretName(name: string): boolean {
  return SECRET_NAME.test(name) && !DERIVED.test(name);
}

export const REDACTED = "<redacted>";

/**
 * Render `value`, unless `name` says it could be a credential.
 *
 * `mono` is the default because every value this renders is machine text — a
 * port, a path, a digest, an env value.
 */
export function SecretSafeValue({
  name,
  value,
  className = "",
}: {
  name: string;
  value: string | number | null | undefined;
  className?: string;
}) {
  const cls = `font-mono text-[11.5px] ${className}`;
  if (isSecretName(name)) {
    return (
      <span className={`${cls} text-faint`} title="withheld by the admin UI, whatever the API sent">
        {REDACTED}
      </span>
    );
  }
  if (value === null || value === undefined || value === "") {
    return <span className={`${cls} text-faint`}>—</span>;
  }
  return <span className={cls}>{String(value)}</span>;
}
