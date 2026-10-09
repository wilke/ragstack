# Building the server and tools images

PR-F (plan `plan-tenant-control-plane-2026-09-10.pr-f.md` §1.1) and ADR-0010
migration step 6. Two images, one build script, one identity mechanism:

| | tools image | server image |
|---|---|---|
| what | the CWL steps' runtime (ingest/embed/load/extract scripts) | the Python API (`uvicorn ragstack.api.main:app`) |
| def | `apptainer/ragstack-tools.def` | `apptainer/ragstack-server.def` |
| name | `ragstack-tools-<version>-b<N>.sif` | `ragstack-server-<version>-b<N>.sif` |
| `org.ragstack.role` | `worker` | `server` |
| staged from the commit | `python/` → `/opt/ragstack/python` | `python/` + `cwl/` → `/opt/ragstack/{python,cwl}` |
| runs as | `apptainer exec` by a GoWe worker | `apptainer run` (the `%runscript`), an instance owned by the ctl |
| lives in | `/scout/containers/ragstack/` (GoWe's placement; released builds only, copied by hand — ADR-0010 decision 6 (c)) | `/rag/data/ctl/images/server/`, entered only through `ragstack-ctl fleet image prepare --sif` (PR-F F3, forthcoming) |

Both carry the same extras (`vector,pdf,text,tokenization,graph,postgres` +
sentencepiece/protobuf; no torch) and read the HF tokenizer from a bound
`HF_HOME` (default `/rag/cache`).

## Build

From a **clean** checkout at the commit you want (a dirty tree is refused,
exit 3; no reachable `v*` tag, exit 4):

```bash
export PYTHON=/rag/envs/ragstack/bin/python   # >= 3.11; a bare `python` on coconut is 3.8

apptainer/build-image.sh --kind server --dry-run            # name, commands, receipt; builds nothing
apptainer/build-image.sh --kind server --sandbox --out DIR  # coconut: --fakeroot does not work, use --sandbox
apptainer/build-image.sh --kind tools  --sandbox --out DIR --store /scout/containers/ragstack
apptainer/build-tools-image.sh …                            # == build-image.sh --kind tools …
```

The flags (`--out`, `--store`, `--repo`, `--python`, `--dry-run`, `--sandbox`)
are documented in the script header and in `apptainer/README.md`. Pass
`--store` with every directory that already holds released builds of the
kind, or the build number restarts at `b1`. For the server image that is
`--store /rag/data/ctl/images/server` once F3 exists.

**Building an older tag.** A tag that predates `ragstack-server.def` (v1.6.6
and earlier) has no server def in its tree. Build it from a checkout at the
tag with the def from a newer tree:

```bash
cd ~/Development/worktrees/rel-1.6.6        # clean, at the tag
PYTHON=/rag/envs/ragstack/bin/python \
  ~/Development/worktrees/<newer>/apptainer/build-image.sh --kind server \
    --repo "$PWD" --def ~/Development/worktrees/<newer>/apptainer/ragstack-server.def \
    --sandbox --out /path/to/out
```

The def is the recipe; the image's identity is still the tag's commit
(`python/` and `cwl/` come from `git archive HEAD` of `--repo`). Such a tree
also predates the `_release.py` fallback of `git_tag`/`git_sha` (below): its
build prints a WARNING, and `/v1/version` inside it reports `git_tag`/`git_sha`
null unless `RAGSTACK_GIT_TAG`/`RAGSTACK_GIT_SHA` are set — which the ctl
always sets for an image-mode tenant (the receipt's version and full commit).

What the script does: derive the version (`python -m ragstack.version
--shell`) → stage the commit's tree into a temp dir → pick `b<N>` → `apptainer
build` → verify the labels (`version`, `commit`, `build`, `build-date` and
`role`) and `/opt/ragstack/RELEASE` against its inputs (a mismatch deletes the
image) → sha256 → write `<sif>.receipt.json`.

## Identity

Three places carry the same four values; the file name is only a handle.

* **Labels** — `apptainer inspect --labels <sif>`: `org.ragstack.role`,
  `org.ragstack.version`, `org.ragstack.commit` (full sha),
  `org.ragstack.build`, `org.ragstack.build-date`.
* **`/opt/ragstack/RELEASE`** — `version=… commit=… build=… build_date=…`,
  and the same values as `ragstack/_release.py` in both copies of the package
  (site-packages and the staged tree).
* **The receipt** — `<sif>.receipt.json` beside the image:
  `{name, version, commit, build, build_date, sha256}`, byte-compatible
  between the two kinds. Keep it with the image; `fleet image prepare`
  refuses an image whose receipt, labels and sha256 disagree.

Inside the server image there is no git and `/opt/ragstack` is no checkout,
so `ragstack.version` answers from `_release.py`: `__version__` is the PEP 440
form (`1.6.7`), `git_tag` the `VERSION` (`v1.6.7`), `git_sha` the **full**
`COMMIT`. `RAGSTACK_GIT_TAG`/`RAGSTACK_GIT_SHA` in the environment still win.

## Server image runtime

* `%environment`: `PYTHONPATH=/opt/ragstack/python` (the API imports the
  staged tree; `parents[2]` is `/opt/ragstack`, so the `GRAPH_EXTRACT_CWL` and
  `COLLECTION_RESTORE_CWL` defaults resolve to `/opt/ragstack/cwl/…`),
  `HF_HOME=/rag/cache`, `PYTHONUNBUFFERED=1`. Under `--cleanenv`,
  `APPTAINERENV_*` variables still override these.
* `%runscript`: `umask 0002; cd /opt/ragstack; exec python -m uvicorn
  ragstack.api.main:app "$@"`, stdout and stderr **appended** to
  `$RAGSTACK_API_LOG` when it is set (the ctl sets
  `<DataDir>/logs/api-<t>.log`), inherited otherwise.
* The image root is read-only. Every path-valued setting must be absolute and
  bound: several default to relative `ragstack_*.db` names, which would
  resolve under `/opt/ragstack` and fail.
* No `apptainer` inside: the boot tool-image check verifies sha256 + receipt
  and logs the labels as "not checked"; the ctl checks the labels (PR-F §1.1).

## Checks after a build

```bash
SIF=/path/to/ragstack-server-<version>-b<N>.sif
apptainer inspect --labels "$SIF"                 # role server, version, commit
apptainer exec --cleanenv "$SIF" cat /opt/ragstack/RELEASE
apptainer exec --cleanenv "$SIF" python -c \
  'import ragstack, ragstack.graph_extract as g, ragstack.version as v; print(ragstack.__file__, g.DEFAULT_CWL, v.git_sha(), v.git_tag())'
sha256sum "$SIF"; cat "$SIF.receipt.json"
```

A boot smoke (memory backends, a throwaway key, nothing real touched):

```bash
T=$(mktemp -d /rag/data/ctl-selftest/server-image/smoke.XXXX)
KEY=$(python3 -c 'import secrets; print(secrets.token_hex(16))')
env APPTAINERENV_REQUIRE_DURABLE_BACKENDS=false APPTAINERENV_VECTOR_BACKEND=memory \
    APPTAINERENV_TEXT_BACKEND=memory APPTAINERENV_GRAPH_BACKEND=disabled \
    APPTAINERENV_COLLECTION_STORE_BACKEND=memory \
    APPTAINERENV_COLLECTION_STORE_PATH=$T/collections.db APPTAINERENV_JOB_STORE_PATH=$T/jobs.db \
    APPTAINERENV_USER_STORE_PATH=$T/users.db APPTAINERENV_GRADING_STORE_PATH=$T/grading.db \
    APPTAINERENV_API_KEYS="[\"$KEY\"]" APPTAINERENV_RAGSTACK_API_LOG=$T/api.log \
  apptainer run --no-home --cleanenv -B "$T" "$SIF" --host 127.0.0.1 --port 26090 &
curl -s -H "X-API-Key: $KEY" 127.0.0.1:26090/v1/version    # git_sha == the commit
kill %1; grep -c uvicorn "$T/api.log"
```

## Not here

Registering a server image (`fleet image prepare`, F3), running a tenant's
API from one (F4) and `tenant update-code` (F5) — see the PR-F brief and,
once they land, `tenant-upgrade.md`.
