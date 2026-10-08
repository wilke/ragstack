// The typed-confirm rule, in one place: a destructive plan
// (`requires_confirm`) executes only when the operator has typed its
// `confirm_value` — the tenant name for tenant ops, `gateway` or `settings`
// otherwise — exactly, apart from surrounding whitespace. An empty expected
// value never confirms, so a plan that forgot its value cannot be waved
// through by an empty field.

export function typedConfirmed(typed: string, expected: string): boolean {
  return expected.length > 0 && typed.trim() === expected;
}
