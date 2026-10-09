// The Upgrade action (PR-F F6): `POST /v1/tenants/{name}/ops/update-code`,
// one OpFlow — dry run → plan → key → typed confirm (the tenant name; the op
// is destructive) → key → the job — that moves a tenant's API onto a PREPARED
// server image. A worktree-mode tenant is migrated by it; an image-mode tenant
// swaps images.
//
// Arguments (`x-ctl-op-args.update-code`):
//   image        required — one of `GET /v1/artifacts` `server_images`
//   rebuild_ui   absent = "the tenant's ui.mode is static"; sent only when the
//                operator's choice differs from that default (the convention
//                every action form follows: defaults are omitted, so the plan
//                and the audit row show what was chosen; the plan hash pins
//                the default the daemon applied)
//   artifact_id  only with a rebuild, and only an artifact at the image's commit
//
// Like the other Actions this is OPERATOR ONLY (TenantView keeps the whole
// section out of a viewer's rail). The key never touches this file.
//
// `UpgradeFields` and `UpgradeResult` are the props-only seams the string
// renders mount; `UpgradeAction` is the stateful wrapper.

import { useState } from "react";
import type { CtlError } from "../api/http";
import { submitOp, type MutationOutcome } from "../api/ops";
import { ctlKeys, useCtlQuery } from "../api/queries";
import type {
  ArtifactRow,
  ArtifactsResponse,
  CtlTenant,
  Job,
  ServerImageRow,
  TenantServerImage,
  UpdateCodeArgs,
} from "../api/types";
import { shortSha, updateCodeArgsProblem } from "../lib/validate";
import { ErrorBanner } from "./ErrorBanner";
import { OpFlow, type OpRunRequest } from "./OpFlow";
import { MatchingArtifacts, ServerImageTable } from "./ServerImagePicker";
import { redactText } from "./redact";

const EYEBROW = "mb-1 font-mono text-[10px] font-medium uppercase tracking-[.12em] text-muted";
const LABEL = "flex items-center gap-1.5 text-[12.5px] text-body";
const MONO = "font-mono text-[11.5px]";

export interface UpgradeForm {
  image: string;
  /** null = the default (`ui.mode === "static"`). */
  rebuildUi: boolean | null;
  /** "" = none. */
  artifactId: string;
}

export const EMPTY_UPGRADE: UpgradeForm = { image: "", rebuildUi: null, artifactId: "" };

type UIMode = "static" | "dev" | "external";

/** What the form needs to know about the tenant — all of it from the operator's registry row. */
export interface UpgradeTenant {
  name: string;
  uiMode: UIMode;
  /** The running image, absent in worktree mode. */
  serverImage: TenantServerImage | null;
  /** `code.tag`: the worktree's tag in worktree mode. */
  codeTag: string;
  supervisor: "systemd" | "manual" | "instance";
  state: string;
  handover: boolean;
}

/** The facts an upgrade turns on, or null when the registry row is withheld. */
export function upgradeTenant(t: CtlTenant): UpgradeTenant | null {
  const r = t.registry;
  if (!r) return null;
  return {
    name: r.name,
    uiMode: r.ui.mode,
    serverImage: r.server_image ?? null,
    codeTag: r.code.tag,
    supervisor: t.summary.supervisor,
    state: t.summary.state,
    handover: t.summary.handover_phase !== undefined,
  };
}

/** The rebuild the request means: the form's choice, or the contract's default. */
export function effectiveRebuild(f: UpgradeForm, uiMode: UIMode): boolean {
  return f.rebuildUi ?? uiMode === "static";
}

/** The request's `args`: `image`, then `rebuild_ui` only when it differs from the default, then the artifact of a rebuild. */
export function updateCodeArgs(f: UpgradeForm, uiMode: UIMode): UpdateCodeArgs {
  const rebuild = effectiveRebuild(f, uiMode);
  const a: UpdateCodeArgs = { image: f.image };
  if (rebuild !== (uiMode === "static")) a.rebuild_ui = rebuild;
  if (rebuild && f.artifactId !== "") a.artifact_id = f.artifactId;
  return a;
}

/**
 * Why Preview stays disabled, or null: the tenant-level preconditions the
 * planner refuses on (ctl-run instance supervision, active, no handover),
 * then the argument rules (`updateCodeArgsProblem`).
 */
