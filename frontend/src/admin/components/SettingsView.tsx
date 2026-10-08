// The settings page (`#/settings`): the fleet-level knobs, read by everyone,
// changed by an operator through a plan and a key.
//
// What may be written, and why only that (verified against the code, not just
// the contract):
//
//   * `contracts/ctl/openapi.yaml` `PUT /v1/settings` and
//     `go/internal/ctl/api/jobs.go` `settingsWritable`: `args` is a PARTIAL
//     settings document with only `retention`, `images` and `ctl`;
//     `recipients`, `python_env_default` and `registry_generation` are 409
//     `refused` (CLI-only).
//   * `go/internal/ctl/ops/settings.go` `persistedSettings`: the
//     `settings-put` op persists only `images` and `ctl` in this release — a
//     document naming `retention` is refused AT PLAN TIME ("the registry has
//     no field for [retention] yet"). Offering a retention editor would offer
//     a change that can only ever be refused, so retention is shown, not
//     edited, until the registry has a home for it.
//
// So an operator edits `images.{qdrant,elasticsearch}.{sif,version,digest}`
// and `ctl.{port,ui_dist,gateway_enabled}`; `settingsPatch` sends only the
// members that changed (the daemon deep-merges, an absent key is "keep").
// `settings-put` is registered non-destructive (ops/ops.go); if a plan ever
// says `requires_confirm`, OpFlow takes `confirm_value` from it.
//
// Every server string is rendered through `SecretSafeValue` under its field
// name AND `redactText` over its value: the contract has no secret-named
// member here, and the client does not rely on that.

import { useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { putSettings, type SettingsPatch } from "../api/ops";
import { ctlKeys, ctlPaths, useCtlQuery } from "../api/queries";
import type { CtlRole, SettingsResponse } from "../api/types";
import { ErrorBanner } from "./ErrorBanner";
import { OpFlow } from "./OpFlow";
import { redactText } from "./redact";
import { SecretSafeValue } from "./SecretSafeValue";
import { StateChip } from "./StateChip";

const H3 = "mb-2 mt-8 font-mono text-[11px] font-medium uppercase tracking-[.14em] text-strong first:mt-0";
const EYEBROW = "mb-1 font-mono text-[10px] font-medium uppercase tracking-[.12em] text-muted";
const NOTE = "mt-2 text-[11.5px] text-dim";
const INPUT =
  "w-full max-w-[34rem] rounded-panel border border-line bg-white px-2 py-1 font-mono text-[12px] focus:border-ink-900 focus:outline-none";
const OPEN =
  "rounded-panel border border-line bg-white px-3 py-1.5 text-[12.5px] font-medium text-strong hover:bg-paper disabled:cursor-not-allowed disabled:opacity-40";

export const IMAGE_NAMES = ["qdrant", "elasticsearch"] as const;
export type ImageName = (typeof IMAGE_NAMES)[number];

/** registry.json#/$defs/Image `digest`. */
const DIGEST = /^sha256:[0-9a-f]{64}$/;
/** registry.json#/$defs/AbsPath. */
const ABS_PATH = /^\/[A-Za-z0-9._/-]*$/;

// ---------------------------------------------------------------------------
// The edit model: the writable members as the form holds them
// ---------------------------------------------------------------------------

export interface ImageDraft {
  sif: string;
  version: string;
  digest: string;
}

/**
 * The writable subset as form state. `port` is text because an input is; it
 * becomes a number in the patch. Values are the REDACTED server strings — a
 * server that leaked a credential into a version string does not get it echoed
 * into an input, and `settingsPatch` diffs against the same redacted baseline,
 * so an untouched field never re-sends `<redacted>`.
 */
export interface SettingsDraft {
  images: Record<ImageName, ImageDraft>;
  ctl: { port: string; ui_dist: string; gateway_enabled: boolean };
}

export function draftFrom(s: SettingsResponse): SettingsDraft {
  const image = (n: ImageName): ImageDraft => ({
    sif: redactText(s.images[n].sif),
    version: redactText(s.images[n].version),
    digest: redactText(s.images[n].digest),
  });
  return {
    images: { qdrant: image("qdrant"), elasticsearch: image("elasticsearch") },
    ctl: {
      port: String(s.ctl.port),
      ui_dist: redactText(s.ctl.ui_dist),
      gateway_enabled: s.ctl.gateway_enabled,
    },
  };
}

/**
 * The `PUT /v1/settings` args for `after`: ONLY the members that differ from
 * `before`, nested as the settings document nests them. `{}` when nothing
 * changed. Never contains `retention`, `recipients`, `python_env_default` or
 * `registry_generation` (see the header).
 */
export function settingsPatch(before: SettingsResponse, after: SettingsDraft): SettingsPatch {
  const base = draftFrom(before);
  const patch: SettingsPatch = {};

  for (const n of IMAGE_NAMES) {
    const changed: Partial<ImageDraft> = {};
    for (const f of ["sif", "version", "digest"] as const) {
      const v = after.images[n][f].trim();
      if (v !== base.images[n][f]) changed[f] = v;
    }
    if (Object.keys(changed).length > 0) {
      patch.images = { ...patch.images, [n]: changed };
    }
  }

  const ctl: NonNullable<SettingsPatch["ctl"]> = {};
  const port = after.ctl.port.trim();
  if (port !== base.ctl.port) ctl.port = Number(port);
  const uiDist = after.ctl.ui_dist.trim();
  if (uiDist !== base.ctl.ui_dist) ctl.ui_dist = uiDist;
  if (after.ctl.gateway_enabled !== base.ctl.gateway_enabled) ctl.gateway_enabled = after.ctl.gateway_enabled;
  if (Object.keys(ctl).length > 0) patch.ctl = ctl;

  return patch;
}

export interface SettingsProblems {
  /** Block Preview: the daemon would answer 422. */
  errors: Record<string, string>;
  /** Shown, not blocking: the daemon is the judge. */
  warnings: Record<string, string>;
}

/**
 * Client checks on the draft. Errors are the contract's hard bounds (port
 * range, a non-empty version, the digest pattern); paths are left to the
 * daemon and only WARNED about when obviously not absolute.
 */
export function settingsProblems(d: SettingsDraft): SettingsProblems {
  const errors: Record<string, string> = {};
  const warnings: Record<string, string> = {};
  const port = d.ctl.port.trim();
  if (!/^\d+$/.test(port) || Number(port) < 1024 || Number(port) > 65535) {
    errors["ctl.port"] = "an integer from 1024 to 65535";
  }
  if (!d.ctl.ui_dist.trim().startsWith("/")) warnings["ctl.ui_dist"] = "not an absolute path";
  else if (!ABS_PATH.test(d.ctl.ui_dist.trim())) warnings["ctl.ui_dist"] = "characters outside the AbsPath pattern";
  for (const n of IMAGE_NAMES) {
    const img = d.images[n];
    if (img.version.trim() === "") errors[`images.${n}.version`] = "required";
    if (!DIGEST.test(img.digest.trim())) errors[`images.${n}.digest`] = "sha256: and 64 lowercase hex";
    if (!img.sif.trim().startsWith("/")) warnings[`images.${n}.sif`] = "not an absolute path";
    else if (!ABS_PATH.test(img.sif.trim())) warnings[`images.${n}.sif`] = "characters outside the AbsPath pattern";
  }
  return { errors, warnings };
}

// ---------------------------------------------------------------------------
// Read-only rendering (everyone)
// ---------------------------------------------------------------------------

/** A server string, guarded by its field name and redacted by content. */
function Val({ name, value }: { name: string; value: string | number | boolean }) {
  const v = typeof value === "string" ? redactText(value) : String(value);
  return <SecretSafeValue name={name} value={v} />;
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-1 gap-1 border-b border-lineSoft py-1.5 md:grid-cols-[14rem_1fr]">
      <div className="font-mono text-[11.5px] text-dim">{label}</div>
      <div className="min-w-0 break-all text-[12.5px] text-strong">{children}</div>
    </div>
  );
}

