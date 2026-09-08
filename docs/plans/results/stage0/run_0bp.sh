#!/usr/bin/env bash
# Stage 0b' (prime) -- the revision-3 calibration re-run, end to end.
#
# Endpoints contacted: :9001-:9006 (SFR, <= 2 in flight each, 197 query embeddings only),
# :50052 (crossencoder, <= 4 in flight), and -- in the LAST step only -- the dev tenant's
# Elasticsearch at the URL in /rag/data/tenants/dev/config/tenant.env. Nothing else.
# GPUs 6 and 7 are untouched.
set -euo pipefail

export HF_HOME=${HF_HOME:-/rag/cache}
export PYTHONPATH=${PYTHONPATH:-/home/wilke/Development/ragstack/python}
export STAGE0_HELPERS=${STAGE0_HELPERS:-/home/wilke/Development/worktrees/phase0-rescue/phase0}
PY=${PY:-/rag/envs/ragstack/bin/python3}
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN=${RUN:-"$HERE/../../../../run"}
mkdir -p "$RUN"
cd "$HERE"

step () {                       # step <name> <script> [args...]
  local name=$1; shift
  echo "== $name =="
  nohup "$PY" "$@" > "$RUN/$name.log" 2>&1 &
  echo $! > "$RUN/$name.pid"
  wait "$(cat "$RUN/$name.pid")"      # poll by the recorded pid; never by name
  tail -5 "$RUN/$name.log"
}

step retrieve  s0b_retrieve.py     # pools: 6 arms x 3 modes x 197 queries, reranked
step gold      s0b_gold.py         # CDS graded/core/unit gold + pointed construction gold
step pack      s0b_pack.py         # 11 scoring arms x 3 budgets in SFR tokens
step score     s0b_score.py        # ERET + the three EPACKs, per document
step checks    s0b_checks.py       # plumbing reproduction + the SS7.6 manipulation checks
step stats     s0b_stats.py        # gates, sigma_d, joint power, mode x size, guards
step contexts  s0b_contexts.py     # the packed contexts the synthesis stage consumes
step es        s0b_es.py           # SS3.4 concordance, dev tenant only, index deleted

echo "== artifacts =="
ls -la "$HERE/artifacts/stage0b-prime/"
