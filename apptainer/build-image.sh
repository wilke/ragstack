#!/usr/bin/env bash
# build-image.sh — build ONE labelled, receipted ragstack image (ADR-0010, PR-F).
#
#   apptainer/build-image.sh --kind tools|server [--out DIR] [--store DIR]...
#                            [--dry-run] [--sandbox] [--repo DIR] [--python EXE]
#                            [--def FILE]
#
#   --kind KIND    tools   — ragstack-tools-<version>-b<N>.sif from
#                            apptainer/ragstack-tools.def; stages python/.
#                            (apptainer/build-tools-image.sh is this with
#                            --kind tools.) The CWL steps' image (GoWe).
#                  server  — ragstack-server-<version>-b<N>.sif from
#                            apptainer/ragstack-server.def; stages python/ AND
#                            cwl/. The Python API (`apptainer run`), see
#                            docs/runbooks/server-image.md.
#                  Required.
#   --def FILE     the def file (default: <repo>/apptainer/ragstack-<kind>.def).
#                  Only for building a commit whose tree predates its def
#                  (e.g. a server image of v1.6.6): the def is the recipe, the
#                  image's identity is still the commit's python/ (+ cwl/).
#   --out DIR      where the image and its receipt are written
#                  (default apptainer/images/, gitignored).
#   --store DIR    a dir where RELEASED builds of this version live (the shared
#                  release store, e.g. /scout/containers/ragstack, and/or a
#                  tenant's image dir). Repeatable, or comma-separated. Read
#                  only — nothing is written there. Pass it whenever the build
#                  may be released: a fresh worktree's --out is empty, and
#                  without --store the build number restarts at b1 and
#                  collides with a b1 already released.
#   --repo DIR     the checkout to build from (default: this script's repo).
#   --python EXE   the interpreter for the version step and the receipt
#                  (needs >= 3.11). Default: $PYTHON if set, else `python3`
#                  only if it is >= 3.11, else a refusal (exit 2) — a bare
#                  `python` is miniconda 3.8 on coconut and dies on
#                  datetime.UTC.
#   --dry-run      print the command(s), the name and the receipt; build nothing.
#   --sandbox      the rootless two-step build (no --fakeroot).
#
# What it does, in order:
#   1. derives the repo version — `python -m ragstack.version --shell`, the only
#      place `git describe` runs: vX on a release tag, vX+<shortsha> past one.
#      A DIRTY tree is refused (exit 3); no reachable v* tag: exit 4.
#   1b. stages python/ (server: python/ and cwl/) FROM THE COMMIT
#      (`git archive HEAD python [cwl] | tar -x`) into a temp dir and passes
#      them to the def as --build-arg SRC (server: and CWL): the image ships
#      what `org.ragstack.commit` names, never the working tree. (`--dirty`
#      and an untracked-file check both ignore gitignored content — a build
#      from the tree shipped .mypy_cache, .pytest_cache, __pycache__, and
#      would ship a gitignored python/.env the same way.)
#   2. picks BUILD = 1 + the highest N found for that version across --out AND
#      every --store (b1 if none; a receipt without its .sif counts too): a
#      second build of the same version — a base-image rebuild, no code
#      change — is b2, and never forces a release. REFUSES (exit 1) if
#      <out>/<name> or <store>/<name> already exists, and (exit 2) if a
#      --store dir does not exist (a typo would silently restart at b1).
#   3. runs
#        apptainer build --fakeroot --build-arg VERSION=… COMMIT=… BUILD=… BUILD_DATE=… \
#            <out>/ragstack-<kind>-<version>-b<N>.sif apptainer/ragstack-<kind>.def
#      or, with --sandbox (hosts where --fakeroot is unavailable), the rootless
#      two-step: `build --sandbox <tmp> <def>` then `build <sif> <tmp>` (labels
#      survive the conversion).
#   4. VERIFIES the image: `apptainer inspect --labels` must equal the four
#      inputs (and org.ragstack.role: worker for tools, server for server) and /opt/ragstack/RELEASE inside must say the same; a mismatch
#      deletes the file and exits 1. Then computes the sha256.
#   5. writes <sif>.receipt.json: {name, version, commit, build, build_date, sha256}
#      — the input to python/scripts/stamp_tool_image.py.
#
# --dry-run prints the exact command(s) and the computed name/receipt (sha256
# null) and builds nothing. --out defaults to apptainer/images/ (in-repo,
# gitignored) — never a shared store. TOOLS: moving a release image into
# /scout/containers/ragstack/ is the management session's job (ADR-0010
# decision 6 (c) / Migration step 3), by hand, and only for release versions
# (no +<sha>); non-release builds stay in the tenant's own image dir. SERVER:
# images enter /rag/data/ctl/images/server/ only through
# `ragstack-ctl fleet image prepare --sif` (PR-F F3), never by hand.
#
# Identity is the labels + the digest; the file name is a handle. Nothing here
# touches a running service, a tenant, or any directory but --out.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$HERE")"
OUT=""
KIND=""
DEF=""
DRY_RUN=0
SANDBOX=0
PY=""
STORES=()

