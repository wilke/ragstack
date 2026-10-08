// Jobs: the fleet-wide list (`#/jobs`), one job's page (`#/job/<id>`), and the
// per-tenant list TenantView embeds.
//
// Every role READS jobs (a viewer gets the stripped shape: no worker, lock,
// reservations, external ids or step log paths). The step log LINES are an
// operator read (`GET /v1/jobs/{id}/steps/{n}/log`), fetched per step and only
// for an operator. The continuations — resume, continue, cancel — are
// mutations: each goes through a planless OpFlow (key prompt; a cancel that
// would roll back gets its typed confirm from the daemon's 428), and none of
// them is rendered for a viewer at all.

import { useState } from "react";
import { CtlError } from "../api/http";
import { jobContinuation } from "../api/ops";
import { ctlKeys, useCtlQuery, useJob, useJobs, useStepLogs } from "../api/queries";
import { JOB_STATES, type CtlFleet, type CtlRole, type Job, type JobState, type JobsResponse } from "../api/types";
import { since } from "../lib/format";
import { ErrorBanner } from "./ErrorBanner";
import { JobView, type JobAction } from "./JobView";
import { OpFlow } from "./OpFlow";
import { StateChip } from "./StateChip";

const EYEBROW = "font-mono text-[10px] font-medium uppercase tracking-[.12em] text-muted";
const TH = `py-1.5 pr-3 ${EYEBROW}`;
const TD = "py-1.5 pr-3 align-middle";
const SELECT = "rounded-panel border border-line bg-white px-2 py-1 font-mono text-[12px]";

/** The 403 panel: a refused read or an absent role, said the same way everywhere. */
export function OperatorRequired({ what }: { what: string }) {
  return (
    <p role="note" className="rounded-card bg-accent-soft p-3 text-[12.5px] text-accent-text">
      Operator credential required. {what}
    </p>
  );
}

export function isForbidden(error: unknown): boolean {
  return error instanceof CtlError && error.status === 403;
}

