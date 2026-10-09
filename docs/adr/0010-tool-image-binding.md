# 0010. A tool is bound to its image at release time, not at registration or by worker group

Status: Proposed (2026-09-24; amended 2026-10-04 (rename/retire; versions vs builds) and 2026-10-05 (the three-artifact model, replacing the earlier decisions 1–5); issues #609, #613, #614; PR #642; GoWe#273, GoWe#274)

## Context

A CWL `CommandLineTool` is an interface plus a binding: its inputs and outputs,
and a `DockerRequirement` naming the image that implements it. For our tools the
image **is** the implementation — `ingest_shard.py`, `embed_shard.py`,
`archive_version.py` live inside `ragstack-worker.sif` at `/opt/ragstack/python/scripts/` (mirrored at `/opt/ragstack/scripts/`);
the CWL only names the entry point. So a tool's identity is *CWL text + image*.
Change either and it is a new version of the tool; a workflow that inlines the
tool (all of ours do — GoWe cannot resolve an external `run:`) has changed with it.

GoWe already models this correctly. It mints an immutable id (`wf_…`, a UUID)
for each registered workflow text and **deduplicates registrations by content
hash** — identical text returns the existing row's id — so the same text always
resolves to the same id and an id always resolves to one text; a submission is
pinned to that id; names and labels (`worker_group`) are mutable pointers and routing, never
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

**Three artifacts, each a build of a tag; two compatibility edges; the workflow
binds them.** (Owner's model, 2026-10-05. It replaces the earlier decisions 1–5,
which asked the tools image to carry the *tenant's* version — a conflation of two
different artifacts that produced a circular release flow.)

### The artifacts

1. **A repo version is a tag.** `vX` when `HEAD` is exactly on a release tag,
   `vX+<shortsha>` otherwise (dev, which runs `main`); the `+` part is SemVer
   build metadata and never orders. The hardcoded version in `pyproject.toml`
   is a *claim* that must equal the last tag's public part (a test holds it).
   `ragstack.__version__` holds the same version as PEP 440 (`1.6.4+a2be96f`).
   `ragstack/version.py` is the only derivation of the **repo version**; raw
   describe strings never leave it. Other tools may run their own `git
   describe` for their own stamps — the ctl binary's version, the docs build
   stamp, the ctl's host-facts `code.tag` — but must never produce or override
   the repo version (known exception: the ctl populates `RAGSTACK_GIT_TAG`
   from its own describe — follow-up recorded on #655). Running code reports
   the commit when it can (a git checkout, or an image's `RELEASE` file) and
   says which source it used; a commit is not guaranteed and is never
   invented.

   **One explicit exception (owner, 2026-10-06): an experiment-provenance
   record.** A study run must be reproducible from its record, which replaces
   the chunking study's commit pin (`ingestion/chunkers.py` is no longer
   frozen). Such a record carries the derived version, the **full commit
   hash**, and the **raw `git describe` output as a provenance field** — never
   as a version, never as a name, never compared or parsed by anything else.
   `ragstack.provenance.experiment_provenance()` is the one helper that builds
   it: it takes the derived version from `ragstack.version`, the raw describe
   from a single clearly-labelled accessor there (the only way the raw string
   leaves that module), and, inside an image, the image identity from
   `provenance.read_release()`. Experiments should run from a versioned tools
   image so the record names a build, not a working tree.

2. **The tools image is one build of the repo at a tag T**, named
   `ragstack-tools-<version>-b<N>.sif` and built **from the tag's own commit**
   — nothing is written back into the repo before the build, so
   `org.ragstack.version = T` and `org.ragstack.commit = sha(T)` are both exactly
   true. Several builds per version (`b2` = a base-image rebuild, no code
   change), so a rebuild never forces a release. Identity is in the labels
   (`org.ragstack.version`, `org.ragstack.commit` full sha, `org.ragstack.build`,
   `org.ragstack.build-date`), mirrored in `/opt/ragstack/RELEASE`, and in the
   sha256 digest recorded in a **receipt beside the image**. The file name is a
   human-readable handle. `"ragstack-tools"` names what the image is — the
   package plus the CWL `CommandLineTool` entry points under
   `/opt/ragstack/python/scripts`; no CWL inside. Off a tag the build derives
   `T+<sha>`: a dev or hand build that never enters the shared release store.
   The build refuses a dirty tree and stages `python/` from `git archive HEAD`,
   so gitignored content never ships.

3. **The server (tenant) image is one build of the repo at a tag S**, the same
   build script, the same labels, and it ships the workflows it registers. A
   running tenant's identity is then its image — which also settles "commit not
   guaranteed at runtime": a server image always knows its version and commit.
   Until tenants run from images they run from tagged checkouts, whose derived
   version plays the same role. **S and T may differ**: a server release chooses
   which tools image its workflows name.

### The workflow binds them

4. **A workflow is CWL text that names a tools image by name.** GoWe mints an
   immutable id per registered text and deduplicates registrations by content
   hash (identical text → the existing row's id; `handler_workflows.go`, GoWe
   v0.21.0), so the id binds *text + image name* — a registered workflow is a specific pairing, and a submission
   is pinned to that id. "The tools image must support the workflow" is the one
   real constraint, and the CWL is where it is declared: the release that writes
   `dockerPull: ragstack-tools-v1.6.5-b1.sif` into a workflow asserts that it
   tested that pairing. `dockerImageId` carries the same name (the file cwltool
   looks for); the digest lives with the image, not in the CWL — GoWe never read
   it there (tested 2026-10-04) and it is unknown before the build. The two
   compatibility edges are therefore **workflow ↔ tools image** (the tools'
   command-line interface), declared in the CWL and proven by the release's
   tests, and **server ↔ workflow inputs** (what `_gowe_inputs` seeds), internal
   to one server release because the CWL ships with it.

5. **There is no image override.** Images are pinned through the CWL. The
   `GOWE_TOOL_IMAGE` setting (#642) is retired: boot refuses if it is set; its
   substitution code is removed once the first stamped release ships
   (substitution code removed PR#TBD, 2026-10-08). Its *checks* survive as
   release-time checks on the stamped tree.

6. **The release order is linear.** (a) `git tag vT`; (b) build the tools image
   from that checkout → `ragstack-tools-vT-b1.sif` + receipt; (c) ops copies
   image and receipt to the shared store `/scout/containers/ragstack/`
   (release versions only; `+sha` builds live in a tenant's own image dir);
   (d) a server release runs the stamping step with the receipt, which writes
   the name into every `dockerPull`/`dockerImageId` and refuses a partially
   rewritten tree; commit; `git tag vS`. Nothing is stamped after a build, no
   tag is ever moved. The tree-wide test asserts the CWL is either unstamped
   (the bare default name, which `main` carries until the first stamped server
   release) or stamped with one name — never mixed.

7. **The check at boot and in `ragstack-ctl gowe render <tenant>`.** For each
   `dockerPull`: the named file exists in the store the workers resolve; its
   labels `org.ragstack.version`, `org.ragstack.commit` and
   `org.ragstack.build` equal its receipt's (`org.ragstack.build-date` is
   informational and not compared); its sha256 equals the receipt's. That is an
   *identity* check — compatibility is not re-derived at boot, it was declared
   by the release. GoWe parses `dockerImageId` and never checks it, so this is
   the only enforcement of image identity in the whole path: boot refuses,
   never merely warns. A stamped name with no store configured to check it
   against (`GOWE_IMAGE_DIRS` unset) is not a pass either — the boot refuses it
   too (#673). Dev on `main`
   passes the same check as hackathon on a tag; there is no branch-tenant
   special case.

8. **Every collection version records what built it**: the GoWe `workflow_id`,
   the tools image name and its digest, in the version manifest and the ingest
   receipt. Provenance runs from a chunk to the exact tool without a log.

Not decided: per-tool images (all tools share one image, so "tool version" and
"tools release" coincide); whether the server image replaces checkouts for every
tenant or only for production ones (an ops decision once the build exists).

## Consequences

* A version is a tag. "Which chunker built this collection" is a lookup
  (manifest → workflow id → registered text → image name → receipt/labels).
* Registration dedup works *for* us: two tenants whose server releases name the
  same tools image and carry the same CWL text share one GoWe row — correct,
  they run the same pairing. The row shows the first registrant's name; nothing
  on our side keys on workflow name (checked 2026-09-24) and it must stay so.
* A tagged tenant changes what it runs only by a tag; a base-image rebuild that
  must reach a tagged tenant is a patch tag, because its CWL names `-b1`. Dev on
  `main` takes `b2` with a plain commit. That is the intended cost.
* The tools image and the server can be released independently; the pairing is
  tested at the server release, which is where the CWL changes.
* `ragstack-dev`/`ragstack-hackathon` lose their reason to exist as image
  isolators once every tenant's CWL names a versioned image resolved from one
  store. Whether they remain as placement is an ops decision (ADR-0009).
* Cost: a build script and a stamping script (shipped in #664), a receipt
  format, a shared store, a boot/render check, three fields in manifests and
  receipts, and — later — a server image and the ctl changes to deploy it.
* Until GoWe validates inputs against declared types (GoWe#273), the CWL enum
  from #613 is documentation and the API's own `chunk_method` check is the gate.

## Migration (#655)

1. **Tools image build + stamping tooling** (#664): `ragstack-tools.def`,
   `build-tools-image.sh`, `ragstack/version.py` as the single derivation,
   `stamp_tool_image.py`, the unstamped-or-stamped tree test, `GOWE_TOOL_IMAGE`
   refused at boot. `dockerPull: ragstack-worker.sif` stays on `main` until the
   first stamped server release.
2. **Provenance fields** in manifest and receipt (additive). Shipped as
   `manifest.provenance` / `ShardReceipt.provenance` /
   `graph_extraction.provenance` / the replay summary's `provenance`:
   `{workflow_id, tool_image, tool_image_digest, image_version, image_commit,
   image_build}`. The first three are **submission inputs** seeded by the
   three registrars between `register_workflow` and `submit` — the `wf_` id
   exists only then, and a file's sha256 cannot live inside the file — the
   last three the worker reads from `/opt/ragstack/RELEASE`. The digest's
   source is the **committed receipt** `cwl/tool-image.receipt.json`, which
   the stamping step (6d) writes beside the CWL it stamps and `--check`
   holds to the stamped name: offline-checkable, in git next to the
   `dockerPull` it belongs to, and no dependency on the store path step 4
   introduces. **On an unstamped tree the API seeds no provenance inputs;
   provenance begins with the first stamped release** — the stamp is the
   declaration that the image supports the workflow (decision 4), and the
   builds the bare name resolves to today predate the tools' flags (an
   unknown `--workflow-id` is argparse exit 2 on every task). The declared
   `["null", string]` inputs stay null and the flags are omitted (GoWe skips
   null inputs before any prefix; cwltool per CWL v1.2). A version written
   before this, or on an unstamped tree, reads as all-`null` ("unknown"),
   never as an error.
3. **The shared store** and the first tools tag built into it (ops).
4. **`ctl gowe render` and the boot identity check.** Shipped: one
   implementation, `ragstack.tool_image.verify_named_image` (exists in the
   store dirs, receipt beside the image, streamed sha256 == receipt,
   `apptainer inspect --labels` == receipt, committed
   `cwl/tool-image.receipt.json` == the store's receipt), exposed as
   `python -m ragstack.tool_image verify`. The API runs it at boot for the
   three registered CWLs when `INGEST_BACKEND=gowe` and **refuses** on any
   problem; the store dirs come from the new `GOWE_IMAGE_DIRS` setting
   (unset with a stamped CWL = `unchecked`, "not verified: GOWE_IMAGE_DIRS
   unset": the boot refuses, `verify` exits 4, `render` exits 3 — #673; it
   first shipped as a warning). The bare default name is `unstamped`: nothing
   to verify, with or without the setting. `ragstack-ctl gowe render <tenant>` shells
   to the same Python check from the tenant's checkout and venv and prints
   each workflow's text sha256 (what GoWe content-hashes), its `dockerPull`
   and the verdict (`--json` for the records); runbook:
   `docs/runbooks/verifying-tools-image.md`.
5. **The first stamped server release**: name the tools image in `cwl/`, tag,
   deploy; then remove #642's substitution code and retire per-group image
   directories when no tenant depends on them.
6. **The server image**: build tenants' API from the same script, deploy via
   ctl; `GET /v1/version` reads `RELEASE`. Supersedes "dev runs a checkout".

Supersedes the group-as-version-knob framing of #614, the override framing of
#642, and this ADR's own earlier "image version == checkout version" check.
