// Small, pure formatters shared by the admin views.

/** Binary units, because every byte count here comes from `du` or `statfs`. */
export function bytes(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return "—";
  const units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v >= 100 || i === 0 ? Math.round(v) : v.toFixed(1)} ${units[i]}`;
}

/**
 * "3 h ago" for an RFC 3339 stamp; `never` for null.
 *
 * `now` is a parameter so the tests are not time-dependent, and so a render can
 * pin one clock across a table instead of drifting row by row.
 */
export function since(at: string | null | undefined, now: number = Date.now()): string {
  if (!at) return "never";
  const t = Date.parse(at);
  if (Number.isNaN(t)) return at;
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 60) return `${s} s ago`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 48) return `${h} h ago`;
  return `${Math.round(h / 24)} d ago`;
}
