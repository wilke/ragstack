// "Type <value> to confirm" — the gate in front of a plan with
// `requires_confirm: true`.
//
// The look is the tenant UI's PurgeConfirm (src/components/OpsDashboard.tsx),
// copied rather than imported: that file pulls `src/api/client.ts` and the
// localStorage credential store with it, which `bundle.test.ts` forbids in this
// bundle. The colours go through the rust tokens so the accessible-vision
// palette reaches them.
//
// `expected` is the plan's `confirm_value` — the tenant name for tenant ops,
// `gateway` or `settings` otherwise — and the execute call carries it as
// `confirm`. The button stays disabled until the typed text matches.

import { useState } from "react";
import { typedConfirmed } from "../lib/confirm";
import { redactText } from "./redact";

export interface TypedConfirmProps {
  expected: string;
  onConfirm: () => void;
  onCancel: () => void;
  /** A destructive plan: the red box and the irreversibility copy. */
  destructive?: boolean;
  /** The plan's warnings, shown above the input. */
  warnings?: string[];
}

export function TypedConfirmView({
  expected,
  typed,
  onTypedChange,
  onConfirm,
  onCancel,
  destructive = false,
  warnings = [],
}: TypedConfirmProps & { typed: string; onTypedChange: (v: string) => void }) {
  const unlocked = typedConfirmed(typed, expected);
  const box = destructive
    ? "border-l-4 border-rust bg-rustSoft text-rust"
    : "border-l-4 border-accent bg-accent-soft text-accent-text";
  const inputId = `typed-confirm-${expected}`;
  return (
    <div className={`${box} rounded-panel px-4 py-3 text-sm`}>
      <p className="font-semibold">
        {destructive ? "This plan is destructive." : "This plan needs a typed confirmation."}
      </p>
      {destructive && (
        <p className="mt-1 text-xs">
          Steps marked <strong>destructive</strong> above cannot be undone by the control plane.
          Read them before you confirm.
        </p>
      )}
      {warnings.length > 0 && (
        <ul className="mt-2 list-disc space-y-0.5 pl-5 text-xs">
          {warnings.map((w, i) => (
            <li key={i}>{redactText(w)}</li>
          ))}
        </ul>
      )}
      <label htmlFor={inputId} className="mt-3 block text-xs font-medium">
        Type <code className="font-mono font-semibold">{expected}</code> to confirm:
      </label>
      <input
        id={inputId}
        type="text"
        value={typed}
        autoComplete="off"
        spellCheck={false}
        onChange={(e) => onTypedChange(e.target.value)}
        placeholder={expected}
        className="mt-1 w-64 rounded-row border border-line bg-white px-2 py-1 font-mono text-xs text-strong placeholder:text-faint focus:border-ink-900 focus:outline-none"
      />
      <div className="mt-2 flex items-center gap-2">
        <button
          type="button"
          onClick={onConfirm}
          disabled={!unlocked}
          title={unlocked ? undefined : `Type ${expected} above to enable this.`}
          className={`rounded-row px-3 py-1 text-xs font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40 ${
            destructive ? "bg-rust" : "bg-ink-900"
          }`}
        >
          {destructive ? "Confirm destructive run" : "Confirm"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="rounded-row border border-line bg-white px-3 py-1 text-xs text-strong hover:bg-paper"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

export function TypedConfirm(props: TypedConfirmProps) {
  const [typed, setTyped] = useState("");
  return (
    <TypedConfirmView
      {...props}
      typed={typed}
      onTypedChange={setTyped}
      onConfirm={() => {
        // Re-check at click time: a disabled button is a hint, not a guard.
        if (typedConfirmed(typed, props.expected)) props.onConfirm();
      }}
    />
  );
}