function Group({ title, note, children }: { title: string; note?: ReactNode; children: ReactNode }) {
  return (
    <section aria-label={`settings: ${title}`}>
      <h3 className={H3}>{title}</h3>
      {children}
      {note && <p className={NOTE}>{note}</p>}
    </section>
  );
}

export interface SettingsPanelProps {
  settings: SettingsResponse;
  role: CtlRole;
  /** Operators only: open the editor. Absent (or a viewer) = no Edit button. */
  onEdit?: () => void;
  /** The editor is open: the Edit button is hidden. */
  editing?: boolean;
}

/** The props-only seam: every group, read-only, plus Edit for an operator. */
export function SettingsPanel({ settings: s, role, onEdit, editing }: SettingsPanelProps) {
  const canEdit = role === "operator" && Boolean(onEdit) && !editing;
  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <span className="font-mono text-[11.5px] text-dim">
          registry generation <span className="tabular-nums text-strong">{s.registry_generation}</span>
        </span>
        {canEdit && (
          <button type="button" onClick={onEdit} className={OPEN}>
            Edit
          </button>
        )}
      </div>

      <Group
        title="Retention"
        note={
          <>
            Counted over verified bundles only; the newest verified bundle is never pruned. Read-only
            here: this release persists only images and ctl, and a settings change naming retention is
            refused at plan time.
          </>
        }
      >
        <Row label="keep_last.backup">
          <Val name="retention.keep_last.backup" value={s.retention.keep_last.backup} />
        </Row>
        <Row label="keep_last.pre_update">
          <Val name="retention.keep_last.pre_update" value={s.retention.keep_last.pre_update} />
        </Row>
        <Row label="keep_partial_hours">
          <Val name="retention.keep_partial_hours" value={s.retention.keep_partial_hours} />
        </Row>
        <Row label="auto_delete">
          <span className="font-mono text-[11.5px]">false</span>
          <span className="ml-2 text-[11.5px] text-dim">
            constant in v1 — <code className="font-mono">backup prune</code> is dry-run only
          </span>
        </Row>
      </Group>

      <Group
        title="Images"
        note="Pinned by digest. A change chooses among SIFs already on disk; nothing is fetched."
      >
        {IMAGE_NAMES.map((n) => (
          <div key={n} className="mb-3">
            <div className={EYEBROW}>{n}</div>
            <Row label="sif">
              <Val name={`images.${n}.sif`} value={s.images[n].sif} />
            </Row>
            <Row label="version">
              <Val name={`images.${n}.version`} value={s.images[n].version} />
            </Row>
            <Row label="digest">
              <Val name={`images.${n}.digest`} value={s.images[n].digest} />
            </Row>
          </div>
        ))}
      </Group>

      <Group
        title="Python env"
        note="The interpreter every tenant API starts with. CLI-only: a trusted-operator change."
      >
        <Row label="python_env_default">
          <Val name="python_env_default" value={s.python_env_default} />
        </Row>
      </Group>

      <Group title="Control plane">
        <Row label="port">
          <Val name="ctl.port" value={s.ctl.port} />
        </Row>
        <Row label="ui_dist">
          <Val name="ctl.ui_dist" value={s.ctl.ui_dist} />
        </Row>
        <Row label="gateway_enabled">
          <Val name="ctl.gateway_enabled" value={s.ctl.gateway_enabled} />
        </Row>
      </Group>

      <Group
        title="Backup recipients"
        note="Read-only everywhere but the CLI: recipients decide who can decrypt every future backup."
      >
        <Row label="file">
          <Val name="recipients.file" value={s.recipients.file} />
        </Row>
        <Row label="count">
          <span className="font-mono text-[11.5px] tabular-nums">{s.recipients.count}</span>
          {s.recipients.count === 0 && (
            <span className="ml-2 inline-flex items-center gap-1.5">
              <StateChip kind="level" value="warn" />
              <span className="text-[11.5px] text-rust">
                no recipients: a backup that includes secrets fails closed
              </span>
            </span>
          )}
        </Row>
        <Row label="fingerprints">
          {s.recipients.fingerprints.length > 0 ? (
            <ul>
              {s.recipients.fingerprints.map((fp, i) => (
                <li key={`${i}-${fp}`}>
                  <Val name="recipients.fingerprints" value={fp} />
                </li>
              ))}
            </ul>
          ) : (
            <span className="text-[11.5px] text-dim">
              {role === "viewer" && s.recipients.count > 0 ? "not shown to a viewer" : "none"}
            </span>
          )}
        </Row>
      </Group>
    </div>
  );
}

