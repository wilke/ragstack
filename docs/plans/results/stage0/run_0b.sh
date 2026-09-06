#!/usr/bin/env bash
# Stage 0b, end to end, on the already-built indexes. Every step is idempotent and
# checkpointed; re-running skips what is already on disk.
#
# Politeness and reservations enforced inside the modules, not here: SFR :9001-:9006 only
# (<=2 in flight per endpoint), mango <=4 concurrent, :50052 <=4 in flight, GPUs 6 and 7
# never touched, no store client constructed anywhere.
set -euo pipefail
cd "$(dirname "$0")"
export HF_HOME=/rag/cache
PY=/rag/envs/ragstack/bin/python
L=/rag/tmp/stage0-conf/work

step () { echo "=== $1 $(date +%H:%M:%S) ==="; }

step "1 retrieve (dev topics only, full 32.7k corpus, rerank on :50052)"
$PY s0_retrieve.py       2>&1 | grep -v "PyTorch was not found" | tee "$L/retrieve.log"

step "2 pack (A1 rule, served-generator tokenizer)"
$PY s0_pack.py           2>&1 | grep -v "PyTorch was not found" | tee "$L/pack.log"

step "3 label (Llama-4-Scout on mango:8003, <=4 concurrent)"
$PY s0_label.py          2>&1 | grep -v "PyTorch was not found" | tee "$L/label.log"

step "4 units (D3) + coverage (D4) + EUC"
$PY s0_score.py          2>&1 | grep -v "PyTorch was not found" | tee "$L/score.log"

step "5 machine label gates"
$PY s0_labelgates.py     2>&1 | grep -v "PyTorch was not found" | tee "$L/labelgates.log"

step "6 manipulation checks"
$PY s0_checks.py         2>&1 | grep -v "PyTorch was not found" | tee "$L/checks.log"

step "7 the 8.5.7 table and the power gate"
$PY s0_stats.py          2>&1 | grep -v "PyTorch was not found" | tee "$L/stats.log"

step "8 the R-dev human-read artifact (PENDING-HUMAN; no agent reads it)"
$PY s0_rdev.py           2>&1 | grep -v "PyTorch was not found" | tee "$L/rdev.log"

step "9 render the table"
$PY s0_report.py         2>&1 | grep -v "PyTorch was not found" > "$L/report.log"

step "done"
nvidia-smi --query-gpu=index,memory.used --format=csv,noheader | tail -2
