# apptainer/ — rootless deployment and the tools image

The `*.sh` scripts here bring up the infra stack and sidecars without Docker
(see [CLAUDE.md § Apptainer deployment](../CLAUDE.md) and `MEMORY.md` for the
rootless quirks). This file is about the **tools image** — the container every
CWL `CommandLineTool` step runs in — and how it is named, labelled, built and
bound to a release (ADR-0010, issue #655).

## Version vs build

A tenant runs a **repo version**; an image is **one build** of some source.
Neither is the other, and they are named separately.

| Thing | Spelling | Where it comes from |
|---|---|---|
| repo version | `v1.6.4` on a release tag; `v1.6.4+a2be96f` past one (dev on `main`) | `python -m ragstack.version` — the only `git describe` in the repo (`ragstack/version.py`). A **dirty tree has no version**: the derivation refuses. |
| `ragstack.__version__` | the same version as PEP 440: `1.6.4`, `1.6.4+a2be96f` | lazily, from the checkout; inside the image from the generated `ragstack/_release.py`; else the distribution version |
| image build | `ragstack-tools-<version>-b<N>.sif`, e.g. `ragstack-tools-v1.6.4-b1.sif`, `ragstack-tools-v1.6.4+a2be96f-b1.sif` | `build-tools-image.sh`; `N` is the next free build number for that version in `--out` |

One version may have several builds: `b2` is a base-image security rebuild
with no code change, so a rebuild never forces a release. The `+<sha>` part
is build metadata and **orders nothing** (PEP 440 sorts it after its public
version and lexicographically between locals — meaningless for shas; we never
compare two derived versions). The `.sif` suffix is load-bearing: GoWe keeps a
`dockerPull` ending in `.sif` as a local image and prefixes anything else with
`docker://` (engine fact, 2026-10-04). A `+` in the name is safe — GoWe's
`resolveApptainerImage` is `filepath.Join(imageDir, name)`, no escaping.

`python/pyproject.toml` keeps a static `version` = the public part of the last
release (`1.6.4`). The stamping step bumps it; `tests/unit/test_version_derivation.py`
asserts it equals the tag part of the derived version. Metadata and
`__version__` therefore agree on the release and differ only in the local
segment that names the commit.

## Identity: labels, `RELEASE`, digest

The file name is a human handle. What proves an image is:

- `apptainer inspect --labels <sif>` →
  `org.ragstack.version`, `org.ragstack.commit` (full sha), `org.ragstack.build`,
  `org.ragstack.build-date` (plus the older `org.ragstack.role/gpu/issue`);
- `/opt/ragstack/RELEASE` inside the image — the same four values as
  `key=value` lines — and `ragstack/_release.py` in the installed package, so
  `apptainer exec <sif> python -c 'import ragstack; print(ragstack.__version__)'`
  answers without git (the build asserts it equals the version it was given);
- the **sha256 digest** of the file, recorded in the receipt and stamped into
  every `dockerImageId`.

The def file has no `%arguments` defaults on purpose: `apptainer build` without
the four `--build-arg`s fails (`build var VERSION is not defined`) instead of
minting an unlabelled image.

## Building

```bash
apptainer/build-tools-image.sh --dry-run        # prints the command, name and receipt; builds nothing
apptainer/build-tools-image.sh                  # → apptainer/images/ragstack-tools-<version>-b<N>.sif
apptainer/build-tools-image.sh --sandbox        # hosts without --fakeroot: build --sandbox, then sif
apptainer/build-tools-image.sh --out /some/dir  # never a shared store (see below)
```

The script: derives the version (refuses a dirty tree, exit 3; exit 4 with no
reachable `v*` tag), stages `python/` **from the commit** (`git archive HEAD`,
passed as `--build-arg SRC`, so gitignored working-tree content such as
`.mypy_cache`, `__pycache__` or a `python/.env` never ships and
`org.ragstack.commit` is true by construction) → picks `b<N>` → `apptainer build --fakeroot --build-arg …`
→ **verifies** the labels and `/opt/ragstack/RELEASE` against its inputs
(a mismatch deletes the image) → sha256 → writes the receipt.

### The receipt

`<sif>.receipt.json`, beside the image:

```json
{
  "name": "ragstack-tools-v1.6.4-b1.sif",
  "version": "v1.6.4",
  "commit": "ffd04cb…(40 hex)",
  "build": "1",
  "build_date": "2026-10-05T03:00:00Z",
  "sha256": "…64 hex…"
}
```

It is the input to the stamping step and the record of what was built; keep
it with the image.

## Stamping (release only)

> Flow under review (#655): the tag/build/stamp/re-tag sequence below and the
> strictness of the pin test on post-release `main` are an open owner decision.

```bash
python python/scripts/stamp_tool_image.py apptainer/images/ragstack-tools-v1.6.5-b1.sif.receipt.json
python python/scripts/stamp_tool_image.py --check     # the tree-wide gate
```

Stamping writes the receipt's `name` into every `dockerPull` and its `sha256`
into every `dockerImageId` of `cwl/*.cwl`, and bumps `pyproject.toml`. It
refuses — writing nothing — when the receipt's version is not the checkout's
derived version, when any image site is in a form the rewrite cannot see
(flow mapping, list item, `dockerPull :`, key case, value on the next line —
#642's refuse-on-partial rule, now at release time), or when any `dockerPull`
does not end in `.sif`.

The tree is in exactly one of two states, and `tests/unit/test_cwl_tool_image_pin.py`
fails anything else:

- **unstamped** — every `dockerPull` is the bare `ragstack-worker.sif`. This
  is `main`. Dev runs `main` and resolves that name through its worker's
  `--image-dir` symlink, so the bare name stays until a release stamps it.
- **stamped** — every `dockerPull` names one `ragstack-tools-<version>-b<N>.sif`
  whose `<version>` equals the checkout's derived version, and every
  `dockerImageId` is a sha256.

A stamped tree therefore only passes at the commit whose derived version the
image carries — i.e. on the tag. The stamp commit is the tag commit: tag
locally, build, stamp, commit, move the tag onto the stamp commit, push the
tag once (the image's `org.ragstack.commit` is then the tag's parent, which
differs from the tag only by `cwl/` and `pyproject.toml`). Which branch the
tag lives on — `main`, or a release branch so `main` stays unstamped — is the
owner's call and is not decided here.

What `dockerImageId` enforces today: **nothing at the engine.** GoWe parses
`dockerImageId` and never checks it; cwltool `--singularity` reads it as a
filename. The digest is enforced only by our own render/boot check (ADR-0010
decision 5, migration step 4); until that lands it is a recorded fact a reader
can verify with `sha256sum`, not a gate.

## Where images live

`--out` defaults to `apptainer/images/` (gitignored). The shared release store
(`/scout/containers/ragstack/ragstack-tools-<version>-b<N>.sif`, ADR-0010
decision 2) is the management session's, by hand, and takes **release versions
only** — a `+<sha>` build never enters it; it lives in the tenant's own image
dir. Nothing in this directory writes to `/scout` or `/rag`.

## There is no image override

`GOWE_TOOL_IMAGE` (#614/#642) is retired: the API refuses to boot while it is
set (naming ADR-0010 and #655), `ragstack-ctl adopt` warns on it, and the
substitution no longer runs on the registration path. A tenant changes its
tool image by checking out a different tag. What survives of #642 is its
checks, in `ragstack/tool_image.py`, run by the stamping step.