usage() { sed -n '2,/^set -euo/p' "${BASH_SOURCE[0]}" | sed '$d' | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
    case "$1" in
        --kind)     KIND="$2"; shift 2 ;;
        --def)      DEF="$2"; shift 2 ;;
        --out)      OUT="$2"; shift 2 ;;
        --store)    IFS=',' read -r -a _s <<< "$2"
                    for d in "${_s[@]}"; do [ -n "$d" ] && STORES+=("$d"); done
                    shift 2 ;;
        --repo)     REPO="$(cd "$2" && pwd)"; shift 2 ;;
        --dry-run)  DRY_RUN=1; shift ;;
        --sandbox)  SANDBOX=1; shift ;;
        --python)   PY="$2"; shift 2 ;;
        -h|--help)  usage; exit 0 ;;
        *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
done

case "$KIND" in
    tools)  ROLE=worker; PARTS=(python) ;;
    server) ROLE=server; PARTS=(python cwl) ;;
    "")     echo "build-image: --kind tools|server is required" >&2; exit 2 ;;
    *)      echo "build-image: unknown --kind $KIND (tools|server)" >&2; exit 2 ;;
esac
ME="build-image --kind $KIND"
PREFIX="ragstack-$KIND"

# ---- 0. the interpreter --------------------------------------------------------
# --python, else $PYTHON, else python3 only if it is >= 3.11. Never a bare
# `python`: on coconut that is miniconda 3.8, which dies on datetime.UTC.
if [ -z "$PY" ] && [ -n "${PYTHON:-}" ]; then PY="$PYTHON"; fi
if [ -z "$PY" ]; then
    if command -v python3 >/dev/null 2>&1 \
        && python3 -c 'import sys; sys.exit(0 if sys.version_info>=(3,11) else 1)' 2>/dev/null; then
        PY="python3"
    else
        echo "$ME: no Python >= 3.11 found: \`python3\` is $(python3 --version 2>&1 || echo missing)." >&2
        echo "  Pass --python /path/to/python3.11+ or set PYTHON (e.g. PYTHON=/rag/envs/ragstack/bin/python)." >&2
        exit 2
    fi
fi

for d in "${STORES[@]}"; do
    [ -d "$d" ] || { echo "$ME: --store $d is not a directory (a wrong store would restart the build number at b1)" >&2; exit 2; }
done

[ -n "$DEF" ] || DEF="$REPO/apptainer/$PREFIX.def"
if [ -d "$(dirname "$DEF")" ]; then DEF="$(cd "$(dirname "$DEF")" && pwd)/$(basename "$DEF")"; fi
[ -n "$OUT" ] || OUT="$REPO/apptainer/images"
[ -f "$DEF" ] || { echo "no def file at $DEF" >&2; exit 2; }

