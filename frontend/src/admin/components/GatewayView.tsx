// The gateway page: which generation is published, whether the registry has
// moved past it, and what the next render would change.
//
// "Show diff" calls `POST /v1/gateway/render`, which a VIEWER may call — it
// renders the next generation into a scratch dir and diffs it, publishing
// nothing. There is deliberately no Apply next to it: `POST /v1/gateway/apply`
// is an operator mutation with a ctl key in its body and lands in PR-C.

import { useState } from "react";
import { ctlKeys, useCtlQuery } from "../api/queries";
import { post } from "../api/http";
import type { CtlGateway, CtlGatewayRender, GatewayRoute } from "../api/types";
import { since } from "../lib/format";
import { ErrorBanner } from "./ErrorBanner";
import { StateChip } from "./StateChip";
import { useMutation } from "@tanstack/react-query";

const TH = "py-1.5 pr-3 font-mono text-[10px] font-medium uppercase tracking-[.12em] text-muted";
const TD = "py-1.5 pr-3 align-middle";

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="mb-1 font-mono text-[10px] font-medium uppercase tracking-[.12em] text-muted">
        {label}
      </div>
      <div className="text-[12.5px] text-strong">{children}</div>
    </div>
  );
}

function RouteTable({ title, routes }: { title: string; routes: readonly GatewayRoute[] }) {
  return (
    <>
      <h3 className="mb-2 mt-7 font-mono text-[11px] font-medium uppercase tracking-[.14em] text-strong">
        {title}
      </h3>
      {routes.length === 0 ? (
        <p className="text-[12.5px] text-dim">None.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-left">
            <thead>
              <tr className="border-b border-line">
                <th className={TH}>name</th>
                <th className={TH}>api</th>
                <th className={TH}>ui</th>
                <th className={TH}>ui mode</th>
                <th className={TH}>read-only</th>
                <th className={TH}>status</th>
              </tr>
            </thead>
            <tbody>
              {routes.map((r) => (
                <tr key={`${title}-${r.name}`} className="border-b border-lineSoft">
                  <td className={`${TD} font-mono text-[12px] font-medium text-strong`}>{r.name}</td>
                  <td className={`${TD} font-mono text-[11.5px] tabular-nums`}>{r.api}</td>
                  <td className={`${TD} font-mono text-[11.5px] tabular-nums`}>{r.ui ?? "—"}</td>
                  <td className={`${TD} font-mono text-[11.5px] text-dim`}>{r.ui_mode}</td>
                  <td className={TD}>
                    {r.readonly ? (
                      <StateChip kind="level" value="info" title="gateway serves this tenant read-only" />
                    ) : (
                      <span className="font-mono text-[11.5px] text-faint">no</span>
                    )}
                  </td>
                  <td className={TD}>
                    <StateChip kind="route" value={r.status} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

/**
 * The result of `POST /v1/gateway/render`: what the next generation would
 * publish, file by file.
 *
 * Split out of `GatewayView` because it is only reachable through a mutation
 * whose success flips internal state — a string render can never get there, so
 * the `<pre>` that shows server-authored diff text had no test at all. This is
 * the seam those tests mount, the same device `TenantSection` is for the tenant
 * page.
 *
 * The diff is SERVER TEXT: whatever the generated nginx files contain. It goes
 * through JSX as a text child, so React escapes it — there is no
 * `dangerouslySetInnerHTML` in this bundle (`npm run guard:xss`) and a diff line
 * carrying markup renders as the characters it is.
 */
export function GatewayDiff({ render }: { render: CtlGatewayRender }) {
  return (
    <div className="mt-3">
      <p className="mb-2 font-mono text-[11px] text-dim">
        would publish generation {render.generation} · {render.changed ? "changed" : "no change"} ·{" "}
        {render.sha256}
      </p>
      {render.files.map((f) => (
        <div key={f.path} className="mb-3">
          <div className="mb-1 font-mono text-[11.5px] font-medium text-strong">{f.path}</div>
          <pre className="max-h-[420px] overflow-auto rounded-card bg-ink-700 p-3 font-mono text-[11.5px] leading-[1.5] text-ink-body">
            {f.diff || "(unchanged)"}
          </pre>
        </div>
      ))}
    </div>
  );
}

export function GatewayView() {
  const [showDiff, setShowDiff] = useState(false);
  const gw = useCtlQuery<CtlGateway>(ctlKeys.gateway(), "/v1/gateway");

  const render = useMutation<CtlGatewayRender, unknown, void>({
    mutationKey: ctlKeys.gatewayRender(),
    mutationFn: () => post<CtlGatewayRender>("/v1/gateway/render"),
    onSuccess: () => setShowDiff(true),
  });

  return (
    <div>
      <div className="bg-ink-900 px-5 py-4 md:px-8">
        <h1 className="font-display text-[20px] font-extrabold text-white">Gateway</h1>
        <span className="font-mono text-[11px] text-ink-dim">
          generated includes published by pointer switch · nothing here applies anything
        </span>
      </div>

      <div className="px-5 py-6 md:px-8">
        {gw.error && <ErrorBanner error={gw.error} onRetry={() => void gw.refetch()} />}
        {!gw.data && !gw.error && <p className="text-[12.5px] text-dim">Loading gateway status…</p>}

        {gw.data && (
          <>
            <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
              <Field label="generation">
                <span className="font-mono text-[13px] tabular-nums">{gw.data.generation}</span>
              </Field>
              <Field label="from registry generation">
                <span className="font-mono text-[13px] tabular-nums">
                  {gw.data.registry_generation}
                </span>
              </Field>
              <Field label="published">
                <span className="font-mono text-[11.5px]">
                  {gw.data.published_at ? since(gw.data.published_at) : "never"}
                  {gw.data.published_by ? ` · ${gw.data.published_by}` : ""}
                </span>
              </Field>
              <Field label="txn state">
                <StateChip kind="txn" value={gw.data.txn_state} />
              </Field>
              <Field label="pending diff">
                {gw.data.pending_diff ? (
                  <StateChip kind="level" value="warn" title="a render would change the published generation" />
                ) : (
                  <span className="font-mono text-[11.5px] text-dim">none</span>
                )}
              </Field>
              <Field label="nginx master">
                <span className="font-mono text-[11.5px] tabular-nums">
                  {gw.data.nginx.master_pid ?? "—"}
                  {gw.data.nginx.master_uid !== null ? ` · uid ${gw.data.nginx.master_uid}` : ""}
                </span>
              </Field>
              <Field label="config test">
                {gw.data.nginx.config_ok === null ? (
                  <span className="font-mono text-[11.5px] text-dim">never run</span>
                ) : (
                  <StateChip kind="health" value={gw.data.nginx.config_ok ? "ok" : "down"} />
                )}
              </Field>
              <Field label="last reload">
                <span className="font-mono text-[11.5px]">
                  {gw.data.nginx.last_reload_at ? since(gw.data.nginx.last_reload_at) : "never"}
                </span>
              </Field>
            </div>

            <RouteTable title="Routes" routes={gw.data.routes} />
            <RouteTable title="Legacy routes" routes={gw.data.legacy_routes} />

            <h3 className="mb-2 mt-8 font-mono text-[11px] font-medium uppercase tracking-[.14em] text-strong">
              Next generation
            </h3>
            <div className="flex flex-wrap items-center gap-3">
              <button
                type="button"
                onClick={() => (showDiff ? setShowDiff(false) : render.mutate())}
                disabled={render.isPending}
                className="rounded-panel border border-line bg-white px-3 py-1.5 text-[12.5px] font-medium text-ink-900 hover:bg-accent-soft disabled:opacity-50"
              >
                {render.isPending ? "Rendering…" : showDiff ? "Hide diff" : "Show diff"}
              </button>
              <span className="font-mono text-[11px] text-dim">
                renders into a scratch dir and diffs it — publishes nothing
              </span>
            </div>

            {render.error && <ErrorBanner error={render.error} />}

            {showDiff && render.data && <GatewayDiff render={render.data} />}
          </>
        )}
      </div>
    </div>
  );
}
