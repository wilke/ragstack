#!/bin/bash
# $1 = entity name (species|gene|chemical|disease)
e=$1
D=/rag/data/pubtator3
zcat $D/${e}2pubtator3.gz | mawk -F'\t' -v OUT=$D/${e}_bypmid.tsv -v SUM=$D/${e}_summary.txt '
 FNR==NR { keep[$1]=1; next }
 {
   rows++
   hasid = ($3 != "-" && $3 != "")
   if (hasid) rows_id++
   if ($1 != prev) { gdistinct++; prev=$1 }
   if ($1 in keep) {
     krows++; if (hasid) krows_id++
     cnt[$1]++; if (hasid) cid[$1]++
   }
 }
 END {
   n=0
   for (p in cnt) { print p "\t" cnt[p] "\t" (p in cid ? cid[p] : 0) > OUT; n++ }
   print "entity_rows\t" rows > SUM
   print "entity_rows_with_id\t" rows_id > SUM
   print "global_pmid_groups(uniq-adjacent)\t" gdistinct > SUM
   print "corpus_rows\t" krows > SUM
   print "corpus_rows_with_id\t" krows_id > SUM
   print "corpus_pmids_present\t" n > SUM
 }' $D/corpus_pmids.txt -
echo "done $e"