// ---------------------------------------------------------------------------
// The editor (operators): a props-only seam, mounted as OpFlow's form
// ---------------------------------------------------------------------------

function Hint({ problems, field }: { problems: SettingsProblems; field: string }) {
  const err = problems.errors[field];
  const warn = problems.warnings[field];
  if (err) return <span className="text-[11px] text-rust">{err}</span>;
  if (warn) return <span className="text-[11px] text-accent-text">warning: {warn} — the daemon decides</span>;
  return null;
}

function TextField({
  field,
  label,
  value,
  problems,
  onChange,
}: {
  field: string;
  label: string;
  value: string;
  problems: SettingsProblems;
  onChange: (v: string) => void;
}) {
  return (
    <label className="block py-1">
      <span className="mb-0.5 block font-mono text-[11px] text-dim">{label}</span>
      <input
        name={field}
        type="text"
        spellCheck={false}
        autoComplete="off"
        value={value}
        aria-invalid={problems.errors[field] ? true : undefined}
        onChange={(e) => onChange(e.target.value)}
        className={INPUT}
      />
      <span className="block">
        <Hint problems={problems} field={field} />
      </span>
    </label>
  );
}

export interface SettingsEditFormProps {
  draft: SettingsDraft;
  problems: SettingsProblems;
  onChange: (next: SettingsDraft) => void;
}

export function SettingsEditForm({ draft, problems, onChange }: SettingsEditFormProps) {
  const setImage = (n: ImageName, f: keyof ImageDraft, v: string) =>
    onChange({ ...draft, images: { ...draft.images, [n]: { ...draft.images[n], [f]: v } } });
  const setCtl = <K extends keyof SettingsDraft["ctl"]>(k: K, v: SettingsDraft["ctl"][K]) =>
    onChange({ ...draft, ctl: { ...draft.ctl, [k]: v } });

  return (
    <div>
      <div className={EYEBROW}>images</div>
      <p className="mb-2 text-[11.5px] text-dim">
        A different SIF needs its own version and digest; the daemon checks the merged registry.
      </p>
      {IMAGE_NAMES.map((n) => (
        <fieldset key={n} className="mb-3 border-l-2 border-line pl-3">
          <legend className="font-mono text-[11.5px] font-medium text-strong">{n}</legend>
          <TextField
            field={`images.${n}.sif`}
            label="sif"
            value={draft.images[n].sif}
            problems={problems}
            onChange={(v) => setImage(n, "sif", v)}
          />
          <TextField
            field={`images.${n}.version`}
            label="version"
            value={draft.images[n].version}
            problems={problems}
            onChange={(v) => setImage(n, "version", v)}
          />
          <TextField
            field={`images.${n}.digest`}
            label="digest"
            value={draft.images[n].digest}
            problems={problems}
            onChange={(v) => setImage(n, "digest", v)}
          />
        </fieldset>
      ))}

      <div className={`${EYEBROW} mt-4`}>control plane</div>
      <p className="mb-2 text-[11.5px] text-dim">
        A new port or UI directory takes effect when the daemon restarts; the page you are on keeps
        talking to the current one.
      </p>
      <TextField
        field="ctl.port"
        label="port"
        value={draft.ctl.port}
        problems={problems}
        onChange={(v) => setCtl("port", v)}
      />
      <TextField
        field="ctl.ui_dist"
        label="ui_dist"
        value={draft.ctl.ui_dist}
        problems={problems}
        onChange={(v) => setCtl("ui_dist", v)}
      />
      <label className="flex items-center gap-2 py-1 text-[12.5px] text-body">
        <input
          name="ctl.gateway_enabled"
          type="checkbox"
          checked={draft.ctl.gateway_enabled}
          onChange={(e) => setCtl("gateway_enabled", e.target.checked)}
        />
        <span className="font-mono text-[11.5px]">gateway_enabled</span>
      </label>

      <p className="mt-3 text-[11.5px] text-dim">
        Retention, the Python env and the backup recipients are not editable here (see their notes).
      </p>
    </div>
  );
}

