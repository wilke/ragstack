# 0010. A tool is bound to its image at release time, not at registration or by worker group

Status: Proposed (2026-09-24; amended 2026-10-04 twice — the owner's rename/retire decisions, then the owner's versions-vs-builds rule; issues #609, #613, #614; PR #642; GoWe#273, GoWe#274)

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

1. **A repo version and an image build are different things.** A tenant runs
   a *repo version*. An image is *one build* of some source. Neither is the
   other, and the two are named separately.

   *Repo version.* `v1.6.4` when `HEAD` is exactly on a release tag;
   **`v1.6.4+<shortsha>`** otherwise (dev, which runs `main`), meaning "commit
   `<sha>`; the last release before it was v1.6.4". The `+` part is SemVer
   build metadata: it **never orders versions** — `v1.6.4+a2be96f` is not
   newer or older than `v1.6.4`, it is a different commit. `ragstack.__version__`
   holds the same string (a valid PEP 440 local version, `1.6.4+a2be96f`).
   No commit count, no `-g`, never a raw `git describe` string. The build
   script is the **only** place `git describe` runs, to derive the version:
   `git describe --tags --match 'v*' --long --dirty` → `vX-0-g<sha>` gives
   `vX`; `vX-N-g<sha>` gives `vX+<sha>`; `-dirty` **refuses** — a build from a
   tree with uncommitted changes exits non-zero.

   *Image build.* The image is renamed **`ragstack-tools`** ("worker" named
   the thing that runs it; "tools" names what it is — the ragstack package
   plus the 27 CWL `CommandLineTool` entry points under
   `/opt/ragstack/python/scripts`; no CWL inside, workflows are registered
   from each tenant's checkout). File name:
   **`ragstack-tools-<version>-b<N>.sif`**, e.g. `ragstack-tools-v1.6.4-b1.sif`,
   `ragstack-tools-v1.6.4+a2be96f-b1.sif`. One version may have several builds
   — `b2` is a base-image security rebuild with no code change — so a rebuild
   never forces a release.

   *Identity is in the labels and the digest, not the file name.* The build
   passes the derived values in (`apptainer build --build-arg …`, templated
   into the def file) so `%labels` carry **`org.ragstack.version`,
   `org.ragstack.commit`** (full sha), **`org.ragstack.build`** (`N`) and
   **`org.ragstack.build-date`**, readable by `apptainer inspect --labels`; the
   same values are written to `/opt/ragstack/RELEASE` inside the image for
   in-container checks. The file name is a human-readable handle only — a
   renamed or copied file proves nothing, the labels and digest do.

   *Stamping.* The release step writes the image name into every `dockerPull`
   in `cwl/*.cwl` and the image's digest into `dockerImageId`, and refuses to
   cut the tag if any `dockerPull` is unpinned or names a different version
   (the refuse-on-partial rule from #642, moved from boot time to release time
   where it belongs). A tagged checkout therefore fixes tool + image by itself;
   GoWe's hash changes whenever either does; the bare name and its symlink
   disappear from the registration path. The existing pin test (#613's
   `test_cwl_chunk_method_enum.py`) gets a sibling asserting every `dockerPull`
   in the tree names the checkout's derived version.

2. **One versioned image store, resolved by absolute path.** GoWe uses an
   absolute `dockerPull` as-is and joins only relative names onto `--image-dir`.
   Images live once, under a single versioned directory
   (`/scout/containers/ragstack/ragstack-worker-v<tag>.sif`), and every group's
   workers can run every version. Worker groups return to being placement only
   (ADR-0009). Per-group image directories are retired once no tenant depends on
   them.
3. **There is no image override. `GOWE_TOOL_IMAGE` is retired.** The owner's
   rule: *we might pin workflows, but not images; images are pinned through the
   CWL tool/workflow specification.* A config-time escape hatch contradicts
   that — it is the three-places problem in miniature — and the case it was
   kept for (a canary) is served by a tag: cut a pre-release tag, point one
   tenant's checkout at it. Facts that make retirement cheap: no tenant sets the
   variable (every `tenant.env` checked 2026-10-04), and it never covered the
   from-disk batch plane. #642's substitution code is removed once the first
   stamped tag ships; until then the API **refuses to boot if the variable is
   set**, so a stale environment cannot keep it silently alive. What survives
   of #642 is its *checks*, relocated: the name/shape and refuse-on-partial
   rules run at release time (decision 1), the existence check at boot
   (decision 5).
4. **Every collection version records what built it.** The pack step writes the
   GoWe `workflow_id`, the resolved image name and its digest into the version
   manifest, and the ingest receipt carries the same three fields. Provenance
   runs from a chunk to the exact tool without a log.
5. **A `render` view before a submission, and the same check at boot.**
   `ragstack-ctl gowe render <tenant>` prints the concrete workflow text the
   API would register, its content hash, and for each `dockerPull` the three
   things that must hold: the image **exists**; its **digest equals
   `dockerImageId`**; its **`org.ragstack.version` label equals the checkout's
   derived version**. Boot refuses when any of the three fails (degrading to a
   warning only where the API host cannot see the image store). Because the
   version string covers branch tenants (`v1.6.4+<sha>`), there is no separate
   convention for them: dev on `main` passes the same check as hackathon on a
   tag. A non-release image (any `+<sha>` version) never enters the shared
   release store of decision 2; it lives in the tenant's own image directory.

What this does **not** decide: per-tool images. All our tools share one image,
so "tool version" and "release version" coincide; splitting the image is a
separate decision if a tool ever needs a different runtime.

## Consequences

* A version is a tag, or a tag plus a commit. "Which chunker built this collection" becomes a lookup
  (manifest → workflow id → registered text → image digest), not an archaeology.
* Registration dedup works *for* us: two tenants on the same tag render the same
  text and share one GoWe row — the correct outcome, since they run the same
  tool. (The row shows the first registrant's name; nothing on our side keys on
  workflow name — checked 2026-09-24 — and it must stay that way.)
* Rolling a tenant forward or back is a checkout change; rolling one *tool* is
  not possible without a release, by design.
* A tenant cannot run a tool version its checkout does not derive. That is the point:
  the only way to change what runs is a release, and a release is reviewable.
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

1. Rename the def file and image to `ragstack-tools`; add the build script
   that derives the version (the only `git describe`), refuses dirty trees,
   passes version/commit/build/date as build-args into `%labels` and
   `/opt/ragstack/RELEASE`, and names the file `ragstack-tools-<version>-b<N>.sif`;
   make `ragstack.__version__` carry the derived version; land the stamping in
   the release step and the tree-wide `dockerPull` test; cut the next tag with
   versioned names. One rewrite of the ~216 `ragstack-worker` references, not
   two. No runtime
   change yet — the old symlink still resolves the bare name for older
   checkouts.
2. Add the manifest/receipt fields (additive; old manifests read as "unknown").
3. Create the shared image store; point new tags' absolute paths at it; leave
   per-group directories in place for tenants on older tags.
4. Add `ctl gowe render` and the boot existence check.
5. Retire per-group image directories when the last tenant is on a stamped tag,
   and remove #642's substitution code (keeping its tests' *checks* at the
   release step). Supersedes the group-as-version-knob framing of #614 and the
   override framing of #642.