# ---- 1. the version ---------------------------------------------------------
set +e
IDENT="$(PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$REPO/python" "$PY" -m ragstack.version --repo "$REPO" --shell)"
rc=$?
set -e
if [ $rc -ne 0 ]; then
    echo "$ME: refusing to build (ragstack.version exit $rc)" >&2
    exit $rc
fi
VERSION=""; COMMIT=""
while IFS= read -r line; do
    case "$line" in
        VERSION=*) VERSION="${line#VERSION=}" ;;
        COMMIT=*)  COMMIT="${line#COMMIT=}" ;;
    esac
done <<< "$IDENT"
if ! [[ "$VERSION" =~ ^v[0-9][A-Za-z0-9.+-]*$ ]] || ! [[ "$COMMIT" =~ ^[0-9a-f]{40}$ ]]; then
    echo "$ME: unusable version/commit from ragstack.version: $IDENT" >&2
    exit 2
fi
BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# ---- 2. the build number ------------------------------------------------------
# 1 + the highest build of THIS version in --out and every --store: a fresh
# worktree's --out is empty, so --out alone would mint a b1 the store holds.
N=0
shopt -s nullglob
for dir in "$OUT" "${STORES[@]}"; do
    for f in "$dir/$PREFIX-$VERSION-b"*.sif "$dir/$PREFIX-$VERSION-b"*.sif.receipt.json; do
        b="${f##*-b}"; b="${b%.receipt.json}"; b="${b%.sif}"
        if [[ "$b" =~ ^[0-9]+$ ]] && [ "$((10#$b))" -gt "$N" ]; then N="$((10#$b))"; fi
    done
done
shopt -u nullglob
BUILD=$((N + 1))
NAME="$PREFIX-$VERSION-b$BUILD.sif"
SIF="$OUT/$NAME"
RECEIPT="$SIF.receipt.json"
for dir in "$OUT" "${STORES[@]}"; do
    if [ -e "$dir/$NAME" ] || [ -e "$dir/$NAME.receipt.json" ]; then
        echo "$ME: refusing: $dir/$NAME (or its receipt) already exists — a build name is never reused" >&2
        exit 1
    fi
done

# ---- 1b. the source: the commit's python/ (+ cwl/), not the working tree's -----
STAGE="${TMPDIR:-/tmp}/$PREFIX-src.$$"
SRC="$STAGE/python"
STAGE_CMD="git -C $(printf '%q' "$REPO") archive --format=tar HEAD ${PARTS[*]} | tar -x -C $(printf '%q' "$STAGE")"
BUILD_ARGS=(--build-arg "VERSION=$VERSION" --build-arg "COMMIT=$COMMIT"
            --build-arg "BUILD=$BUILD" --build-arg "BUILD_DATE=$BUILD_DATE"
            --build-arg "SRC=$SRC")
if [ "$KIND" = server ]; then BUILD_ARGS+=(--build-arg "CWL=$STAGE/cwl"); fi
SBX="${TMPDIR:-/tmp}/$PREFIX-$VERSION-b$BUILD.sbx.$$"
if [ "$SANDBOX" -eq 1 ]; then
    CMD1=(apptainer build --sandbox "${BUILD_ARGS[@]}" "$SBX" "$DEF")
    CMD2=(apptainer build "$SIF" "$SBX")
else
    CMD1=(apptainer build --fakeroot "${BUILD_ARGS[@]}" "$SIF" "$DEF")
    CMD2=()
fi

receipt_json() {  # $1 = sha256 or empty (→ null)
    "$PY" - "$NAME" "$VERSION" "$COMMIT" "$BUILD" "$BUILD_DATE" "${1:-}" <<'EOF'
import json, sys
name, version, commit, build, date, sha = sys.argv[1:7]
print(json.dumps({"name": name, "version": version, "commit": commit, "build": build,
                  "build_date": date, "sha256": sha or None}, indent=2))
EOF
}

