#!/bin/bash
# Glossary benchmark: attach ONE arm's glossary file to ONE bench index (dry run -> apply -> census)
# and write the swap record the found@10 runner verifies before it measures (found10.py --swap-record).
#
# Committed form of round 1's scratch swap.sh. Nothing defaults: index, ES URL, backfill command and
# output directory are arguments, and the index must be a bench copy.
#
#   swap.sh --arm ARM --glossary-file FILE --index INDEX --es-url URL \
#           --backfill "CMD" --record-dir DIR [--python PY] [-- EXTRA BACKFILL ARGS...]
#
#   --backfill   the backfill command up to its own arguments, e.g.
#                "apptainer exec --bind /data IMAGE python /opt/ragstack/scripts/backfill_symbol_glossary.py"
#                or "python python/scripts/backfill_symbol_glossary.py"
#   EXTRA        passed to both the dry run and the apply, e.g. --replace-llm, --shuffle-rendered SEED
#                (the shuffle seed is recorded in the swap record; it is what tells a shuffled arm apart)
#
# Guards (kept from round 1): the index name must match ragstack_lib_bench_* (never a served
# collection's index, never an alias); --apply always carries --confirm-index; every apply is
# preceded by its dry run and a stamp census and followed by the same census; a failed step stops.
# The census is one read-only `terms` aggregation on metadata.symbol_glossary_version.
# Output (mode 0600): DIR/<arm>.{dryrun,apply}.{json,err}, DIR/<arm>.swap.json, DIR/LOG.md lines.
set -euo pipefail
umask 077

arm= gfile= idx= es= backfill= rdir= py=python3
extra=()
while [ $# -gt 0 ]; do
  case "$1" in
    --arm) arm=$2; shift 2;;
    --glossary-file) gfile=$2; shift 2;;
    --index) idx=$2; shift 2;;
    --es-url) es=$2; shift 2;;
    --backfill) backfill=$2; shift 2;;
    --record-dir) rdir=$2; shift 2;;
    --python) py=$2; shift 2;;
    --) shift; extra=("$@"); break;;
    *) echo "unknown argument $1" >&2; exit 2;;
  esac
done
[ -n "$arm" ] || { echo "missing --arm" >&2; exit 2; }
[ -n "$gfile" ] || { echo "missing --glossary-file" >&2; exit 2; }
[ -n "$idx" ] || { echo "missing --index" >&2; exit 2; }
[ -n "$es" ] || { echo "missing --es-url" >&2; exit 2; }
[ -n "$backfill" ] || { echo "missing --backfill" >&2; exit 2; }
[ -n "$rdir" ] || { echo "missing --record-dir" >&2; exit 2; }
case "$idx" in ragstack_lib_bench_*) ;; *) echo "REFUSE: $idx is not a bench index (ragstack_lib_bench_*)" >&2; exit 9;; esac
case "$arm" in *[!A-Za-z0-9._#-]*) echo "REFUSE: arm name $arm" >&2; exit 2;; esac
[ -s "$gfile" ] || { echo "no glossary file $gfile" >&2; exit 2; }
mkdir -p "$rdir"
shuffle=null
for ((i = 0; i < ${#extra[@]}; i++)); do
  if [ "${extra[$i]}" = --shuffle-rendered ]; then shuffle="\"${extra[$((i + 1))]}\""; fi
done

ts() { date -u +%FT%TZ; }
log() { echo "$(ts) $*" | tee -a "$rdir/LOG.md"; }
census() {
  curl -sS -f -m 60 -H 'Content-Type: application/json' "${es%/}/$idx/_search" \
    -d '{"size":0,"track_total_hits":true,"aggs":{"v":{"terms":{"field":"metadata.symbol_glossary_version","size":100,"missing":"<none>"}}}}' \
  | "$py" -c "import json,sys,time; d=json.load(sys.stdin); print(json.dumps({'utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'total': d['hits']['total']['value'], 'stamps': {b['key']: b['doc_count'] for b in d['aggregations']['v']['buckets']}}))"
}

start=$(ts)
gsha=$(sha256sum "$gfile" | cut -d' ' -f1)
log "SWAP $arm start index $idx file sha256 $gsha extra ${extra[*]:-}"
before=$(census); log "census-before $before"
# shellcheck disable=SC2086  # $backfill is a command line by design
$backfill --es-url "$es" --index "$idx" --glossary-file "$gfile" "${extra[@]}" \
  > "$rdir/$arm.dryrun.json" 2> "$rdir/$arm.dryrun.err" || { log "DRYRUN FAILED $arm"; exit 3; }
log "dryrun $("$py" -c "import json,sys; d=json.load(open(sys.argv[1])); print({k: d.get(k) for k in ('dry_run','docs','chunks','chunks_with_glossary','chunks_to_update','chunks_current','chunks_llm_replaced','reassembly_gaps','reassembly_length_mismatches')})" "$rdir/$arm.dryrun.json")"
# shellcheck disable=SC2086
$backfill --es-url "$es" --index "$idx" --glossary-file "$gfile" --apply --confirm-index "$idx" "${extra[@]}" \
  > "$rdir/$arm.apply.json" 2> "$rdir/$arm.apply.err" || { log "APPLY FAILED $arm"; exit 4; }
log "apply $("$py" -c "import json,sys; d=json.load(open(sys.argv[1])); print({k: d.get(k) for k in ('dry_run','docs','chunks','chunks_with_glossary','chunks_written','chunks_to_update','chunks_current','errors','failed')})" "$rdir/$arm.apply.json")"
sleep 2
after=$(census); log "census-after $after"
end=$(ts)

"$py" - "$rdir/$arm.swap.json" "$arm" "$idx" "$gfile" "$gsha" "$shuffle" "$start" "$end" \
  "$rdir/$arm.dryrun.json" "$rdir/$arm.apply.json" "$before" "$after" "${extra[@]}" <<'PY'
import json, os, sys
dst, arm, idx, gfile, gsha, shuffle, start, end, dry, app, before, after, *extra = sys.argv[1:]
rec = {"schema": "glossbench-swap/1", "arm": arm, "index": idx,
       "glossary_file": {"path": gfile, "sha256": gsha}, "shuffle_seed": json.loads(shuffle),
       "backfill_extra_args": extra, "utc_start": start, "utc_end": end,
       "census_before": json.loads(before), "dryrun": json.load(open(dry)), "apply": json.load(open(app)),
       "census_after": json.loads(after)}
fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as fh:
    json.dump(rec, fh, indent=1)
print(dst)
PY
log "SWAP $arm complete record $rdir/$arm.swap.json"
