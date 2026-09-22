#!/bin/bash
# Sequential download of PubTator3 bulk files. One at a time, descriptive UA.
set -u
D=/rag/data/pubtator3
UA="RAGStack-corpus-coverage-study/0.1 (contact: awilke1972@gmail.com; one-off corpus coverage measurement)"
BASE=https://ftp.ncbi.nlm.nih.gov/pub/lu/PubTator3
for f in README.txt species2pubtator3.gz gene2pubtator3.gz relation2pubtator3.gz chemical2pubtator3.gz disease2pubtator3.gz; do
  echo "=== $(date -Is) START $f"
  curl -sS --fail --retry 3 --retry-delay 10 -C - -A "$UA" -o "$D/$f" "$BASE/$f"
  rc=$?
  echo "=== $(date -Is) DONE $f rc=$rc size=$(stat -c%s "$D/$f" 2>/dev/null)"
  touch "$D/.done_$f"
done
echo "=== ALL DONE $(date -Is)"
