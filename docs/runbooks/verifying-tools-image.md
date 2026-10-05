# Verifying the tools image a tenant will run

ADR-0010 decision 7; #655 step 4. **Why this exists:** GoWe resolves a
workflow's `dockerPull: ragstack-tools-<version>-b<N>.sif` as
`<worker --image-dir>/<name>` at task execution and never checks
`dockerImageId`. Nothing in the engine compares the file to anything. So the
only place "the bytes behind that name are the build the release stamped" is
enforced is this check, which the API runs at boot and you run by hand with
`ragstack-ctl gowe render`.

## What the check does

For a stamped name, in order:

1. **Exists** — the file is looked up in the store dirs in order; the first
   hit is verified (that is what a worker on that `--image-dir` resolves).
   Every dir that has it is listed; a second copy is a warning.
2. **Receipt** — `<name>.receipt.json` beside the image (what
   `apptainer/build-tools-image.sh` writes) must exist and name the image.
3. **sha256** — the file's streamed sha256 must equal the receipt's.
4. **Labels** — `apptainer inspect --labels` must say the receipt's
   `org.ragstack.version` / `commit` / `build`. No `apptainer` on the host is a
   *warning* (`labels_checked=false`); an inspect that fails on the file is a
   problem (it is not a SIF).
5. **Committed receipt** — `cwl/tool-image.receipt.json`, the copy the
   stamping step committed beside the CWL, must agree with the receipt in the
   store (`name`, `sha256`, `version`, `commit`, `build`). A disagreement
   means the store holds a different build than the release stamped.

Any problem is a refusal. The bare `ragstack-worker.sif` (an unstamped tree,
what `main` carries until the first stamped server release) is `unstamped`:
nothing to verify, not a failure.

One implementation: `ragstack.tool_image.verify_named_image`, exposed as
`python -m ragstack.tool_image verify`. The ctl shells to it from the tenant's
own checkout and interpreter; it does not re-implement the rules.

## At boot

With `INGEST_BACKEND=gowe` the API runs the check on the three CWLs it
registers (`GOWE_WORKFLOW_CWL`, `GRAPH_EXTRACT_CWL`, `COLLECTION_RESTORE_CWL`,
the last two defaulting to the repo copies) against **`GOWE_IMAGE_DIRS`** —
comma-separated, the dir(s) the tenant's worker group resolves `--image-dir`
against: the shared release store and/or the group's own dir.

| State | Boot |
|---|---|
| stamped, dirs set, all checks pass | boots; one info line per image: `verified at <path>` |
| stamped, dirs set, any problem | **refuses**: `tool image identity check FAILED (ADR-0010 decision 7, #655)`, then per setting the image, the path tried and each problem |
| stamped, `GOWE_IMAGE_DIRS` unset | **warns** naming the setting and boots — this host cannot see a store; verify from one that can |
| unstamped (`ragstack-worker.sif`) | boots; info: `unstamped tree, identity check skipped` |
| `INGEST_BACKEND=local` | no check |

A refusal looks like this in the tenant's API log:

```
RuntimeError: tool image identity check FAILED (ADR-0010 decision 7, #655): ...
GOWE_WORKFLOW_CWL=/rag/repos/ragstack/cwl/pdf-ingest-scatter.cwl
ragstack-tools-v1.7.0-b1.sif: problem (/scout/containers/ragstack/ragstack-tools-v1.7.0-b1.sif)
  problem: sha256 mismatch: file ... is 37e0d5…, receipt says e03fb4… — the bytes under this name are not the build the receipt describes
Fix the store (copy the image AND its receipt from the build), or check out the release whose CWL names the image that is there.
```

## By hand: `ragstack-ctl gowe render <tenant>`

```bash
ragstack-ctl gowe render hackathon            # text
ragstack-ctl gowe render hackathon --json     # the records
```

Prints, for the tenant's checkout, each registered workflow's **text sha256**
(labelled "GoWe would content-hash this" — the id GoWe mints is the content
hash of exactly these bytes), its `dockerPull`, and the verdict. Exit 0 when
nothing is wrong, **3 (refused)** when any workflow has a problem — the same
decision the boot makes — 1 when the check could not run (no interpreter, no
checkout, no JSON back), 2 on usage.

It reads the registry row (worktree, `python_env`) and the tenant's
`tenant.env` (the three CWL keys, `GOWE_IMAGE_DIRS`, `INGEST_BACKEND`), then
runs `<python_env>/bin/python -m ragstack.tool_image verify --json --dirs …
--cwl …` with `PYTHONPATH=<worktree>/python` and a sanitized environment (no
tenant.env, no secrets — the check reads files, not services). Read-only:
nothing is written and no service is touched.

Overrides for a dry run before the row says so: `--image-dirs A,B`,
`--worktree DIR`, `--python PATH`.

Without the ctl, from any checkout that can see the store:

```bash
python -m ragstack.tool_image verify --name ragstack-tools-v1.7.0-b1.sif --dirs /scout/containers/ragstack
python -m ragstack.tool_image verify --cwl cwl/pdf-ingest-scatter.cwl --dirs /scout/containers/ragstack --json
```

## When it fails

* **not found in: …** — the image is not in any listed dir. Either
  `GOWE_IMAGE_DIRS` names the wrong dir(s) for this tenant's worker group, or
  ops has not placed the release (step 3 of the migration: copy the image
  *and* its receipt).
* **no receipt beside the image** — the `.sif` was copied without its
  `.receipt.json`. Copy it from the build output; never write one by hand.
* **sha256 mismatch** — the bytes under the name are not the build. Do not
  "fix" the receipt: replace the file from the build output, or find out
  which build was placed and why.
* **label … image says … receipt says …** — the file and the receipt are
  from different builds (a receipt copied next to the wrong `.sif`).
* **committed receipt … different build** — the checkout's
  `cwl/tool-image.receipt.json` was written against another build than the
  one in the store: the store has a `b2` the release never saw, or the
  checkout is not the release that was stamped against this store.
* **labels not verified: no 'apptainer'** — a warning only; the sha256 holds
  the file to its receipt. Run the ctl from a worker host for the full check.

Setting `GOWE_IMAGE_DIRS` is a CLI-only (executable-surface) edit:
`ragstack-ctl env set <tenant> GOWE_IMAGE_DIRS /scout/containers/ragstack`.