/** A one-line description of a patch, for the disabled-Preview reason and the flow title. */
export function patchSummary(patch: SettingsPatch): string[] {
  const out: string[] = [];
  for (const n of IMAGE_NAMES) {
    for (const f of Object.keys(patch.images?.[n] ?? {})) out.push(`images.${n}.${f}`);
  }
  for (const f of Object.keys(patch.ctl ?? {})) out.push(`ctl.${f}`);
  return out;
}

// ---------------------------------------------------------------------------
// The stateful page
// ---------------------------------------------------------------------------

export function SettingsView({
  role = "viewer",
  onOpenJob,
}: {
  role?: CtlRole;
  onOpenJob?: (id: string) => void;
}) {
  const qc = useQueryClient();
  const q = useCtlQuery<SettingsResponse>(ctlKeys.settings(), ctlPaths.settings());
  const [draft, setDraft] = useState<SettingsDraft | null>(null);

  const operator = role === "operator";
  const editing = operator && draft !== null && q.data !== undefined;
  const patch = editing && q.data ? settingsPatch(q.data, draft) : {};
  const problems = draft ? settingsProblems(draft) : { errors: {}, warnings: {} };
  const changed = patchSummary(patch);
  const blocked = Object.keys(problems.errors).length > 0;

  return (
    <div>
      <div className="bg-ink-900 px-5 py-4 md:px-8">
        <h1 className="font-display text-[20px] font-extrabold text-white">Settings</h1>
        <span className="font-mono text-[11px] text-ink-dim">
          fleet-level knobs ·{" "}
          {operator ? "images and ctl change through a plan and your key" : "read-only for a viewer"}
        </span>
      </div>

      <div className="px-5 py-6 md:px-8">
        {q.error && <ErrorBanner error={q.error} onRetry={() => void q.refetch()} />}
        {!q.data && !q.error && <p className="text-[12.5px] text-dim">Loading settings…</p>}

        {q.data && (
          <SettingsPanel
            settings={q.data}
            role={role}
            editing={editing}
            onEdit={operator ? () => setDraft(draftFrom(q.data as SettingsResponse)) : undefined}
          />
        )}

        {editing && draft && (
          <div className="mt-8">
            <OpFlow
              op="settings-put"
              title="Change fleet settings"
              run={(req) => putSettings({ ...req, args: patch })}
              disabled={changed.length === 0 || blocked}
              disabledReason={
                blocked ? "Fix the fields marked above first." : "Nothing changed yet."
              }
              onOpenJob={onOpenJob}
              onDone={() => {
                // The registry moved: re-read, so the baseline the next patch
                // diffs against is what was saved.
                void qc.invalidateQueries({ queryKey: ctlKeys.settings() });
              }}
            >
              <SettingsEditForm draft={draft} problems={problems} onChange={setDraft} />
              {changed.length > 0 && (
                <p className="mt-2 font-mono text-[11px] text-dim">changes: {changed.join(", ")}</p>
              )}
            </OpFlow>
            <button type="button" onClick={() => setDraft(null)} className={`${OPEN} mt-3`}>
              Close editor
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
