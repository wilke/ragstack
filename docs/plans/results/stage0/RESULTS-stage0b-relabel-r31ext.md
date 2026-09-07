# RESULTS — the r3.1 extension: Scout ×20, Qwen ×10, and what a *graded* gold is worth

**Specification:** [`../design/SPEC-confirmation-run-r3.md`](../design/SPEC-confirmation-run-r3.md)
§3.7 item 6 and **§10 item 4**, whose three options the owner is pursuing in full. This run
supplies the measurement two of them turn on: option (a)'s **graded per-sentence support** —
how reliable is it, and how many readings does it take? — and option (c)'s implicit claim that
**more presentations converge** on a canonical span set.

**Extends** [`RESULTS-stage0b-relabel-r31.md`](RESULTS-stage0b-relabel-r31.md) (#507). Same 308
development pairs, same two judges, same prompt revision 3.1, same seed formula. #507's five
presentations per judge are **read, not regenerated**, and its committed artifacts are untouched.

**Scope note, stated first because it bounds everything below.** No human read was performed.
**No κ(human–human) or κ(judge–human) appears anywhere in this document**, the R-dev pairs were
not read, and r3 §3.7 item 5's enumeration recall is **absent, not zero**. The human half stays
`PENDING-HUMAN`. Nothing here is a decision about §10 item 4; it is the measurement that
decision needs.

PLACEHOLDER-VERDICT

---

## 0. Provenance

PLACEHOLDER-PROVENANCE

---

## 1. The three pre-registered predictions, scored

PLACEHOLDER-PREDICTIONS

---

## 2. The gate table at depth

PLACEHOLDER-GATES

---

## 3. Saturation

PLACEHOLDER-SATURATION

---

## 4. The reliability of graded support

PLACEHOLDER-RELIABILITY

---

## 5. Cross-judge

PLACEHOLDER-CROSS

---

## 6. What this licenses for §10 item 4 — as a measurement, not a decision

PLACEHOLDER-LICENSE

---

## 7. Deviations

PLACEHOLDER-DEVIATIONS

---

## 8. Cost

PLACEHOLDER-COST

---

## 9. Reproduction

```bash
export HF_HOME=/rag/cache PYTHONPATH=/home/wilke/Development/ragstack/python
export STAGE0_HELPERS=/home/wilke/Development/worktrees/phase0-rescue/phase0
PY=/rag/envs/ragstack/bin/python3
cd docs/plans/results/stage0

# 0. the pre-registration, from the COMMITTED r3.1 labels only — no endpoint
$PY s0_labelgates_r31ext.py --prefit

# 1. seed work/r31ext with #507's records, after asserting the prompt has not moved
#    (the assertions are in §0 of this document; the copy is byte-for-byte)
cp artifacts/r31/labels-r31-{scout,qwen}.jsonl "$STAGE0_BIG/work/r31ext/"

# 2. smoke, then the extension: k = 5..19 for scout, k = 5..9 for qwen
$PY s0_label_r31.py --judge scout --limit 20 --presentations 5:6 --tag smoke --workdir r31ext
$PY s0_label_r31.py --judge qwen  --limit 20 --presentations 5:6 --tag smoke --workdir r31ext
$PY s0_label_r31.py --judge scout --presentations 0:20 --workdir r31ext
$PY s0_label_r31.py --judge qwen  --presentations 0:10 --workdir r31ext
$PY s0_label_r31.py --merge-manifest --workdir r31ext

# 3. the gate table, the curves, the reliability and the scored predictions
$PY s0_labelgates_r31ext.py                 # artifacts/r31ext/gates-r31ext.{json,md}
```

`s0_label_r31.py` is resumable and idempotent on `(topic, docno, presentation)`, which is
exactly what makes the extension possible: `--presentations 0:20` over a file that already
holds k = 0..4 runs **only** k = 5..19 and leaves the existing records byte-identical. Work
goes to `$STAGE0_BIG/work/r31ext/` and nowhere else.
