// The two pickers an image-mode choice needs (PR-F): the prepared server
// images (`GET /v1/artifacts` `server_images`), and the prepared artifacts at
// the chosen image's commit — the only ones a static UI may be built from
// alongside that image. Used by the Upgrade action (TenantActions) and the
// create wizard's Code step. Props only; every server string goes through
// `redactText` like the artifact picker's.

import type { ArtifactRow, ServerImageRow } from "../api/types";
import { since } from "../lib/format";
import { artifactsAtCommit, shortSha } from "../lib/validate";
import { redactText } from "./redact";

const EYEBROW = "mb-1 font-mono text-[10px] font-medium uppercase tracking-[.12em] text-muted";
const TH = `py-1.5 pr-3 ${EYEBROW}`;
const CHIP = "ml-1.5 inline-flex items-center rounded-chip px-2 py-[2px] font-mono text-[10.5px] font-medium";

/** By name (the daemon's order), so `-b1` sits next to `-b2`. */
export function sortServerImages(rows: readonly ServerImageRow[]): ServerImageRow[] {
  return [...rows].sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
}

export function ServerImageTable({
  images,
  selected,
  current = null,
  currentSelectable = true,
  radioName = "server-image",
  onSelect,
}: {
  images: readonly ServerImageRow[];
  selected: string;
  /** The image the tenant runs now: marked, and selectable only when `currentSelectable`. */
  current?: string | null;
  currentSelectable?: boolean;
  radioName?: string;
  onSelect: (name: string) => void;
}) {
  if (images.length === 0) {
    return (
      <p className="text-[12.5px] text-dim">
        No server image is prepared on this host yet (
        <code className="font-mono">ragstack-ctl fleet image prepare --sif …</code>).
      </p>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table aria-label="server images" className="w-full border-collapse text-left">
        <thead>
          <tr className="border-b border-line">
            <th className={TH} aria-label="choose" />
            <th className={TH}>image</th>
            <th className={TH}>version · build</th>
            <th className={TH}>commit</th>
            <th className={TH}>prepared</th>
          </tr>
        </thead>
        <tbody>
          {sortServerImages(images).map((i) => {
            const isCurrent = current !== null && i.name === current;
            const disabled = isCurrent && !currentSelectable;
            return (
              <tr key={i.name} className="border-b border-lineSoft align-middle">
                <td className="py-1.5 pr-3">
                  <input
                    type="radio"
                    name={radioName}
                    value={i.name}
                    aria-label={`image ${i.name}`}
                    checked={selected === i.name}
                    disabled={disabled}
                    title={disabled ? "Already running: only a UI rebuild makes this an upgrade." : undefined}
                    onChange={() => onSelect(i.name)}
                  />
                </td>
                <td className="py-1.5 pr-3 font-mono text-[12px] text-strong">
                  {redactText(i.name)}
                  {isCurrent && <span className={`${CHIP} bg-mossSoft text-moss`}>current</span>}
                </td>
                <td className="py-1.5 pr-3 font-mono text-[11.5px] text-body">
                  {redactText(i.version)} · b{i.build}
                </td>
                <td className="py-1.5 pr-3 font-mono text-[11.5px] text-dim" title={i.commit}>
                  {shortSha(i.commit)}
                </td>
                <td className="py-1.5 pr-3 font-mono text-[11.5px] text-dim">
                  <span title={i.prepared_at}>{since(i.prepared_at, Date.now())}</span> by{" "}
                  {redactText(i.prepared_by)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/**
 * The prepared artifacts at `image.commit`. Everything else is left out rather
 * than disabled: an artifact at another commit is never a valid choice here,
 * and the empty state names the commit so the operator knows what to prepare.
 */
export function MatchingArtifacts({
  artifacts,
  image,
  selected,
  radioName = "image-artifact",
  onSelect,
}: {
  artifacts: readonly ArtifactRow[];
  image: ServerImageRow | undefined;
  selected: string;
  radioName?: string;
  onSelect: (id: string) => void;
}) {
  if (!image) {
    return <p className="text-[12.5px] text-dim">Choose an image first: the artifact must be at its commit.</p>;
  }
  const rows = artifactsAtCommit(artifacts, image.commit);
  if (rows.length === 0) {
    return (
      <p role="note" className="rounded-card bg-accent-soft p-3 text-[12.5px] text-accent-text">
        No prepared artifact is at commit <code className="font-mono">{shortSha(image.commit)}</code>, the commit{" "}
        <code className="font-mono">{redactText(image.name)}</code> was built from — a UI built from any other
        artifact would not match the API. Prepare one on the CLI (
        <code className="font-mono">ragstack-ctl fleet artifact prepare --tag {redactText(image.version)}</code>).
      </p>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table aria-label="artifacts at the image's commit" className="w-full border-collapse text-left">
        <thead>
          <tr className="border-b border-line">
            <th className={TH} aria-label="choose" />
            <th className={TH}>artifact</th>
            <th className={TH}>prepared</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((a) => (
            <tr key={a.id} className="border-b border-lineSoft align-middle">
              <td className="py-1.5 pr-3">
                <input
                  type="radio"
                  name={radioName}
                  value={a.id}
                  aria-label={`artifact ${a.id}`}
                  checked={selected === a.id}
                  onChange={() => onSelect(a.id)}
                />
              </td>
              <td className="py-1.5 pr-3 font-mono text-[12px] text-strong">
                {redactText(a.tag)}
                {a.id !== a.tag && <span className="ml-1.5 text-[11px] text-dim">id {redactText(a.id)}</span>}
              </td>
              <td className="py-1.5 pr-3 font-mono text-[11.5px] text-dim">
                <span title={a.prepared_at}>{since(a.prepared_at, Date.now())}</span> by {redactText(a.prepared_by)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
