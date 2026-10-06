# Cutting a release: tools image, store, stamp, server tag

> **Status: never exercised end to end as of 2026-10-06. The first real
> release will validate it.** This is a draft written from the code and
> [ADR-0010](../adr/0010-tool-image-binding.md) on `main` at `cde401b` (#672).
> As of that date no release has gone through this flow: every `dockerPull` in
> `cwl/*.cwl` is the bare `ragstack-worker.sif`, the newest tag is `v1.6.4`,
> and the shared store `/scout/containers/ragstack/` does not exist. Each step
> is marked **[exercised]** (run on coconut while this was written; read-only
> commands, dry runs and scratch copies only) or **[never exercised]**. Fix this
> file from what the first release actually does.

ADR-0010 names three artifacts separately: a **tools image** (one build of tag
`T`), a **workflow** (CWL text that names a tools image by file name) and a
**server release** (tag `S`, which picks the tools image its CWL names). The
release order is linear (ADR-0010 decision 6; [`apptainer/README.md`
§ Three artifacts, and the release order](../../apptainer/README.md#three-artifacts-and-the-release-order)):

```
1. git tag vT          (wilke)       a tag on main
2. build-tools-image   (wilke)       ragstack-tools-vT-b1.sif + .receipt.json, from vT's own commit
3. copy to the store   (wilke)       /scout/containers/ragstack/  (release versions only)
4. stamp               (wilke, PR)   stamp_tool_image.py <receipt> → cwl/*.cwl + cwl/tool-image.receipt.json
5. --check + tests     (wilke, CI)   the tree is stamped with exactly one name
6. commit, merge, tag vS (wilke)     the server release
```

Two rules hold at every step. **A tag never moves**: if something is wrong
after a tag is pushed, you cut a new tag. **Nothing is stamped after a build,
and a bad build is never stamped**: rebuild it as `b2`, never reuse a build
number, never edit a receipt.

The examples use **`T = v1.6.5`** and **`S = v1.6.6`**. These are placeholders.
`S` cannot equal `T`, because stamping makes a new commit after `T` and the tag
on `T` cannot move.

---

## Who runs what, and why

| Step | Account | Why that account |
|---|---|---|
| tag `vT`, tag `vS`, push | `wilke` | Owns the GitHub repo. Push with an explicit URL and refspec (`/rag/repos/ragstack.git` is a bare mirror, so a plain `git push` can delete remote refs). |
| build | `wilke`, in a fresh worktree | Rootless Apptainer. The script writes only to `--out`. It must run from a clean checkout of the tag. Never build from `/rag/repos/ragstack` (frozen) or from a peer's live checkout. |
| create the store, copy into it | `wilke` | `/scout/containers` is `wilke:cels 755`, and every GoWe worker runs as `wilke`. ADR-0010 makes the store "the management session's, by hand". |
| stamp, PR, merge | `wilke` (any dev session, in a worktree) | A normal code change. CI runs the pin test. |
| `ragstack-ctl gowe render` | anyone who can read `registry.json`, the tenant's `tenant.env`, its checkout and the store | Read-only. The boot check runs as the tenant's API account (`svcbvbrc` for `dev`/`hackathon`), so the store must be readable by that account too. |

---

## 0. Once: create the shared store [never exercised]

ADR-0010 decision 6 and Migration step 3 put it at `/scout/containers/ragstack/`.
It holds **release builds only**: `ragstack-tools-vX.Y.Z-b<N>.sif`, each with its
`<name>.receipt.json` beside it. No `+<sha>` builds, no `ragstack-worker.sif`,
no symlinks. As `wilke`:

```bash
mkdir /scout/containers/ragstack
chgrp cels /scout/containers/ragstack          # wilke's primary group; svcbvbrc is in cels too
chmod 2755 /scout/containers/ragstack          # owner writes; group and other read and traverse
ls -ld /scout/containers/ragstack              # Expect: drwxr-sr-x wilke cels
```

Both readers have to get in. GoWe workers run as `wilke` (owner). The API boot
check for `dev` and `hackathon` runs as `svcbvbrc` (uid 10078, group `cels`).
Check the `svcbvbrc` side through the service wrapper, the same way
`docs/runbooks/ctl-deploy.md` § 3a runs a shell as that account:

```bash
CTL_BIN=/bin/bash /rag/bin/ctl-as-svc.sh -c 'ls -l /scout/containers/ragstack/ && id'
```

Files go in mode `0444`, so nobody can rewrite the bytes behind a name by
accident. Run `sha256sum` against the receipt after every copy (§ 3).

> **The store is not on any worker's `--image-dir` today.** See § 7 before you
> expect a worker to resolve anything from it.

---

## 1. Tag `vT` [never exercised under this flow]

Pick the commit on `main` that the tools image will be. The tag's tree has to
pass the version test, so **bump `python/pyproject.toml`'s `version` to `T`'s
public part (`1.6.5`) in the commit you are going to tag**. The image does not
depend on this (the def rewrites its staged copy from `VERSION`), but the test
does:

```bash
git fetch -q origin
git worktree add ~/Development/worktrees/release-v1.6.5 -b release/v1.6.5 origin/main
cd ~/Development/worktrees/release-v1.6.5
sed -i 's/^version = ".*"/version = "1.6.5"/' python/pyproject.toml
git commit -am "release v1.6.5: pyproject version"
# → PR, review, merge to main as usual. Then tag the MERGED commit:
git fetch -q origin
git tag -a v1.6.5 -m "v1.6.5 (tools image)" <merged-sha>
git push https://github.com/wilke/ragstack.git refs/tags/v1.6.5:refs/tags/v1.6.5
```

Existing tags are annotated (`git cat-file -t v1.6.4` → `tag`), so keep them
that way.

**Verify:**

```bash
git -C ~/Development/worktrees/release-v1.6.5 checkout --detach v1.6.5
PYTHONPATH=python /rag/envs/ragstack/bin/python -m ragstack.version --shell
# Expect: VERSION=v1.6.5  COMMIT=<full sha of the tag's commit>       [exercised: CLI shape]
cd python && /rag/envs/ragstack/bin/python -m pytest -q tests/unit/test_version_derivation.py
```

> **Expected red, once.** `test_pyproject_carries_the_public_part_of_the_last_release`
> compares `pyproject.toml` with the newest *reachable* tag. On a full clone
> the bump commit fails it until the tag exists, and passes once the tag is
> on it. CI does not see this: `actions/checkout` there is shallow, so the
> test skips with "no v* tag reachable". Run it locally **after** tagging.
> [never exercised]

**If it fails partway:** a pushed tag stays where it is. If the tagged tree
turns out to be wrong, merge the fix and tag `v1.6.6` as the tools tag. `v1.6.5`
just never gets a tools image. Write that down in the release notes.

---

## 2. Build the tools image from the tag [dry run exercised; real build never exercised]

Build in a **clean, detached checkout of exactly `vT`**. The script derives the
version from `HEAD` and refuses a dirty tree (exit 3). If no `v*` tag is
reachable it exits 4. It stages `python/` with `git archive HEAD`, so the
image ships the commit and never the working tree.

```bash
cd ~/Development/worktrees/release-v1.6.5          # detached at v1.6.5, `git status --short` empty
OUT=$HOME/ragstack-tools-builds                    # see "build numbers" below
mkdir -p "$OUT"
TMPDIR=/rag/tmp apptainer/build-tools-image.sh --sandbox --python /rag/envs/ragstack/bin/python --out "$OUT" --dry-run
# read: version v1.6.5, build 1, image $OUT/ragstack-tools-v1.6.5-b1.sif; then drop --dry-run:
TMPDIR=/rag/tmp apptainer/build-tools-image.sh --sandbox --python /rag/envs/ragstack/bin/python --out "$OUT"
```

Flags, from `apptainer/build-tools-image.sh` (`--help` prints its header):

| Flag | Why here |
|---|---|
| `--sandbox` | **Required on coconut.** `--fakeroot` does not work (no subuid/subgid; `MEMORY.md`), and without this flag the script runs `apptainer build --fakeroot`. The flag does the rootless two-step instead: `build --sandbox <tmp> <def>`, then `build <sif> <tmp>`. A sandbox two-step produced `ragstack-worker-v1.6.4-21-g6a7fe95.sif` on this host on 2026-10-04 (its labels show `deffile.from: /rag/tmp/…sbx`), but that was before the script existed. |
| `--python /rag/envs/ragstack/bin/python` | **Required on coconut.** The default is `python` on `PATH`, and for `wilke` that is miniconda's 3.8. There `ragstack.version` fails on `from datetime import UTC` and the build stops with "refusing to build (ragstack.version exit 1)" [exercised]. `PYTHON=…` in the environment does the same thing. Neither `apptainer/README.md` nor the usage line mentions this flag. |
| `--out DIR` | Where the image and receipt go. The default is `apptainer/images/` inside the worktree (gitignored). **Never point it at the shared store.** |
| `TMPDIR=/rag/tmp` | The sandbox and the staged `python/` go under `$TMPDIR`. The 2026-10-04 sandbox build used `/rag/tmp`. |
| `--dry-run` | Prints the version, build number, paths, the exact `apptainer` command(s) and the receipt with `sha256: null`. Builds nothing. [exercised] |

**Build numbers come from `--out` only.** `b<N>` is one more than the highest
`ragstack-tools-<version>-b*.sif` already in `--out`. The script never looks in
the store. A fresh worktree's `apptainer/images/` is empty, so it **always
starts at `b1`**. A rebuild done there would mint a second, different
`…-b1.sif`, and it would collide with the one already in the store. So use one
persistent build directory for every release build (above:
`~/ragstack-tools-builds`). Keep every build in it, including bad ones, so the
numbering keeps going up. Before any rebuild, check that `--dry-run` prints a
number the store does not already hold.

**What it produces:** `$OUT/ragstack-tools-v1.6.5-b1.sif` and
`$OUT/ragstack-tools-v1.6.5-b1.sif.receipt.json` =
`{name, version, commit, build, build_date, sha256}`. The script already
checks the labels and `/opt/ragstack/RELEASE` against its inputs. On a
mismatch it **deletes the image** and exits 1.

**Verify (before anything leaves `$OUT`):**

```bash
SIF=$OUT/ragstack-tools-v1.6.5-b1.sif; R=$SIF.receipt.json
cat "$R"
apptainer inspect --labels "$SIF" | grep org.ragstack
#   org.ragstack.version = v1.6.5, .commit = $(git rev-parse v1.6.5^{commit}), .build = 1, .build-date = receipt's
apptainer exec "$SIF" cat /opt/ragstack/RELEASE                      # the same four values
sha256sum "$SIF"                                                     # == the receipt's sha256
apptainer exec "$SIF" python -c "import ragstack, fitz, qdrant_client, elasticsearch, transformers, neo4j, asyncpg; print(ragstack.__version__)"
#   Expect 1.6.5 (PEP 440: no leading v)
apptainer exec "$SIF" python /opt/ragstack/scripts/load_graph.py --help >/dev/null
apptainer exec "$SIF" python /opt/ragstack/scripts/ingest_shard.py --help | grep -- --workflow-id
#   the provenance flags (#668) must be there; the first stamped release turns them on
```

The import and `--help` checks are the post-build checks from
[`cwl/README.md` § Post-build checks](../../cwl/README.md#post-build-checks).
`import neo4j` is required, not optional (#404).

**If it fails partway:**

- *Refused before building* (exit 3 dirty, exit 4 no tag, exit 2 bad
  version): fix the checkout and run it again. Nothing was written.
- *`apptainer build` failed*: check that no partial `.sif` was left in `$OUT`
  (the script refuses to overwrite an existing name). Remove it if one was.
  Fix the environment and run again from the same tag. The build number does
  not move.
- *The script's label/RELEASE verification failed*: the script already deleted
  the image, and the number is free again. Investigate before you retry. This
  means the def or `ragstack/version.py` is broken, which needs a code fix and
  therefore a new tag.
- *Built fine, but your checks above fail*: **do not copy it.** Leave the bad
  `b1` in `$OUT` so the next build becomes `b2`. If the cause is outside the
  code (base image, PyPI, host), rebuild from the same tag → `b2`. If the code
  is at fault, the fix goes in a new tag.

---

## 3. Copy image and receipt to the shared store [never exercised]

Copy **both** files. Each one goes in under a temporary name and is then
renamed (a rename within one ext4 filesystem is atomic), so a worker or a boot
check never sees a half-written image. Never overwrite a name that already
exists:

```bash
ST=/scout/containers/ragstack; N=ragstack-tools-v1.6.5-b1.sif
test ! -e "$ST/$N" && test ! -e "$ST/$N.receipt.json" || { echo "already in the store: STOP"; false; }
install -m 0444 "$OUT/$N"              "$ST/.$N.partial"              && mv -n "$ST/.$N.partial"              "$ST/$N"
install -m 0444 "$OUT/$N.receipt.json" "$ST/.$N.receipt.json.partial" && mv -n "$ST/.$N.receipt.json.partial" "$ST/$N.receipt.json"
```

**Verify:** run the same identity check the API runs at boot, against the store:

```bash
cd ~/Development/worktrees/release-v1.6.5
PYTHONPATH=python /rag/envs/ragstack/bin/python -m ragstack.tool_image verify --name "$N" --dirs "$ST"
# Expect: "dockerPull: ragstack-tools-v1.6.5-b1.sif -> ok at /scout/containers/ragstack/…", "ok", exit 0
sha256sum "$ST/$N"; grep sha256 "$ST/$N.receipt.json"           # the same digest, by eye
CTL_BIN=/bin/bash /rag/bin/ctl-as-svc.sh -c "sha256sum $ST/$N"  # svcbvbrc can read it
```

`verify` checks that the file exists, the receipt beside it names it, the
streamed sha256 equals the receipt's, and `apptainer inspect --labels` equals
the receipt's version, commit and build (`ragstack.tool_image.verify_named_image`).
Run it with `PYTHONPATH=<checkout>/python`, because the copy of `ragstack`
installed in `/rag/envs/ragstack` lacks the newer modules [exercised:
`python -m ragstack.version` → "No module named ragstack.version" without it].
The `--name … --dirs /scout/containers/ragstack` form runs today and reports
`not found in: /scout/containers/ragstack` [exercised].

**If it fails partway:** an interrupted copy leaves a `.partial` file, which
nothing resolves. Delete it and copy again. If you copied an image but not its
receipt, `verify` says `no receipt beside the image`: copy the receipt from
`$OUT`, and never write one by hand. If `verify` reports a sha256 or label
mismatch, the store holds different bytes from the build. Remove the store
entry before anything stamps it (nothing names it yet, so this is the one point
where removal is safe), then copy again from `$OUT`.

---

## 4. Stamp the CWL: the server release [exercised on a scratch copy only]

In a worktree on a branch off `main` (a commit at or after `T`), name the image
by the **receipt in the store**. Using the store copy means step 3 has to have
happened first:

```bash
git worktree add ~/Development/worktrees/release-v1.6.6 -b release/v1.6.6 origin/main
cd ~/Development/worktrees/release-v1.6.6
/rag/envs/ragstack/bin/python python/scripts/stamp_tool_image.py \
    /scout/containers/ragstack/ragstack-tools-v1.6.5-b1.sif.receipt.json
sed -i 's/^version = ".*"/version = "1.6.6"/' python/pyproject.toml   # S, not T: pyproject tracks the server tag
```

The script (`python/scripts/stamp_tool_image.py`, `--help` [exercised]):

- writes the receipt's `name` into every `dockerPull` **and** every
  `dockerImageId` in `cwl/*.cwl`. On a scratch copy of today's tree it stamped
  all 12 `.cwl` files: 23 `dockerPull` sites and 23 `dockerImageId` sites
  [exercised];
- writes the receipt as given (keys sorted) to **`cwl/tool-image.receipt.json`**.
  This is the committed receipt that decision 8 needs (§ 8);
- refuses, writing nothing, when the receipt is not self-consistent (name ≠
  `ragstack-tools-<version>-b<N>.sif`, sha256 not 64 hex, commit not a full
  sha), when an image site is in a form it cannot rewrite, or when a current
  `dockerPull` does not end in `.sif`;
- does **not** check that the receipt matches any file. That is the job of
  § 3's `verify` and of the boot check. It also does not compare `T` with the
  checkout's version and does not touch `pyproject.toml`.

**If it fails partway:** a refusal writes nothing. Fix the CWL site it names (in
its own PR if it is a real change) and stamp again. A "post-stamp check FAILED
(tree written)" leaves the tree written: `git checkout -- cwl/ && rm -f
cwl/tool-image.receipt.json`, then find out why. If you stamped with the wrong
receipt, stamp again with the right one. Restamping a stamped tree rewrites
every site to the new name and replaces the committed receipt.

---

## 5. Check the stamped tree [exercised on a scratch copy only]

```bash
/rag/envs/ragstack/bin/python python/scripts/stamp_tool_image.py --check
#   Expect: "ok: tree is stamped with ragstack-tools-v1.6.5-b1.sif", exit 0
/rag/envs/ragstack/bin/python python/scripts/stamp_tool_image.py --check \
    /scout/containers/ragstack/ragstack-tools-v1.6.5-b1.sif.receipt.json
#   also holds the tree and the committed receipt to THIS receipt's name and sha256
cd python && /rag/envs/ragstack/bin/python -m pytest -q \
    tests/unit/test_cwl_tool_image_pin.py tests/unit/test_tool_image_identity.py tests/unit/test_gowe_tool_image.py
cd .. && PYTHONPATH=python /rag/envs/ragstack/bin/python -m ragstack.tool_image verify \
    --cwl cwl/pdf-ingest-scatter.cwl --cwl cwl/graph-extract.cwl --cwl cwl/restore-collection.cwl \
    --dirs /scout/containers/ragstack
#   the boot check's verdict for the three CWLs the API registers, against the store,
#   including "committed receipt == the store's receipt"
```

`--check` fails a **mixed** tree (some sites stamped, some not, or two names),
a stamped tree with no `cwl/tool-image.receipt.json` [exercised: "tree is
stamped … but cwl/tool-image.receipt.json is missing"], a committed receipt
naming another image, and an unstamped tree that still carries a receipt.
`tests/unit/test_cwl_tool_image_pin.py` runs the same check, so CI enforces it
on the PR.

Before merging, also run the `verify --cwl …` command above against the dir the
tenant's workers resolve (`--dirs <that group's --image-dir>`). That shows the
text sha256 GoWe will content-hash and the verdict, without touching the
tenant. `ragstack-ctl gowe render <tenant> --worktree <this worktree>` does
NOT do this for `dev` or `hackathon`: their `GOWE_WORKFLOW_CWL` is an
absolute path into the live checkout, which `--worktree` does not redirect
(`internal/ctl/gowe/render.go`, `Resolve`), so the ingest row would render
the old CWL. Use `render` only after the tenant's checkout is on the tag. See [`tenant-upgrade.md` § 4a](tenant-upgrade.md#4a-a-release-whose-cwl-is-stamped-the-tools-image-must-be-where-the-workers-look)
and [`verifying-tools-image.md`](verifying-tools-image.md).

---

## 6. Commit, merge, tag `vS` [never exercised]

```bash
git add cwl/ python/pyproject.toml
git commit -m "release v1.6.6: stamp ragstack-tools-v1.6.5-b1.sif"
# → PR (CI: pin test, everything else), review, merge. Then tag the merged commit:
git fetch -q origin
git tag -a v1.6.6 -m "v1.6.6 (server; CWL names ragstack-tools-v1.6.5-b1.sif)" <merged-sha>
git push https://github.com/wilke/ragstack.git refs/tags/v1.6.6:refs/tags/v1.6.6
```

**`main` is stamped from this merge on.** The bare `ragstack-worker.sif` is
gone from `main`. Any tenant that runs `main` (`dev` does) names
`ragstack-tools-v1.6.5-b1.sif` from its next update, and the API starts seeding
provenance inputs (§ 8). The image must already be where that tenant's workers
look (§ 7) **before** `dev` moves.

**Verify:** `git -C <a clean checkout at v1.6.6> describe --tags` → `v1.6.6`;
`stamp_tool_image.py --check` on that checkout → stamped with the expected name;
`test_version_derivation.py` passes locally (§ 1's note).

**If it fails partway:** if CI goes red on the stamping PR, nothing is
released. Fix it on the branch. If a problem with the image turns up after
`vS` is pushed, `vS` stays. Build `b2` from `vT` (if the cause is environmental)
or cut a new tools tag (if it is the code), then cut `vS+1` stamping the new
name. A tagged tenant only changes images when it changes tag (ADR-0010
§ Consequences).

---

## 7. Worker groups and `--image-dir` during the migration [never exercised]

> **Discrepancy: absolute `dockerPull`.** An earlier version of ADR-0010
> (`17425fd`, decision 2 "One versioned image store, resolved by absolute
> path") had the CWL name the store by absolute path. The current ADR replaced
> that (decision 4: a workflow names its image *by name*), and **the code only
> allows a bare file name**. `ragstack.tool_image.validate_tool_image` rejects
> any `/`, and `STAMPED_IMAGE_RE` and `stamp_tool_image` accept only
> `ragstack-tools-<version>-b<N>.sif`. GoWe would take an absolute path
> (`resolveApptainerImage` uses an absolute `.sif` as is), but ragstack never
> writes one. `apptainer/README.md` § Where images live still says "ADR-0010
> decision 2" about the store, which is the old numbering. The store is now
> decision 6 and Migration step 3.

So a worker resolves a stamped name the same way it resolves the bare name
today, as `<--image-dir>/<name>` (single dir, GoWe `internal/toolexec/execute.go`).
Here is what is running on coconut as of 2026-10-06 (`ps -eo user,args | grep
gowe-worker`, all as `wilke`):

| Group | Used by | `--image-dir` | Today it holds |
|---|---|---|---|
| `ragstack-dev` | `dev` | `/scout/containers/ragstack-dev` | `ragstack-worker.sif -> ragstack-worker-v1.6.3-1-ga2be96f.sif` |
| `ragstack-hackathon` | `hackathon` | `/scout/containers/ragstack-hackathon` | `ragstack-worker.sif -> ragstack-worker-v1.6.4.sif` |
| `ragstack` (`ragstack-oa-*`) | OA bulk loads | `/scout/containers` | `ragstack-worker.sif` (a file) |

**No group resolves `/scout/containers/ragstack/`.** It is a *subdirectory* of
the `ragstack` group's dir, and a bare name never reaches into a
subdirectory. Before a tenant runs a stamped release, one of these has to be
true for **its** group (`GOWE_WORKER_GROUP` in its `tenant.env`):

1. **The group's `--image-dir` is the store.** Restart that group's workers
   with `--image-dir /scout/containers/ragstack`. This is the end state ADR-0010
   points to, where per-group dirs retire (Migration step 5). Until every tenant
   on that group is stamped, an unstamped tenant on it can no longer resolve
   `ragstack-worker.sif`, because the store holds release builds only. So do
   this per group, after its tenants move. The worker processes belong to the
   GoWe session, and restarting them is that session's operation.
2. **The group's own dir also has the release.** Put the image **and its
   receipt** into the group dir under the same names, as symlinks into the
   store:
   `ln -s /scout/containers/ragstack/<name> /scout/containers/ragstack-hackathon/<name>`
   and the same for `<name>.receipt.json`. The receipt must be beside the path
   the check opens. `verify_named_image` reads `<path>.receipt.json` next to
   the symlink, not next to its target. The bare `ragstack-worker.sif` symlink
   stays for unstamped tenants.

Whichever you pick, **`GOWE_IMAGE_DIRS` in the tenant's `tenant.env` must name
the dir the group's workers resolve**: the group's `--image-dir`, and only
that. The boot check verifies the first hit in `GOWE_IMAGE_DIRS`. If that is
the store while the workers resolve the group dir, the check passes on a file
the workers never open.

**Off-tag `+<sha>` builds** (`ragstack-tools-v1.6.5+abc1234-b1.sif`, built from
a commit that is not on a tag) are for dev and hand use. **They never enter the
shared store.** They go in the tenant's own image dir. For `dev` that is
`/scout/containers/ragstack-dev/`:

```bash
apptainer/build-tools-image.sh --sandbox --python /rag/envs/ragstack/bin/python --out /scout/containers/ragstack-dev
#   numbering there is per version, so b<N> is computed from that dir
```

On an **unstamped** `main` (today), point `dev`'s bare name at it:
`ln -sfn ragstack-tools-<version>-b<N>.sif /scout/containers/ragstack-dev/ragstack-worker.sif`
(the existing mechanism, [`cwl/README.md` § Running these on a real GoWe engine](../../cwl/README.md#running-these-on-a-real-gowe-engine--operational-gotchas)).
Nothing verifies an unstamped name. Once `main` is stamped, `dev` changes image
only through a commit that stamps a different name, which can be a `+<sha>`
build that lives in `/scout/containers/ragstack-dev/`. `stamp_tool_image.py`
accepts one ("a dev server on `main` may name a `vT+<sha>` build"). `dev`'s
`GOWE_IMAGE_DIRS` then has to name `/scout/containers/ragstack-dev`. Whether
such a commit belongs on `main` itself (which every other `main` checkout would
then name) is a policy question ADR-0010 leaves open. [never exercised]

---

## 8. What decision 8's provenance needs from a stamped tree

The provenance fields `{workflow_id, tool_image, tool_image_digest}` are
submission inputs that the API seeds between `register_workflow` and `submit`
(`ragstack.tool_image.provenance_inputs`). They only exist on a stamped tree:

- `tool_image` is the stamped `dockerPull` name. On an unstamped tree nothing
  is seeded at all: the inputs stay null and the tools' flags are omitted,
  because the images the bare name resolves to predate the flags.
- `tool_image_digest` is the `sha256` from **`cwl/tool-image.receipt.json`
  beside the CWL file the API registers** (`read_committed_receipt(Path(cwl_path).parent)`).
  If that file is missing the digest is `null` silently; if it names another image (or has no 64-hex sha256) the digest is `null` and a warning is logged. So the receipt has to be **committed**. `--check` and the
  pin test refuse a stamped tree without it.
- `image_version` / `image_commit` / `image_build` come from the worker's own
  `/opt/ragstack/RELEASE`, so they need an image built by this script.

Two consequences for operators:

- If a tenant's `GOWE_WORKFLOW_CWL` (or `GRAPH_EXTRACT_CWL` /
  `COLLECTION_RESTORE_CWL`) points at a CWL **outside the checkout's `cwl/`**,
  for example a hand copy, there is no receipt beside it. The digest is then
  recorded as `null`, and the boot check silently skips its
  committed-receipt comparison. Point these keys at the checkout's own `cwl/`.
- The digest is copied from the committed receipt. It is held against the
  store's bytes only by the boot check, and that runs only when
  `GOWE_IMAGE_DIRS` is set. With it unset, provenance records a digest that
  nothing on that host verified.

---

## Discrepancies found while writing this (ADR vs README vs code)

Where they disagree, this runbook follows the code.

1. **Absolute `dockerPull`.** The pre-#666 ADR (decision 2) had an absolute
   path. The current ADR and the code use a bare name, so the store has to be
   on a worker's `--image-dir`. See § 7.
2. **`apptainer/README.md` § Where images live cites "ADR-0010 decision 2"**
   for the store. That is stale numbering. It is decision 6 / Migration step 3.
3. **The build number comes from `--out`, not from the store** (§ 2). The
   README's "`N` is the next free build number for that version in `--out`" is
   accurate, but "`b2` = a rebuild" only holds if `--out` still contains `b1`.
4. **The default interpreter.** The script uses `python` from `PATH`, which on
   coconut is 3.8 and fails. The README does not document `--python` (or
   `--repo`), and the script's own usage line leaves out `--python`.
5. **Labels compared.** ADR decision 7 says "its labels equal its receipt". The
   code compares `org.ragstack.version`, `commit` and `build`, and **not**
   `build-date` (`LABEL_FIELDS`). The build script itself does check
   `build-date`.
6. **"Boot refuses, never merely warns."** In the code, refusal applies only
   with `INGEST_BACKEND=gowe` **and** `GOWE_IMAGE_DIRS` set. With the dirs
   unset there is one warning (`unchecked`). With no `apptainer` on `PATH` the
   label check is a warning and the sha256 still has to match. The ADR's
   Migration step 4 says this ("wherever the API host can see the store"), but
   decision 7 alone reads stricter.
7. **`ragstack-ctl gowe render` passes when it checked nothing.** With
   `GOWE_IMAGE_DIRS` unset, every stamped workflow comes back `unchecked` and
   the exit code is 0. Pass `--image-dirs` until the key is set.
8. **The installed ctl has no `gowe` verb.** `/rag/bin/ragstack-ctl` is
   `ragstack-ctl-v1.6.2-10-g5a05168` → `unknown command "gowe"` [exercised].
   It needs `make install-ctl` from a checkout at or after `cde401b` (#672).
   `docs/runbooks/ctl-deploy.md` says to build the ctl from `/rag/repos/ragstack`,
   which `STATUS.md` calls frozen (and `ctl-deploy.md` § 3a itself calls stale).
9. **Stale "not landed yet" (fixed in this PR).** `docs/runbooks/bulk-load-throughput.md` § The gating step said the boot/render check had not landed; it landed in #672. The same wording survives in code docstrings (`ragstack/tool_image.py`, `scripts/stamp_tool_image.py`), which belong to a code PR.
10. **Step numbering.** `stamp_tool_image.py`'s docstring calls stamping "step
    2" of the release order. The ADR calls it (d) of decision 6, and the README
    calls it step 4.

## Related

- [ADR-0010](../adr/0010-tool-image-binding.md): decisions 1–8 and the Migration (#655).
- [`apptainer/README.md`](../../apptainer/README.md): version vs build, identity, the receipt, stamping.
- [`verifying-tools-image.md`](verifying-tools-image.md): the boot check and `ragstack-ctl gowe render`, and every failure message.
- [`tenant-upgrade.md` § 4a](tenant-upgrade.md#4a-a-release-whose-cwl-is-stamped-the-tools-image-must-be-where-the-workers-look): moving a tenant onto a stamped release.
- [`cwl/README.md`](../../cwl/README.md): `dockerPull` and `dockerImageId`, post-build checks, worker image dirs.
