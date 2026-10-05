#!/usr/bin/env bash
# build-tools-image.sh — build ONE labelled, receipted tools image (ADR-0010).
#
#   apptainer/build-tools-image.sh [--out DIR] [--dry-run] [--sandbox] [--repo DIR]
#
# What it does, in order:
#   1. derives the repo version — `python -m ragstack.version --shell`, the only
#      place `git describe` runs: vX on a release tag, vX+<shortsha> past one.
#      A DIRTY tree is refused (exit 3); no reachable v* tag: exit 4.
#   1b. stages python/ FROM THE COMMIT (`git archive HEAD python | tar -x`) into
#      a temp dir and passes it to the def as --build-arg SRC: the image ships
#      what `org.ragstack.commit` names, never the working tree. (`--dirty`
#      and an untracked-file check both ignore gitignored content — a build
#      from the tree shipped .mypy_cache, .pytest_cache, __pycache__, and
#      would ship a gitignored python/.env the same way.)
#   2. picks BUILD = next free N for that version in the output dir (b1 if
#      none): a second build of the same version — a base-image rebuild, no
#      code change — is b2, and never forces a release.
#   3. runs
#        apptainer build --fakeroot --build-arg VERSION=… COMMIT=… BUILD=… BUILD_DATE=… \
#            <out>/ragstack-tools-<version>-b<N>.sif apptainer/ragstack-tools.def
#      or, with --sandbox (hosts where --fakeroot is unavailable), the rootless
#      two-step: `build --sandbox <tmp> <def>` then `build <sif> <tmp>` (labels
#      survive the conversion).
#   4. VERIFIES the image: `apptainer inspect --labels` must equal the four
#      inputs and /opt/ragstack/RELEASE inside must say the same; a mismatch
#      deletes the file and exits 1. Then computes the sha256.
#   5. writes <sif>.receipt.json: {name, version, commit, build, build_date, sha256}
#      — the input to python/scripts/stamp_tool_image.py.
#
# --dry-run prints the exact command(s) and the computed name/receipt (sha256
# null) and builds nothing. --out defaults to apptainer/images/ (in-repo,
# gitignored) — never a shared store; moving a release image into
# /scout/containers/ragstack/ is the management session's step 3, by hand, and
# only for release versions (no +<sha>). Non-release builds stay in the
# tenant's own image dir.
#
# Identity is the labels + the digest; the file name is a handle. Nothing here
# touches a running service, a tenant, or any directory but --out.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$HERE")"
OUT=""
DRY_RUN=0
SANDBOX=0
PY="${PYTHON:-python}"

usage() { sed -n '2,40p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
    case "$1" in
        --out)      OUT="$2"; shift 2 ;;
        --repo)     REPO="$(cd "$2" && pwd)"; shift 2 ;;
        --dry-run)  DRY_RUN=1; shift ;;
        --sandbox)  SANDBOX=1; shift ;;
        --python)   PY="$2"; shift 2 ;;
        -h|--help)  usage; exit 0 ;;
        *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
done

DEF="$REPO/apptainer/ragstack-tools.def"
[ -n "$OUT" ] || OUT="$REPO/apptainer/images"
[ -f "$DEF" ] || { echo "no def file at $DEF" >&2; exit 2; }

# ---- 1. the version ---------------------------------------------------------
set +e
IDENT="$(PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$REPO/python" "$PY" -m ragstack.version --repo "$REPO" --shell)"
rc=$?
set -e
if [ $rc -ne 0 ]; then
    echo "build-tools-image: refusing to build (ragstack.version exit $rc)" >&2
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
    echo "build-tools-image: unusable version/commit from ragstack.version: $IDENT" >&2
    exit 2
fi
BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# ---- 2. the build number ------------------------------------------------------
N=0
shopt -s nullglob
for f in "$OUT/ragstack-tools-$VERSION-b"*.sif; do
    b="${f##*-b}"; b="${b%.sif}"
    if [[ "$b" =~ ^[0-9]+$ ]] && [ "$b" -gt "$N" ]; then N="$b"; fi
done
shopt -u nullglob
BUILD=$((N + 1))
NAME="ragstack-tools-$VERSION-b$BUILD.sif"
SIF="$OUT/$NAME"
RECEIPT="$SIF.receipt.json"

# ---- 1b. the source: the commit's python/, not the working tree's -------------
STAGE="${TMPDIR:-/tmp}/ragstack-tools-src.$$"
SRC="$STAGE/python"
STAGE_CMD="git -C $(printf '%q' "$REPO") archive --format=tar HEAD python | tar -x -C $(printf '%q' "$STAGE")"
BUILD_ARGS=(--build-arg "VERSION=$VERSION" --build-arg "COMMIT=$COMMIT"
            --build-arg "BUILD=$BUILD" --build-arg "BUILD_DATE=$BUILD_DATE"
            --build-arg "SRC=$SRC")
SBX="${TMPDIR:-/tmp}/ragstack-tools-$VERSION-b$BUILD.sbx.$$"
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

echo "version:    $VERSION"
echo "commit:     $COMMIT"
echo "build:      $BUILD"
echo "build_date: $BUILD_DATE"
echo "image:      $SIF"
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
git -C "$REPO" archive --format=tar HEAD python | tar -x -C "$STAGE"
[ -d "$SRC/ragstack" ] || { echo "staging failed: no $SRC/ragstack" >&2; exit 1; }
"${CMD1[@]}"
if [ ${#CMD2[@]} -gt 0 ]; then
    "${CMD2[@]}"
    rm -rf "$SBX"
fi

# ---- 4. verify ----------------------------------------------------------------
# (the inspect output goes in by argv, not a pipe: the heredoc below owns stdin)
LABELS_JSON="$(apptainer inspect --json --labels "$SIF")"
if ! "$PY" - "$VERSION" "$COMMIT" "$BUILD" "$BUILD_DATE" "$LABELS_JSON" <<'EOF'
import json, sys
want = dict(zip(("org.ragstack.version", "org.ragstack.commit", "org.ragstack.build",
                 "org.ragstack.build-date"), sys.argv[1:5]))
labels = json.loads(sys.argv[5])["data"]["attributes"]["labels"]
bad = {k: (labels.get(k), v) for k, v in want.items() if labels.get(k) != v}
if bad:
    for k, (got, exp) in bad.items():
        print(f"label {k}: image says {got!r}, build passed {exp!r}", file=sys.stderr)
    sys.exit(1)
print("labels ok: " + ", ".join(f"{k}={v}" for k, v in want.items()))
EOF
then
    echo "build-tools-image: label verification FAILED; removing $SIF" >&2
    rm -f "$SIF"
    exit 1
fi
RELEASE_IN_IMAGE="$(apptainer exec "$SIF" cat /opt/ragstack/RELEASE)"
EXPECTED_RELEASE="$(printf 'version=%s\ncommit=%s\nbuild=%s\nbuild_date=%s' "$VERSION" "$COMMIT" "$BUILD" "$BUILD_DATE")"
if [ "$RELEASE_IN_IMAGE" != "$EXPECTED_RELEASE" ]; then
    echo "build-tools-image: /opt/ragstack/RELEASE inside the image disagrees with the inputs; removing $SIF" >&2
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
echo "Next (release only): python/scripts/stamp_tool_image.py $RECEIPT"
