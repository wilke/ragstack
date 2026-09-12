// Query keys + the one hook every admin view reads through.
//
// The keys are exported constants rather than inline arrays because the tests
// SEED them (`qc.setQueryData(ctlKeys.fleet(), fixture)`) to render a view
// against a recorded control-plane body with no network. A key typo would make
// those tests pass against an empty view, so there is exactly one spelling.

import { useQuery, type UseQueryOptions } from "@tanstack/react-query";
import { get } from "./http";
import type { CtlError } from "./http";
import type { LogFile } from "./types";

export const ctlKeys = {
  version: () => ["ctl", "version"] as const,
  me: () => ["ctl", "me"] as const,
  fleet: () => ["ctl", "fleet"] as const,
  doctor: () => ["ctl", "doctor"] as const,
  gateway: () => ["ctl", "gateway"] as const,
  gatewayRender: () => ["ctl", "gateway", "render"] as const,
  tenant: (name: string) => ["ctl", "tenant", name] as const,
  tenantEnv: (name: string) => ["ctl", "tenant", name, "env"] as const,
  tenantLogs: (name: string, file: LogFile, lines: number) =>
    ["ctl", "tenant", name, "logs", file, lines] as const,
};

/** The fleet poll, and the doctor poll that rides with it. */
export const FLEET_POLL_MS = 15000;
/** The logs tab's opt-in refresh. */
export const LOGS_POLL_MS = 5000;

/**
 * Poll only while the tab is visible.
 *
 * A background tab polling four tenants' health every 15 s is traffic nobody is
 * reading, against a daemon whose probes hit real stores. React Query calls
 * this per tick, so hiding the tab stops the next one; showing it again
 * restarts on the refetch-on-focus that follows.
 */
export function pollWhenVisible(ms: number): () => number | false {
  return () => (typeof document !== "undefined" && document.hidden ? false : ms);
}

/**
 * One read of the control plane.
 *
 * Retry is off: the failures this API produces are decisions (401 session gone,
 * 403 role too low, 409 refused), not flakes, and retrying a 403 three times
 * only delays the honest message.
 */
export function useCtlQuery<T>(
  key: readonly unknown[],
  path: string,
  options?: Omit<UseQueryOptions<T, CtlError, T, readonly unknown[]>, "queryKey" | "queryFn">,
) {
  return useQuery<T, CtlError, T, readonly unknown[]>({
    queryKey: key,
    queryFn: ({ signal }) => get<T>(path, signal),
    retry: false,
    ...options,
  });
}