/** The job table: id, op, tenant, state, principal, created. Props only. */
export function JobsTable({
  jobs,
  onSelect,
  selectedId = null,
  showTenant = true,
}: {
  jobs: readonly Job[];
  onSelect: (id: string) => void;
  selectedId?: string | null;
  showTenant?: boolean;
}) {
  if (jobs.length === 0) return <p className="text-[12.5px] text-dim">No jobs match.</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-left">
        <thead>
          <tr className="border-b border-line">
            <th className={TH}>job</th>
            <th className={TH}>op</th>
            {showTenant && <th className={TH}>tenant</th>}
            <th className={TH}>state</th>
            <th className={TH}>principal</th>
            <th className={TH}>created</th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((j) => (
            <tr
              key={j.id}
              className={`border-b border-lineSoft ${selectedId === j.id ? "bg-accent-soft" : ""}`}
            >
              <td className={TD}>
                <button
                  type="button"
                  onClick={() => onSelect(j.id)}
                  className="font-mono text-[11.5px] text-link hover:underline"
                >
                  {j.id}
                </button>
              </td>
              <td className={`${TD} font-mono text-[11.5px] text-strong`}>{j.op}</td>
              {showTenant && (
                <td className={`${TD} font-mono text-[11.5px] text-dim`}>{j.tenant ?? "—"}</td>
              )}
              <td className={TD}>
                <StateChip kind="job" value={j.state} />
              </td>
              <td className={`${TD} font-mono text-[11.5px] text-dim`}>{j.principal}</td>
              <td className={`${TD} font-mono text-[11.5px] text-dim`} title={j.created_at}>
                {since(j.created_at)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export interface JobsFilterState {
  tenant: string;
  state: JobState | "";
}

/** The fleet-wide list with its two filters. Props only — the seam the tests mount. */
export function JobsListView({
  data,
  error,
  filter,
  tenants,
  onFilter,
  onSelect,
  onRetry,
}: {
  data: JobsResponse | undefined;
  error: unknown;
  filter: JobsFilterState;
  tenants: readonly string[];
  onFilter: (f: JobsFilterState) => void;
  onSelect: (id: string) => void;
  onRetry?: () => void;
}) {
  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <label className="font-mono text-[11px] text-dim" htmlFor="jobs-tenant">
          tenant
        </label>
        <select
          id="jobs-tenant"
          value={filter.tenant}
          onChange={(e) => onFilter({ ...filter, tenant: e.target.value })}
          className={SELECT}
        >
          <option value="">all</option>
          {tenants.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
        <label className="font-mono text-[11px] text-dim" htmlFor="jobs-state">
          state
        </label>
        <select
          id="jobs-state"
          value={filter.state}
          onChange={(e) => onFilter({ ...filter, state: e.target.value as JobState | "" })}
          className={SELECT}
        >
          <option value="">all</option>
          {JOB_STATES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        {data && (
          <span className="font-mono text-[11px] text-dim">
            {data.jobs.length} job{data.jobs.length === 1 ? "" : "s"}
            {data.truncated ? ` · first ${data.limit}, more match` : ""}
          </span>
        )}
      </div>
      {isForbidden(error) ? (
        <OperatorRequired what="The control plane refused the job list for this credential." />
      ) : error ? (
        <ErrorBanner error={error} onRetry={onRetry} />
      ) : !data ? (
        <p className="text-[12.5px] text-dim">Loading jobs…</p>
      ) : (
        <JobsTable jobs={data.jobs} onSelect={onSelect} />
      )}
    </div>
  );
}

const CONTINUATION_TITLE: Record<JobAction, string> = {
  resume: "Resume job",
  continue: "Continue to cutover",
  cancel: "Cancel job",
};

/**
 * One job: polled while live, its step logs for an operator, and the
 * continuations JobView offers — each through a planless OpFlow.
 */
export function JobDetail({
  id,
  role,
  onOpenJob,
}: {
  id: string;
  role: CtlRole;
  onOpenJob?: (id: string) => void;
}) {
  const job = useJob(id);
  const operator = role === "operator";
  const logs = useStepLogs(job.data, operator);
  const [action, setAction] = useState<JobAction | null>(null);

  if (isForbidden(job.error)) {
    return <OperatorRequired what="The control plane refused this job for this credential." />;
  }
  if (job.error) return <ErrorBanner error={job.error} onRetry={() => void job.refetch()} />;
  if (!job.data) return <p className="text-[12.5px] text-dim">Loading job {id}…</p>;

  return (
    <div className="space-y-3">
      <JobView
        job={job.data}
        role={role}
        logs={operator ? logs : undefined}
        onResume={operator ? () => setAction("resume") : undefined}
        onContinue={operator ? () => setAction("continue") : undefined}
        onCancel={operator ? () => setAction("cancel") : undefined}
      />
      {operator && action && (
        <OpFlow
          key={action}
          planless
          autoStart
          follow={false}
          op={`job-${action}`}
          title={`${CONTINUATION_TITLE[action]} ${id}`}
          destructive={action === "cancel"}
          run={(req) => jobContinuation(id, action, req)}
          onOpenJob={onOpenJob}
          onClose={() => setAction(null)}
        />
      )}
    </div>
  );
}

/** `#/jobs`: every job on the fleet, filtered by tenant and state. */
export function JobsView({ role, onOpenJob }: { role: CtlRole; onOpenJob: (id: string) => void }) {
  const [filter, setFilter] = useState<JobsFilterState>({ tenant: "", state: "" });
  const jobs = useJobs({ tenant: filter.tenant || undefined, state: filter.state || undefined });
  const fleet = useCtlQuery<CtlFleet>(ctlKeys.fleet(), "/v1/fleet");
  const tenants = fleet.data?.tenants.map((t) => t.name) ?? [];
  return (
    <div>
      <div className="bg-ink-900 px-5 py-4 md:px-8">
        <h1 className="font-display text-[20px] font-extrabold text-white">Jobs</h1>
        <span className="font-mono text-[11px] text-ink-dim">
          every mutation is a job · {role === "operator" ? "open one to follow, resume or cancel it" : "read-only for a viewer"}
        </span>
      </div>
      <div className="px-5 py-6 md:px-8">
        <JobsListView
          data={jobs.data}
          error={jobs.error}
          filter={filter}
          tenants={tenants}
          onFilter={setFilter}
          onSelect={onOpenJob}
          onRetry={() => void jobs.refetch()}
        />
      </div>
    </div>
  );
}

/** `#/job/<id>`. */
export function JobPage({
  id,
  role,
  onBack,
  onOpenJob,
}: {
  id: string;
  role: CtlRole;
  onBack: () => void;
  onOpenJob: (id: string) => void;
}) {
  return (
    <div>
      <div className="bg-ink-900 px-5 py-4 md:px-8">
        <button type="button" onClick={onBack} className="mb-1 font-mono text-[11px] text-ink-dim hover:text-white">
          ← jobs
        </button>
        <h1 className="font-mono text-[16px] font-semibold text-white">{id}</h1>
      </div>
      <div className="px-5 py-6 md:px-8">
        <JobDetail id={id} role={role} onOpenJob={onOpenJob} />
      </div>
    </div>
  );
}

/** TenantView's Jobs section: this tenant's jobs, and the selected one below. */
export function TenantJobs({
  name,
  role,
  onOpenJob,
}: {
  name: string;
  role: CtlRole;
  onOpenJob?: (id: string) => void;
}) {
  const jobs = useJobs({ tenant: name });
  const [selected, setSelected] = useState<string | null>(null);
  if (isForbidden(jobs.error)) {
    return <OperatorRequired what="The control plane refused this tenant's jobs for this credential." />;
  }
  if (jobs.error) return <ErrorBanner error={jobs.error} onRetry={() => void jobs.refetch()} />;
  if (!jobs.data) return <p className="text-[12.5px] text-dim">Loading jobs…</p>;
  return (
    <div className="space-y-4">
      <JobsTable jobs={jobs.data.jobs} onSelect={setSelected} selectedId={selected} showTenant={false} />
      {selected && <JobDetail id={selected} role={role} onOpenJob={onOpenJob} />}
    </div>
  );
}
