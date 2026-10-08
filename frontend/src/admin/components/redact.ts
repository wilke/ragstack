// Client-side redaction for the server strings the mutation views must print.
//
// PlanView and JobView are the first admin views whose whole point is to show
// server text: a file preview, an argv, a step log, an error. The daemon redacts
// all of it before it leaves (`would_write[].preview` is documented as redacted
// and null for a secret file; step logs are scrubbed). As with
// `SecretSafeValue`, the client does not rely on that: the same NAME rule —
// `isSecretName` — is applied to every `NAME=value` / `NAME: value` it can find
// inside the text, to every `--secret-flag value` pair in an argv, and to the
// password half of a URL's userinfo. A value is withheld because of the name
// next to it, never because of what it looks like.
//
// This over-redacts on purpose (a public `CHUNK_MAX_TOKENS=512` loses its
// value, exactly as `SecretSafeValue` already blanks that key in the Config
// tab). An operator who needs the literal reads it on the host.

import { isSecretName, REDACTED } from "./SecretSafeValue";

/** `name` as the shared name rule sees it — CLI flags spell `_` as `-`. */
function secretish(name: string): boolean {
  return isSecretName(name.replace(/-/g, "_"));
}

// NAME, an optional closing quote (JSON keys), `=` or `:`, then a quoted or
// bare value. The NAME group must contain one of the secret words, which keeps
// the scan cheap; `isSecretName` then applies the derived-value allowlist
// (`*_fingerprint`, `*_sha256`, `secret_refs`, …).
const ASSIGNMENT =
  /([A-Za-z0-9_.-]*(?:api[_-]key|password|secret|token|dsn)[A-Za-z0-9_.-]*)(["']?\s*[=:]\s*)("[^"]*"|'[^']*'|[^\s"',;&]+)/gi;

// `--secret-flag VALUE` inside PROSE (an error message quoting a command
// line). In an argv the pair is two elements and `redactArgv` handles it.
const FLAG_PAIR =
  /(--?[A-Za-z0-9_-]*(?:api[_-]key|password|secret|token|dsn)[A-Za-z0-9_-]*)(\s+)("[^"]*"|'[^']*'|[^\s"',;&-][^\s"',;&]*)/gi;

// scheme://user:password@host — the password half, whatever the key was named.
const USERINFO = /(\b[a-z][a-z0-9+.-]*:\/\/[^\s/:@]*:)([^\s/@]+)(@)/gi;

/** Redact every secret-named assignment and URL password inside `text`. */
export function redactText(text: string): string {
  return text
    .replace(USERINFO, (_m, head: string, _pw: string, at: string) => `${head}${REDACTED}${at}`)
    .replace(ASSIGNMENT, (m, name: string, sep: string) =>
      secretish(name.replace(/^[-.]+/, "")) ? `${name}${sep}${REDACTED}` : m,
    )
    .replace(FLAG_PAIR, (m, flag: string, gap: string) =>
      secretish(flag.replace(/^-+/, "")) ? `${flag}${gap}${REDACTED}` : m,
    );
}

/** Redact a nullable server string; null/undefined stay as they are. */
export function redactMaybe(text: string | null | undefined): string | null {
  return text === null || text === undefined ? null : redactText(text);
}

/**
 * Redact an argv: the value AFTER a bare secret-named flag (`--api-key VALUE`),
 * and every element through `redactText` (`--api-key=VALUE`, `TOKEN=…` in an
 * `env` invocation, a DSN with a password).
 */
export function redactArgv(argv: readonly string[]): string[] {
  const out: string[] = [];
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    out.push(redactText(arg));
    const flag = /^--?([A-Za-z0-9_-]+)$/.exec(arg);
    if (flag && secretish(flag[1]) && i + 1 < argv.length) {
      out.push(REDACTED);
      i += 1;
    }
  }
  return out;
}

/**
 * Whether a would-be file is one whose CONTENT is a credential by name
 * (`secrets.env`, `api_keys.json`, …). Its preview is withheld entirely, even
 * though the daemon is documented to send null for exactly these.
 */
export function isSecretPath(path: string): boolean {
  const base = path.split("/").pop() ?? path;
  const stem = base.replace(/\.[A-Za-z0-9]+$/, "");
  return secretish(stem) || secretish(base);
}
