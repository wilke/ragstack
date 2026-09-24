# 0010. A tool is bound to its image at release time, not at registration or by worker group

Status: Proposed (2026-09-24; issues #609, #613, #614; PR #642; GoWe#273, GoWe#274)

## Context

A CWL `CommandLineTool` is an interface plus a binding: its inputs and outputs,
and a `DockerRequirement` naming the image that implements it. For our tools the
image **is** the implementation — `ingest_shard.py`, `embed_shard.py`,
`archive_version.py` live inside `ragstack-worker.sif` at `/opt/ragstack/scripts/`;
the CWL only names the entry point. So a tool's identity is *CWL text + image*.
Change either and it is a new version of the tool; a workflow that inlines the
tool (all of ours do — GoWe cannot resolve an external `run:`) has changed with it.

GoWe already models this correctly. It content-hashes the registered workflow
text and mints an immutable, deduplicated id (`wf_…`); a submission is pinned to
that id; names and labels (`worker_group`) are mutable pointers and routing, never
identity (GoWe#274). The id is a version. It is GoWe's to assign and ours to
reference.

Our CWL violated the model at the binding:

```yaml
requirements:
  DockerRequirement:
    dockerPull: ragstack-worker.sif      # a name with no version
```

The worker resolves that bare name against its `--image-dir`, and `--image-dir`
is a property of a **worker group**. Three consequences followed, all of which we
have now paid for:

1. **Identical text, different tool.** v1.6.3 and v1.6.4 shipped byte-identical
   CWL over different images (`ingest_shard.py` gained semantic chunking). GoWe
   gave both the *same* workflow id for different behaviour, because the hash
   could not see the change. The first sign was a job failing mid-scatter with
   "`--chunk-method semantic` is not yet wired" (#609, Clark's `Salmonella_AMR2`).
2. **The group became the version knob.** The only way to run a different image
   for one tenant was a different `--image-dir`, i.e. a different group.
   `ragstack-dev` and `ragstack-hackathon` exist for that reason, not for
   placement; rolling the image to test semantic chunking on dev threatened the
   four OA workers sharing the `ragstack` group. ADR-0009 already argued a group
   is placement, not a boundary.
3. **#614 patched the wrong layer.** `GOWE_TOOL_IMAGE` (#642) rewrites
   `dockerPull` in the workflow text the API registers, per tenant, from tenant
   configuration. It works — GoWe resolves the name at task execution, our
   id-based submission makes rollback real, a half-pinned document refuses at
   boot — but it puts the tool/image pairing in three places: the git tag (CWL
   text), the tenant env (image name) and the GoWe row (the result). A tenant on
   tag v1.6.4 with `GOWE_TOOL_IMAGE=ragstack-worker-v1.6.3.sif` runs a CWL/image
   skew no release ever tested, and nothing records that it did.

Three further gaps sit on the same fault line:

* **Image identity is by name, not content.** Two image directories can hold
  different bytes under the same versioned name; nothing checks a digest. CWL has
  `dockerImageId` for exactly this purpose; we set it to the same bare name.
* **No provenance from a collection to its tool.** A collection version's
  manifest (`archive_version.py`) records `ragstack_version` and `spec_hash`, not
  the GoWe workflow id or the image that built it. "Which tool made these chunks"
  is answered today by grepping worker logs.
* **No "what would run" view.** Nothing renders a tenant's concrete workflow and
  shows its hash and whether the image exists before a job is submitted. A wrong
  image surfaces as the first failed job (apptainer fails fast, three retries,
  ~16 s, resolved path in stderr — tested on a scratch engine, 2026-09-24).

## Decision

**The tool/image binding is a property of a release, written into the tool
definition when the release is cut.** Everything else is an override or a view.

1. **The CWL in git names the versioned image.** The release step that builds
   `ragstack-worker-v<tag>.sif` from a tag also writes that name into every
   `dockerPull` in `cwl/*.cwl`, and its digest into `dockerImageId`. A tagged
   checkout therefore fixes tool + image by itself; GoWe's hash changes whenever
   either does; the bare `ragstack-worker.sif` name and its symlink disappear
   from the registration path. The existing pin test (#613's
   `test_cwl_chunk_method_enum.py`) gets a sibling asserting every `dockerPull`
   in the tree names the checkout's own version.
2. **One versioned image store, resolved by absolute path.** GoWe uses an
   absolute `dockerPull` as-is and joins only relative names onto `--image-dir`.
   Images live once, under a single versioned directory
   (`/scout/containers/ragstack/ragstack-worker-v<tag>.sif`), and every group's
   workers can run every version. Worker groups return to being placement only
   (ADR-0009). Per-group image directories are retired once no tenant depends on
   them.
3. **`GOWE_TOOL_IMAGE` is an override, not the binding.** It stays (#642's
   validation, refuse-on-partial and ctl classification are kept as they are)
   for canaries and emergencies, and it logs the rendered workflow's hash and a
   warning naming the skew whenever it differs from the checkout's own version.
   It is never the normal way a tenant gets a tool version; a tag bump is.
4. **Every collection version records what built it.** The pack step writes the
   GoWe `workflow_id`, the resolved image name and its digest into the version
   manifest, and the ingest receipt carries the same three fields. Provenance
   runs from a chunk to the exact tool without a log.
5. **A `render` view before a submission.** `ragstack-ctl gowe render <tenant>`
   prints the concrete workflow text the API would register, its content hash,
   and for each `dockerPull` whether the file exists and its digest matches
   `dockerImageId`. The same check runs at API boot and refuses to start when an
   image the CWL names does not exist (extending #642's boot validation from the
   name's *shape* to its *existence*).

What this does **not** decide: per-tool images. All our tools share one image,
so "tool version" and "release version" coincide; splitting the image is a
separate decision if a tool ever needs a different runtime.

## Consequences

* A version is a tag. "Which chunker built this collection" becomes a lookup
  (manifest → workflow id → registered text → image digest), not an archaeology.
* Registration dedup works *for* us: two tenants on the same tag render the same
  text and share one GoWe row — the correct outcome, since they run the same
  tool. (The row shows the first registrant's name; nothing on our side keys on
  workflow name — checked 2026-09-24 — and it must stay that way.)
* Rolling a tenant forward or back is a checkout change; rolling one *tool* is
  not possible without a release, by design.
* `ragstack-dev`/`ragstack-hackathon` lose their reason to exist as image
  isolators. Whether they remain as placement (GPU/CPU, registry env per
  ADR-0009's interim) is an ops decision, not a versioning one.
* Cost: the release script grows a stamping step and a digest computation; the
  pack step and receipt gain three fields; ctl gains one subcommand; the boot
  check needs the API host to see the image store (it does on `coconut`; the
  check must degrade to a warning where it cannot).
* Until GoWe validates inputs against declared types (GoWe#273), the CWL enum
  from #613 remains documentation and the API's own `chunk_method` check remains
  the gate — unrelated to this ADR, noted so nobody expects the versioned
  binding to catch a bad *input*.

## Migration

1. Land the stamping in the release script and the tree-wide `dockerPull` test;
   cut the next tag with versioned names (no runtime change yet — the symlink
   still resolves the old bare name for older checkouts).
2. Add the manifest/receipt fields (additive; old manifests read as "unknown").
3. Create the shared image store; point new tags' absolute paths at it; leave
   per-group directories in place for tenants on older tags.
4. Add `ctl gowe render` and the boot existence check.
5. Retire per-group image directories when the last tenant is on a stamped tag.
   Supersedes the group-as-version-knob framing of #614; #642's mechanism is
   retained as the override in decision 3.
