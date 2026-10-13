#!/usr/bin/env bash
# Layer A (reproduce at the pin) and the CURRENT-CODE run of the same gate scripts.
#
# Re-runs every gate script that produced a Paper A claim source, (1) at the commit that
# committed it, in a detached worktree under ~/Development/worktrees/, and (2) from THIS
# checkout (main), writing only to "$OUT". The harnesses are not edited; they are relocated by
# environment only: STAGE0_BIG points at a scratch copy of the DEV label inputs (the gate
# scripts also write under $STAGE0_BIG/work, so the shared /rag/tmp tree must not be used).
# Nothing under work/conf is copied or read. No s0c_* script is run.
#
#   OUT=/path/to/scratch bash reproduce_pinned.sh
set -euo pipefail
: "${OUT:?set OUT to a scratch directory}"
PY=${PY:-/rag/envs/ragstack/bin/python3}
REPO=${REPO:-$HOME/Development/ragstack}
SRC_WORK=${SRC_WORK:-${STAGE0_DEV_WORK:-/rag/tmp/stage0-conf/work}}
HELPERS=${STAGE0_HELPERS:-$HOME/Development/worktrees/phase0-rescue/phase0}
HERE=$(cd "$(dirname "$0")" && pwd)
MAIN_ROOT=$(git -C "$HERE" rev-parse --show-toplevel)

# scratch STAGE0_BIG with only the DEV inputs the gate scripts read
export STAGE0_BIG="$OUT/big"
mkdir -p "$STAGE0_BIG/work"
cp "$SRC_WORK/labels.jsonl" "$SRC_WORK/label_meta.json" "$STAGE0_BIG/work/"
for d in r3 r31 r31ext claude; do
  mkdir -p "$STAGE0_BIG/work/$d"
  cp "$SRC_WORK/$d"/label-manifest* "$SRC_WORK/$d"/labels-* "$STAGE0_BIG/work/$d/"
done
cp "$SRC_WORK"/claude/raw-{sonnet5,opus5,fable51}.jsonl "$STAGE0_BIG/work/claude/"

run_set() {   # $1 = stage0 dir, $2 = output dir, $3.. = which (s0 r3 r31 r31ext claude)
  local s0dir=$1 od=$2; shift 2
  mkdir -p "$od"/{r3,r31,r31ext,claude}
  for w in "$@"; do
    case $w in
      s0) (cd "$s0dir" && PYTHONPATH="$HELPERS/stage1:$HELPERS/pilots" "$PY" s0_labelgates.py \
             > "$od/s0.log" 2>&1 && cp "$STAGE0_BIG/work/label_gates.json" "$od/label_gates.json") ;;
      r3) (cd "$s0dir" && "$PY" s0_labelgates_r3.py --outdir "$od/r3" > "$od/r3.log" 2>&1) ;;
      r31) (cd "$s0dir" && "$PY" s0_labelgates_r31.py --outdir "$od/r31" > "$od/r31.log" 2>&1) ;;
      r31ext) (cd "$s0dir" && "$PY" s0_labelgates_r31ext.py --outdir "$od/r31ext" > "$od/r31ext.log" 2>&1
               cd "$s0dir" && "$PY" s0_labelgates_r31ext.py --prefit > "$od/r31ext/prefit.json" 2>"$od/prefit.log") ;;
      claude) (cd "$s0dir" && "$PY" s0_gates_claude.py --outdir "$od/claude" > "$od/claude.log" 2>&1) ;;
    esac
  done
}

declare -A PIN=([s0]=b1f9ead [r3]=0886ddc [r31]=bd9db86 [r31ext]=fc1ace4 [claude]=9696d41)
for w in s0 r3 r31 r31ext claude; do
  c=${PIN[$w]}
  wt="$HOME/Development/worktrees/paper-a-pin-$c"
  [ -d "$wt" ] || git -C "$REPO" worktree add --detach "$wt" "$c" >/dev/null
  [ "$(git -C "$wt" rev-parse --short=7 HEAD)" = "$c" ] || { echo "pin mismatch $wt"; exit 1; }
  run_set "$wt/docs/plans/results/stage0" "$OUT/pin" "$w"
  echo "pin $w @ $c done"
done
run_set "$MAIN_ROOT/docs/plans/results/stage0" "$OUT/main" s0 r3 r31 r31ext claude
echo "main @ $(git -C "$MAIN_ROOT" rev-parse --short HEAD) done"

# byte comparison with the committed artifacts
ART="$MAIN_ROOT/docs/plans/results/stage0/artifacts"
for side in pin main; do
  for f in label_gates.json r3/gates-r3.json r31/gates-r31.json r31ext/gates-r31ext.json \
           claude/gates-claude.json; do
    if cmp -s "$OUT/$side/$f" "$ART/$f"; then echo "$side IDENTICAL $f"
    else echo "$side DIFFERS  $f ($(diff <("$PY" -m json.tool "$OUT/$side/$f") \
              <("$PY" -m json.tool "$ART/$f") | grep -c '^<') lines)"; fi
  done
done
