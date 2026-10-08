// Query keys + the one hook every admin view reads through.
//
// The keys are exported constants rather than inline arrays because the tests
// SEED them (`qc.setQueryData(ctlKeys.fleet(), fixture)`) to render a view
// against a recorded control-plane body with no network. A key typo would make
// those tests pass against an empty view, so there is exactly one spelling.

import { useQueries, useQuery, type UseQueryOptions } from "@tanstack/react-query";
import { get } from "./http";
import type { CtlError } from "./http";
import type { Job, JobsResponse, JobState, LogFile, StepLogResponse } from "./types";
import { jobIsLive } from "../lib/opflow";

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
  // Every filter member is spelled out (null when absent) so `{}` and
  // `{tenant: undefined}` are one cache entry, not two.
  jobs: (filter: JobsFilter = {}) =>
    ["ctl", "jobs", filter.tenant ?? null, filter.state ?? null, filter.limit ?? null] as const,
  job: (id: string) => ["ctl", "job", id] as const,
  jobStepLog: (id: string, n: number) => ["ctl", "job", id, "steps", n, "log"] as const,
  audit: (filter: AuditFilter = {}) =>
    ["ctl", "audit", filter.tenant ?? null, filter.limit ?? null] as const,
  settings: () => ["ctl", "settings"] as const,
  artifacts: () => ["ctl", "artifacts"] as const,
  // NO key for `/v1/jobs/{id}/secrets`: that body is a one-shot secret and
  // must never sit in the query cache (ops.ts `fetchSecrets`, RevealOnce).
};

export interface JobsFilter {
  tenant?: string;
  state?: JobState;
  limit?: number;
}

export interface AuditFilter {
  tenant?: string;
  limit?: number;
}

function query(params: Record<string, string | number | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "") q.set(k, String(v));
  const s = q.toString();
  return s ? `?${s}` : "";
}

export const ctlPaths = {
  jobs: (f: JobsFilter = {}) => `/v1/jobs${query({ tenant: f.tenant, state: f.state, limit: f.limit })}`,
  job: (id: string) => `/v1/jobs/${encodeURIComponent(id)}`,
  jobStepLog: (id: string, n: number) => `/v1/jobs/${encodeURIComponent(id)}/steps/${n}/log`,
  audit: (f: AuditFilter = {}) => `/v1/audit${query({ tenant: f.tenant, limit: f.limit })}`,
  settings: () => "/v1/settings",
  artifacts: () => "/v1/artifacts",
};

/** The fleet poll, and the doctor poll that rides with it. */
export const FLEET_POLL_MS = 15000;
/** The logs tab's opt-in refresh. */
export const LOGS_POLL_MS = 5000;
/** A job being followed: queued, running or parked awaiting `continue`. */
export const JOB_POLL_MS = 2000;

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

/**
 * The refetch interval for one job, given its last-seen state: JOB_POLL_MS
 * (while the tab is visible) for a live state, `false` otherwise — including
 * before the first answer and after an error, so a 404 or 403 is not retried
 * every two seconds.
 */
export function jobRefetchInterval(state: JobState | null | undefined): number | false {
  if (!jobIsLive(state)) return false;
  return pollWhenVisible(JOB_POLL_MS)();
}

/** One job, polled every 2 s while it is queued, running or awaiting cutover. */
export function useJob(id: string | null, opts: { enabled?: boolean } = {}) {
  return useCtlQuery<Job>(ctlKeys.job(id ?? ""), ctlPaths.job(id ?? ""), {
    enabled: (opts.enabled ?? true) && !!id,
    refetchInterval: (q) => jobRefetchInterval(q.state.data?.state),
  });
}

/** The job list (fleet-wide, or filtered), on the fleet poll. */
export function useJobs(filter: JobsFilter = {}, opts: { enabled?: boolean } = {}) {
  return useCtlQuery<JobsResponse>(ctlKeys.jobs(filter), ctlPaths.jobs(filter), {
    enabled: opts.enabled ?? true,
    refetchInterval: pollWhenVisible(FLEET_POLL_MS),
  });
}

/**
 * The log LINES of every step of `job` that has a log, by step number — for
 * JobView's `logs` prop. A step's `log` field is a PATH (and null for a
 * viewer, whose body the daemon strips); the lines come from
 * `GET /v1/jobs/{id}/steps/{n}/log`, an OPERATOR read. So `enabled` must be
 * the role gate: a viewer fires none of these requests. Refetched on the job
 * poll while the job is live, so a running step's log grows on screen.
 */
export function useStepLogs(
  job: Job | undefined,
  enabled: boolean,
): Readonly<Record<number, readonly string[]>> {
  const steps = enabled && job ? job.steps.filter((s) => s.log !== null && s.state !== "pending") : [];
  const live = jobIsLive(job?.state);
  const results = useQueries({
    queries: steps.map((s) => ({
      queryKey: ctlKeys.jobStepLog(job!.id, s.n),
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        get<StepLogResponse>(ctlPaths.jobStepLog(job!.id, s.n), signal),
      retry: false,
      refetchInterval: live ? pollWhenVisible(JOB_POLL_MS) : (false as const),
    })),
  });
  const logs: Record<number, readonly string[]> = {};
  results.forEach((r, i) => {
    if (r.data) logs[steps[i].n] = r.data.lines;
  });
  return logs;
}
