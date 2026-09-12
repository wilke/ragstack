import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

// What the admin bundle is allowed to put on disk.
//
// The rule in src/admin/auth/session.ts is that the control-plane session lives
// in sessionStorage and NEVER localStorage: this mount shares an origin with
// every tenant UI, is internet-reachable for test/dev, and localStorage
// survives the tab, the reboot and the user walking away. That rule is about
// CREDENTIALS. One preference is explicitly allowed — the accessible-vision
// mode (src/lib/vision.ts), whose whole point is surviving the tab and whose
// stored value ("accessible") names nobody.
//
// So: exactly one module in this bundle's graph may contain `localStorage`, and
// it is that one. Asserted over the real import graph rather than by grepping
// src/admin/, because the way this breaks is an IMPORT — `api/config.ts` (the
// tenant UI's localStorage credential store) is one relative path away and used
// to be imported here for two constants. Those now live in `api/base.ts`, which
// touches no storage; if anything pulls config.ts back in, this fails.

const HERE = dirname(fileURLToPath(import.meta.url));
const ENTRY = join(HERE, "main.tsx");

/** The one module permitted to touch localStorage, and how often it does. */
const VISION = resolve(HERE, "../lib/vision.ts");

const CODE = [".ts", ".tsx"];

function resolveImport(fromFile: string, spec: string): string | null {
  if (!spec.startsWith(".")) return null; // node_modules — not ours to police
  const base = resolve(dirname(fromFile), spec);
  const candidates = [base, ...CODE.map((e) => base + e), base + ".d.ts"];
  for (const c of candidates) {
    if (existsSync(c) && statSync(c).isFile()) return c;
  }
  for (const e of CODE) {
    const idx = join(base, "index" + e);
    if (existsSync(idx)) return idx;
  }
  return null;
}

// Deliberately crude and OVER-inclusive: every `from "…"` and every bare
// `import "…"`. A guard test must not miss an edge (a multi-line import, an
// `export … from`, a side-effect import) — resolving one specifier too many
// only adds a file to the scan, which is the harmless direction.
const SPECIFIERS = [/\bfrom\s*["']([^"']+)["']/g, /\bimport\s*["']([^"']+)["']/g];

function moduleGraph(entry: string): string[] {
  const seen = new Set<string>();
  const queue = [entry];
  while (queue.length) {
    const file = queue.pop()!;
    if (seen.has(file)) continue;
    seen.add(file);
    if (!CODE.some((e) => file.endsWith(e))) continue; // don't parse .css
    const src = readFileSync(file, "utf8");
    for (const re of SPECIFIERS) {
      for (const m of src.matchAll(re)) {
        const target = resolveImport(file, m[1]);
        if (target) queue.push(target);
      }
    }
  }
  return [...seen];
}

const BLOCK_COMMENT = /\/\*[\s\S]*?\*\//g;
const LINE_COMMENT = /(^|[^:])\/\/.*$/gm;

/**
 * How many times a source actually USES localStorage.
 *
 * Comments are stripped first and only property access counts, because this
 * whole change is comments ABOUT localStorage: session.ts states the rule,
 * http.ts explains why it does not reuse the tenant client, lib/auth.ts tells
 * the user their bearer token is XSS-exposed in it. Counting the word would
 * flag every one of those and teach the next person to delete the explanation
 * to get the test green.
 */
function localStorageUses(src: string): number {
  const code = src.replace(BLOCK_COMMENT, "").replace(LINE_COMMENT, "$1");
  return (code.match(/\blocalStorage\s*(?:\.|\[)/g) ?? []).length;
}

describe("admin bundle", () => {
  const graph = moduleGraph(ENTRY);

  it("reaches the views it is supposed to (the walk is not vacuous)", () => {
    for (const f of ["AdminApp.tsx", "components/FleetView.tsx", "auth/session.ts"]) {
      expect(graph).toContain(join(HERE, f));
    }
    expect(graph.length).toBeGreaterThan(10);
  });

  it("touches localStorage in exactly one module: the vision preference", () => {
    const offenders = graph.filter(
      (f) => f !== VISION && localStorageUses(readFileSync(f, "utf8")) > 0,
    );
    expect(offenders).toEqual([]);
    expect(graph).toContain(VISION);
    // Read, write, delete — and nothing has been added since.
    expect(localStorageUses(readFileSync(VISION, "utf8"))).toBe(3);
  });

  it("never pulls the tenant UI's credential store into its graph", () => {
    expect(graph).not.toContain(resolve(HERE, "../api/config.ts"));
    expect(graph).toContain(resolve(HERE, "../api/base.ts"));
  });

  // The same assertion against the BUILT artefact, when there is a current one:
  // proof the rule survived bundling and tree shaking, not merely that it holds
  // in the source tree. `npm test` does not build, so a fresh checkout has no
  // dist-admin and this is a no-op; it earns its keep on a developer's machine
  // and after CI's `build:admin`. Skipped when the bundle is older than any
  // source in the graph — a stale build's answer is about yesterday's code.
  it("ships localStorage only from the vision module, if dist-admin is current", () => {
    const dist = resolve(HERE, "../../dist-admin/assets");
    if (!existsSync(dist)) return;
    const js = readdirSync(dist)
      .filter((f) => f.endsWith(".js"))
      .map((f) => join(dist, f));
    if (js.length === 0) return;
    const built = Math.min(...js.map((f) => statSync(f).mtimeMs));
    const newestSource = Math.max(...graph.map((f) => statSync(f).mtimeMs));
    if (built < newestSource) return; // stale bundle, nothing to say

    const total = js
      .map((f) => localStorageUses(readFileSync(f, "utf8")))
      .reduce((a, b) => a + b, 0);
    expect(total).toBe(localStorageUses(readFileSync(VISION, "utf8")));
  });
});