echo "kind:       $KIND (role $ROLE)"
echo "def:        $DEF"
echo "version:    $VERSION"
echo "commit:     $COMMIT"
echo "build:      $BUILD"
echo "build_date: $BUILD_DATE"
echo "image:      $SIF"
[ ${#STORES[@]} -gt 0 ] && echo "stores:     ${STORES[*]}"
echo "receipt:    $RECEIPT"
echo "stage:      $STAGE_CMD"
echo "command:    $(printf '%q ' "${CMD1[@]}")"
[ ${#CMD2[@]} -gt 0 ] && echo "then:       $(printf '%q ' "${CMD2[@]}")"
if [ "$DRY_RUN" -eq 1 ]; then
    echo "--- receipt (dry run; sha256 unknown until built) ---"
    receipt_json ""
    exit 0
fi

# ---- 3. build -----------------------------------------------------------------
mkdir -p "$OUT"
[ -e "$SIF" ] && { echo "refusing to overwrite existing $SIF" >&2; exit 1; }
mkdir -p "$STAGE"
trap 'rm -rf "$STAGE"' EXIT
git -C "$REPO" archive --format=tar HEAD "${PARTS[@]}" | tar -x -C "$STAGE"
[ -d "$SRC/ragstack" ] || { echo "staging failed: no $SRC/ragstack" >&2; exit 1; }
if [ "$KIND" = server ]; then
    [ -d "$STAGE/cwl" ] || { echo "staging failed: no $STAGE/cwl" >&2; exit 1; }
fi
"${CMD1[@]}"
if [ ${#CMD2[@]} -gt 0 ]; then
    "${CMD2[@]}"
    rm -rf "$SBX"
fi

# ---- 4. verify ----------------------------------------------------------------
# (the inspect output goes in by argv, not a pipe: the heredoc below owns stdin)
LABELS_JSON="$(apptainer inspect --json --labels "$SIF")"
if ! "$PY" - "$VERSION" "$COMMIT" "$BUILD" "$BUILD_DATE" "$ROLE" "$LABELS_JSON" <<'EOF'
import json, sys
want = dict(zip(("org.ragstack.version", "org.ragstack.commit", "org.ragstack.build",
                 "org.ragstack.build-date", "org.ragstack.role"), sys.argv[1:6]))
labels = json.loads(sys.argv[6])["data"]["attributes"]["labels"]
bad = {k: (labels.get(k), v) for k, v in want.items() if labels.get(k) != v}
if bad:
    for k, (got, exp) in bad.items():
        print(f"label {k}: image says {got!r}, build passed {exp!r}", file=sys.stderr)
    sys.exit(1)
print("labels ok: " + ", ".join(f"{k}={v}" for k, v in want.items()))
EOF
then
    echo "$ME: label verification FAILED; removing $SIF" >&2
    rm -f "$SIF"
    exit 1
fi
RELEASE_IN_IMAGE="$(apptainer exec "$SIF" cat /opt/ragstack/RELEASE)"
EXPECTED_RELEASE="$(printf 'version=%s\ncommit=%s\nbuild=%s\nbuild_date=%s' "$VERSION" "$COMMIT" "$BUILD" "$BUILD_DATE")"
if [ "$RELEASE_IN_IMAGE" != "$EXPECTED_RELEASE" ]; then
    echo "$ME: /opt/ragstack/RELEASE inside the image disagrees with the inputs; removing $SIF" >&2
    printf '%s\n' "$RELEASE_IN_IMAGE" >&2
    rm -f "$SIF"
    exit 1
fi
echo "RELEASE ok"
SHA256="$(sha256sum "$SIF" | cut -d' ' -f1)"

# ---- 5. receipt ---------------------------------------------------------------
receipt_json "$SHA256" > "$RECEIPT"
echo "sha256:     $SHA256"
echo "wrote       $RECEIPT"
echo
if [ "$KIND" = tools ]; then
    echo "Next (release only): python/scripts/stamp_tool_image.py $RECEIPT"
else
    echo "Next: ragstack-ctl fleet image prepare --sif $SIF   (docs/runbooks/server-image.md)"
fi