export function upgradeProblem(
  f: UpgradeForm,
  t: UpgradeTenant | null,
  images: readonly ServerImageRow[] | null,
  artifacts: readonly ArtifactRow[] | null,
): string | null {
  if (!t) return "The registry row is not available to this credential: an upgrade is planned from it.";
  if (t.supervisor !== "instance") {
    return `${t.name} is supervised by ${t.supervisor}: a server image runs as an apptainer instance, which only the instance supervisor runs.`;
  }
  if (t.handover) return `${t.name} has a handover in flight: take it, commit it or abandon it first.`;
  if (t.state !== "active") {
    return `${t.name} is ${t.state}, not active: an upgrade proves the new API answers. Start it first (Actions → start).`;
  }
  return updateCodeArgsProblem(
    {
      image: f.image,
      rebuildUi: effectiveRebuild(f, t.uiMode),
      artifactId: f.artifactId,
      uiMode: t.uiMode,
      currentImage: t.serverImage?.name ?? null,
    },
    images,
    artifacts,
  );
}

/** "image <name> (version · commit)" or "worktree <tag>" — the API leg as TenantView's Overview says it. */
export function CurrentApi({ t }: { t: UpgradeTenant }) {
  return t.serverImage ? (
    <span className={MONO}>
      image {redactText(t.serverImage.name)}{" "}
      <span className="text-dim">
        ({redactText(t.serverImage.version)} · {shortSha(t.serverImage.commit)})
      </span>
    </span>
  ) : (
    <span className={MONO}>worktree {redactText(t.codeTag)}</span>
  );
}

export interface UpgradeFieldsProps {
  form: UpgradeForm;
  tenant: UpgradeTenant | null;
  /** null while `GET /v1/artifacts` loads. */
  images: readonly ServerImageRow[] | null;
  artifacts: readonly ArtifactRow[] | null;
  error?: CtlError | null;
  onRetry?: () => void;
  onChange: (f: UpgradeForm) => void;
}

/** The form — props only. */
export function UpgradeFields(p: UpgradeFieldsProps) {
  const { form, tenant: t } = p;
  if (!t) {
    return (
      <p className="text-[12.5px] text-dim">
        Loading the tenant&apos;s registry row… (an upgrade is planned from it; a viewer never receives it)
      </p>
    );
  }
  const rebuild = effectiveRebuild(form, t.uiMode);
  const current = t.serverImage?.name ?? null;
  const chosen = p.images?.find((i) => i.name === form.image);

  const selectImage = (name: string) => {
    const next = p.images?.find((i) => i.name === name);
    const art = p.artifacts?.find((a) => a.id === form.artifactId);
    // An artifact chosen for the previous image stays only if it is at the new one's commit.
    const keep = next && art && art.sha === next.commit;
    p.onChange({ ...form, image: name, artifactId: keep ? form.artifactId : "" });
  };
  const setRebuild = (on: boolean) =>
    p.onChange({
      ...form,
      rebuildUi: on,
      artifactId: on ? form.artifactId : "",
      // The current image is an upgrade only with a rebuild.
      image: !on && form.image === current ? "" : form.image,
    });

  return (
    <div className="space-y-3">
      <p className="text-[12.5px] text-body">
        Move the API onto a prepared server image in one job: a light pre-update bundle, the worktree
        checked out at the image&apos;s commit, the UI rebuilt (when chosen), the API stopped and started
        from the image, then proven by its health and <code className="font-mono">/v1/version</code>. Any
        failure rolls back to the old image (or worktree), the old UI and the old API running.
      </p>
      <div>
        <div className={EYEBROW}>API now</div>
        <CurrentApi t={t} />
        {!t.serverImage && (
          <p role="note" className="mt-1 text-[12px] text-accent-text">
            This tenant runs its API from its worktree: the upgrade migrates it to image mode.
          </p>
        )}
      </div>

      <div>
        <div className={EYEBROW}>server image</div>
        {p.error && <ErrorBanner error={p.error} onRetry={p.onRetry} />}
        {!p.images && !p.error && <p className="text-[12.5px] text-dim">Loading the prepared server images…</p>}
        {p.images && (
          <ServerImageTable
            images={p.images}
            selected={form.image}
            current={current}
            currentSelectable={rebuild}
            radioName="upgrade-image"
            onSelect={selectImage}
          />
        )}
      </div>

      <label className={LABEL}>
        <input
          type="checkbox"
          name="rebuild_ui"
          checked={rebuild}
          disabled={t.uiMode !== "static"}
          onChange={(e) => setRebuild(e.target.checked)}
        />
        <span>
          <span className="font-mono">Rebuild UI</span>
          {t.uiMode === "static"
            ? " — build the static UI from an artifact at the image's commit and swap it in (the previous build is kept)"
            : ` — not available: this tenant's UI is ${t.uiMode}, and nginx serves no build for it`}
        </span>
      </label>

      {rebuild && t.uiMode === "static" && (
        <div>
          <div className={EYEBROW}>artifact (the UI&apos;s source, at the image&apos;s commit)</div>
          {p.artifacts ? (
            <MatchingArtifacts
              artifacts={p.artifacts}
              image={chosen}
              selected={form.artifactId}
              radioName="upgrade-artifact"
              onSelect={(id) => p.onChange({ ...form, artifactId: id })}
            />
          ) : (
            !p.error && <p className="text-[12.5px] text-dim">Loading the prepared artifacts…</p>
          )}
        </div>
      )}
    </div>
  );
}

