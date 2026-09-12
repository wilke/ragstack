// One chip vocabulary for the whole dashboard.
//
// Every enum the control plane emits — tenant state, stores mode, health, unit
// ActiveState, doctor level and status, gateway txn state, route status —
// resolves to one of five TONES here, and nowhere else. The point is not tidy
// code: it is that "green" means the same thing on the fleet table, the tenant
// header and the gateway page, so an operator does not have to relearn the
// colours per screen.
//
// Tones ride the design tokens in tailwind.config.js (moss/amber/rust resolve
// through CSS variables, so the accessible-vision palette swaps them without
// touching this file).

export type ChipTone = "ok" | "warn" | "bad" | "info" | "neutral";

export type ChipKind =
  | "state" // tenant lifecycle
  | "mode" // stores_mode
  | "health" // HealthState
  | "unit" // UnitState
  | "level" // doctor finding / drift level
  | "status" // doctor status (green/yellow/red)
  | "txn" // gateway txn_state
  | "route" // route status
  | "class" // env key class
  | "role"; // viewer / operator

const TONES: Record<ChipKind, Record<string, ChipTone>> = {
  state: {
    active: "ok",
    provisioned: "info",
    stopped: "neutral",
    migrating: "warn",
    quarantined: "bad",
    decommissioned: "neutral",
  },
  // A stores mode is a FACT, not a verdict — `shared` is the live arrangement
  // for demo and asm-next and nothing is wrong with it. Only `unknown` is
  // coloured, because unconfirmed ownership is what blocks stop/purge/restore.
  mode: { dedicated: "info", shared: "neutral", mixed: "neutral", unknown: "warn" },
  health: { ok: "ok", degraded: "warn", down: "bad", unknown: "neutral", "n/a": "neutral" },
  unit: {
    active: "ok",
    activating: "warn",
    deactivating: "warn",
    inactive: "neutral",
    failed: "bad",
    "n/a": "neutral",
  },
  level: { info: "info", warn: "warn", error: "bad" },
  status: { green: "ok", yellow: "warn", red: "bad" },
  txn: { complete: "ok", repaired: "warn", incomplete: "bad", none: "neutral" },
  route: { active: "ok", maintenance: "warn", retired: "neutral" },
  class: {
    public: "neutral",
    secret: "info",
    "executable-surface": "warn",
    unsupported: "bad",
  },
  role: { operator: "info", viewer: "neutral" },
};

export function chipTone(kind: ChipKind, value: string): ChipTone {
  return TONES[kind][value] ?? "neutral";
}

const TONE_CLASS: Record<ChipTone, string> = {
  ok: "bg-mossSoft text-moss",
  warn: "bg-accent-soft text-accent-text",
  bad: "bg-rustSoft text-rust",
  info: "bg-linkSoft text-link",
  neutral: "bg-lineSoft text-dim",
};

export function StateChip({
  kind,
  value,
  title,
}: {
  kind: ChipKind;
  value: string;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={`inline-flex items-center rounded-chip px-2 py-[2px] font-mono text-[10.5px] font-medium leading-[15px] ${TONE_CLASS[chipTone(kind, value)]}`}
    >
      {value}
    </span>
  );
}

const DOT_CLASS: Record<ChipTone, string> = {
  ok: "bg-moss",
  warn: "bg-amber",
  bad: "bg-rust",
  info: "bg-link",
  neutral: "bg-faint",
};

/**
 * A health dot. `n/a` is HOLLOW, never grey-filled: a shared store the ctl only
 * observes has no verdict to give, and a filled dot of any colour would read as
 * one. The label is on `title` (and an sr-only span) so the three dots are not
 * colour-only information.
 */
export function HealthDot({ label, state }: { label: string; state: string }) {
  const na = state === "n/a";
  const tone = chipTone("health", state);
  return (
    <span className="inline-flex items-center" title={`${label}: ${state}`}>
      <span
        aria-hidden="true"
        className={`h-[9px] w-[9px] rounded-full ${
          na ? "border border-faint bg-transparent" : DOT_CLASS[tone]
        }`}
      />
      <span className="sr-only">{`${label}: ${state}`}</span>
    </span>
  );
}
