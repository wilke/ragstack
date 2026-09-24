#!/bin/bash
cd "<scratch>"
for tag in run1 run2; do
  echo "=== $tag start $(date -u +%FT%TZ)"
  HF_HUB_OFFLINE=1 HF_HOME=/rag/cache apptainer exec --bind /rag /scout/containers/ragstack-hackathon/ragstack-worker.sif \
    python3 compare_corpus.py "<scratch>/batch-00000.jsonl" "<scratch>/offline" --tag $tag \
    --url http://localhost:9005 --url http://localhost:9006 2>&1 | grep -v -iE "^\[transformers\]"
  echo "=== $tag end $(date -u +%FT%TZ) rc=${PIPESTATUS[0]}"
done
echo "=== ALL DONE"