function str(v: unknown): string | null {
  return typeof v === "string" && v !== "" ? v : null;
}

/**
 * `result.version`: the receipt's version string in the plan. The post-check
 * step also records the `/v1/version` body under the same key, so a settled
 * job may carry that object instead; its `version` member is the same fact.
 */
function versionOf(v: unknown): string | null {
  if (typeof v === "string") return v || null;
  if (v && typeof v === "object") return str((v as Record<string, unknown>).version);
  return null;
}

/** The success panel of an `update-code` job; null for anything else. */
export function UpgradeResult({ job }: { job: Job }) {
  const r = (job.result ?? null) as Record<string, unknown> | null;
  if (!r || job.op !== "update-code" || job.state !== "succeeded") return null;
  const image = str(r.image);
  const previous = str(r.previous_image);
  const version = versionOf(r.version);
  const commit = str(r.commit);
  const artifact = str(r.artifact_id);
  const bundle = str(r.bundle);
  const migration = r.migration === true;
  return (
    <div role="note" aria-label="upgrade result" className="rounded-card border border-line bg-paper p-3">
      <div className={EYEBROW}>result</div>
      {migration && (
        <p className="mb-2 text-[12.5px] font-medium text-moss">
          Migrated to image mode: the API now runs as an apptainer instance of the image.
        </p>
      )}
      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        <Item label="image">{image ? redactText(image) : "—"}</Item>
        <Item label="version">{version ? redactText(version) : "—"}</Item>
        <Item label="commit">
          <span title={commit ?? undefined}>{commit ? shortSha(commit) : "—"}</span>
        </Item>
        <Item label="previous image">
          {previous ? redactText(previous) : migration ? "none (was worktree mode)" : "—"}
        </Item>
        <Item label="UI">{r.rebuild_ui === true ? `rebuilt from ${artifact ? redactText(artifact) : "?"}` : "kept"}</Item>
        <Item label="pre-update bundle">{bundle ? redactText(bundle) : "—"}</Item>
      </div>
    </div>
  );
}

function Item({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className={EYEBROW}>{label}</div>
      <div className={`${MONO} break-all text-strong`}>{children}</div>
    </div>
  );
}

/** The OpFlow `run` for an upgrade: the args bound, the key in the body via ops.ts. */
export function upgradeRun(name: string, args: UpdateCodeArgs): (req: OpRunRequest) => Promise<MutationOutcome> {
  return (req) => submitOp(name, "update-code", { ...req, args });
}

export function UpgradeAction({ name, onOpenJob }: { name: string; onOpenJob?: (id: string) => void }) {
  const tenantQ = useCtlQuery<CtlTenant>(ctlKeys.tenant(name), `/v1/tenants/${name}`);
  const artifactsQ = useCtlQuery<ArtifactsResponse>(ctlKeys.artifacts(), "/v1/artifacts");
  const [form, setForm] = useState<UpgradeForm>(EMPTY_UPGRADE);
  const [round, setRound] = useState(0);
  const [settled, setSettled] = useState<Job | null>(null);

  const t = tenantQ.data ? upgradeTenant(tenantQ.data) : null;
  const images = artifactsQ.data?.server_images ?? null;
  const artifacts = artifactsQ.data?.artifacts ?? null;
  const args = updateCodeArgs(form, t?.uiMode ?? "static");
  const problem = upgradeProblem(form, t, images, artifacts);

  return (
    <div className="space-y-3">
      {tenantQ.error && <ErrorBanner error={tenantQ.error} onRetry={() => void tenantQ.refetch()} />}
      <OpFlow
        key={round}
        op="update-code"
        title={`upgrade ${name}`}
        destructive
        confirmValue={name}
        run={upgradeRun(name, args)}
        disabled={problem !== null}
        disabledReason={problem ?? undefined}
        onDone={(job) => setSettled(job)}
        onOpenJob={onOpenJob}
        onClose={() => {
          setSettled(null);
          setForm(EMPTY_UPGRADE);
          setRound((r) => r + 1);
        }}
      >
        <UpgradeFields
          form={form}
          tenant={t}
          images={images}
          artifacts={artifacts}
          error={artifactsQ.error}
          onRetry={() => void artifactsQ.refetch()}
          onChange={setForm}
        />
      </OpFlow>
      {settled && <UpgradeResult job={settled} />}
    </div>
  );
}
