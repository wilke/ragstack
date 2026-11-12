# Evidence dispersion and question type: are questions with evidence across sections usually composite?

**Date:** 2026-11-11 · **Record:** `docs/papers/bib-inbox/15-evidence-dispersion-question-type.md`
**Assumption tested:** questions whose answer evidence spans more than one document section are usually composite, multi-hop or complex rather than simple factoid questions.

Status key, as in `bibliography.md`: **verified** means title, authors, venue and year were confirmed. **read** means the full text was retrieved. **PDF-checked** means the specific claim was found in the PDF text.

## Method

1. **Three areas surveyed at the same time.** Each was covered by one Argo Opus agent (uncommitted working reports, summarised here):
   - multi-hop and compositional QA;
   - long-document and scientific/biomedical QA;
   - RAG, chunking and multi-evidence benchmarks.
2. **Metadata** was checked through the arXiv API, OpenAlex, the ACL Anthology and PMLR.
3. **Full texts** were read through ar5iv or arXiv HTML using WebFetch, which summarises pages. None of the agents could read PDFs. The summariser garbled at least two tables (LongDocURL Table 3 and MMLongBench-Doc Table 3), and both had to be re-extracted.
4. **PDF verification, done separately.** The 11 claims that the verdict rests on were checked against the PDFs (pymupdf text). All 11 were confirmed, with precision notes. Those notes are authoritative where they differ from a survey file, and they are applied throughout this record.
5. **Derived figures** are our own arithmetic on published tables and are marked *derived*.

## Consolidated verdict

1. **Not supported as stated.** No paper, in any domain, reports evidence spread at the *section* level broken down by question type.
2. Spread evidence is common for questions that were not written to be composite. QASPER: 55.5% of answerable questions with text-only evidence have multi-paragraph evidence. PeerQA: 30% have evidence in more than one non-consecutive location.
3. The only dataset that crosses evidence spread with answer type is TAT-QA. There, 35.3% of table-plus-text questions are single-span extractions *(derived)*. The table-plus-text rate is about the same for single-span answers (25.8%) and arithmetic answers (29.8%).
4. Composite questions are often local. Only 27% of sampled HotpotQA bridge questions truly need multi-hop reasoning. In TAT-QA, 68.2% of arithmetic answers use the table alone *(derived)*.
5. Evidence in several places is often *redundant*, not *composite* (PeerQA's authors; QASPER's rule to prefer earlier paragraphs). Where an explanation is offered, the type it points to is enumeration or aggregation, not multi-hop (Buchmann 2024).
6. A weaker claim does have support: spread evidence goes with *difficulty*. Examples are MMLongBench-Doc cross-page versus single-page, and LURCH's negative correlations with performance. Difficulty is not composition, though, and LongDocURL partly contradicts the difficulty link.
7. Whether questions were written with sight of the text is a confound to control for. Writers who could not see the text produced more spread evidence (NewsQA, TriviaQA, QASPER, IIRC).
8. In practice, measure this rather than assume it. QASPER's release makes the measurement possible (see Next steps).

## Consolidated contradicting evidence

PDF-checked claims are marked **[PDF]**. Everything else comes from the survey reads only.

**Simple questions with spread evidence**
- **QASPER [PDF]:** 55.5% multi-paragraph, from questions written using only the title and abstract (p3). It has no breakdown by answer type, and the unit is the paragraph, not the section.
- **PeerQA [PDF]:** 30% non-consecutive evidence (p5). The authors found no consistent difference between questions with single and multiple evidence chunks (appendix, p19). They also write that the separate locations can hold similar information, so one chunk may be enough to answer (p19, paraphrased).
- **TAT-QA [PDF, Table 2 p4]:**
  - 1,842 of 7,139 single-span answers (25.8%) need both table and text;
  - 35.3% of the 5,219 table-plus-text questions are single-span *(derived)*;
  - "usually complex" holds only if multi-span lists count as complex (64.7%), and fails if only arithmetic and counting do (44.8%) *(derived)*.
- **QuALITY [PDF, p4]:** 73.9% of EASY and 81.3% of HARD questions need "at least a long paragraph or two".

**Composite questions with local evidence**
- **Min et al. 2019 [PDF]:** 27% of 100 bridge questions need multi-hop reasoning (p3; Table 2, p4); for comparison questions the figure is 45% (Table 3, p4). A single-paragraph BERT reaches 67.08 F1 (distractor dev).
- **WikiHop [PDF, p7]:** among samples judged follows/likely, 44.1% needed a single document. This is *not* 44.1% of all answerable samples, as the multi-hop survey stated. The authors add that many "single" cases only hint at the answer, which annotators completed with background knowledge.
- **FinQA [PDF, p5]:** 62.43% of questions are table-only, and 40.90% of programs have more than one step. So at least 26.75% of all questions are multi-step yet single-source *(derived)*.
- **TAT-QA [PDF]:** 4,747 of 6,964 arithmetic answers (68.2%) are table-only *(derived)*.

**Evidence count tracks the number of facts, not reasoning depth**
- **Buchmann 2024 [PDF]:** evidence segments per instance (Table 1, p4) are QASPER 1.7, NQ 1, Evidence Inference 1.1 and ContractNLI 1.5. WiCE's 3.7 is at sentence level and cannot be compared. The authors say WiCE and QASPER instances "often contain multiple facts (e.g. an enumeration or multiple subclaims)" (p9). That is their interpretation, not a coded measurement.
- **LURCH [PDF]:** "Only 11% of instances have all evidence in a single contiguous block" (p6). Scope and dispersion have r = 0.55 (0.45 after centring), which is the strongest pairwise association reported (appendix, p19). Dispersion–depth is r = 0.10 (0.02 within-task centred; Fig. 9, p20), so dispersion tracks scope, not depth. The benchmark prioritises tasks involving multi-hop reasoning, comparison, aggregation or evidence integration over single-fact retrieval (p4), so 11% is not a natural rate.

**Constructed cross-section questions**
- **GraLC-RAG [PDF]:** its 2,033 cross-section questions are synthetic and built from 5 templates over 2,359 PMC articles in IMRaD structure. Content-similarity retrievers "always retrieve from a single section" yet score the best MRR. That is consistent with these questions being answerable locally, although the authors read it the other way.

**On the difficulty side (supports only the weaker claim)**
- **MMLongBench-Doc [PDF]:** GPT-4o scores 54.5 single-page against 41.5 cross-page (Table 3, p7). The cross-page share of 365 out of 1,082 questions (33.7%) was engineered.
- **LongDocURL (not PDF-checked):** multi-page accuracy is at least single-page accuracy for several models, and the authors attribute this to redundancy.

## Surveyed but not PDF-checked

Each line gives the claim as the survey reported it. **None of these claims has been PDF-checked.** Check them before citing.

- `yang-2018-hotpotqa`: about 6% of a 100-question sample can be answered from one gold paragraph. *Not PDF-checked.*
- `chen-durrett-2019`: a sentence-factored model solves much of WikiHop and HotpotQA. *Not PDF-checked.*
- `trivedi-2020-dire`: only 18 of XLNet's 72 F1 on HotpotQA comes from multifact reasoning (abstract). *Not PDF-checked.*
- `trivedi-2022-musique`: the disconnection filter kept only 26.5% of tail questions. *Not PDF-checked.*
- `ho-2020-2wikimultihopqa`: the number of gold paragraphs is fixed by template, so spread and composition coincide. *Not PDF-checked.*
- `geva-2021-strategyqa`: about 2.93 decomposition steps against about 2.33 evidence paragraphs; paragraph Jaccard is 0.43. *Not PDF-checked.*
- `ferguson-2020-iirc`: 70% of questions need one linked document; 61% need no discrete reasoning. *Not PDF-checked.*
- `dua-2019-drop`: about 2.18 spans, on average 26 words apart, within a single paragraph. *Not PDF-checked.*
- `dasigi-2019-quoref`: 78% of analysed questions need coreference resolution. *Not PDF-checked.*
- `joshi-2017-triviaqa`: 40% of Wikipedia questions and 35% of web questions need multiple sentences. *Not PDF-checked.*
- `trischler-2017-newsqa`: synthesis questions are 20.7% of NewsQA against 11.9% of SQuAD. *Not PDF-checked.*
- `sugawara-2018-easier`: multiple-sentence reasoning is more frequent in hard subsets in 10 of 12 datasets. *Not PDF-checked.*
- `krishna-2025-frames`: 2–15 Wikipedia articles per question, by design. *Not PDF-checked.*
- `tang-2024-multihop-rag`: 2–4 evidence pieces per query, by construction. *Not PDF-checked.*
- `wang-2024-loong`: on Gemini-1.5-Pro, Spotlight Locating is easiest and Chain of Reasoning scores above Clustering and Comparison. *Not PDF-checked.*
- `zhu-2020-mashqa`: answers come from non-consecutive spans (abstract only). *Not PDF-checked.*
- `reddy-2024-docfinqa`: gold chunks inherit FinQA's single-page localisation. *Not PDF-checked.*
- `singh-2024-scidqa`: 10–14% of questions each need tables, the appendix, or equations and symbols. *Not PDF-checked.*
- `li-2024-m3sciqa`: questions are multi-document and composite by construction. *Not PDF-checked.*
- `sun-2022-conditionalqa`: says conditions sit in other sections, without quantifying it. *Not PDF-checked.*
- `jin-2019-pubmedqa`: comparative reasoning inside a single abstract (57.5% inter-group). *Not PDF-checked.*
- `krithara-2023-bioasq`: snippet counts depend on the "sufficient set" annotation policy. *Not PDF-checked.*
- `lehman-2019-evidence-inference`: the evidence span is in the abstract only 40.5% of the time. *Not PDF-checked.*
- `li-2023-loogle`: GPT-4-32k scores 71.52 on short-dependency against 54.09 on long-dependency questions. *Not PDF-checked.*
- `goldman-2024-long-context`: dispersion and scope are treated as orthogonal axes (a conceptual paper). *Not PDF-checked.*
- `kocisky-2018-narrativeqa`: says most questions need several paragraphs, without measuring it. *Not PDF-checked.*
- `xu-2022-fairytaleqa`: section-level evidence labels are released, but no statistics are reported. *Not PDF-checked.*
- `skarlinski-2024-paperqa2`: LitQA2 answers are in the body of the paper by construction. *Not PDF-checked.*
- `asai-2024-openscholar`: ScholarQABench covers synthesis across papers (arXiv entry only). *Not PDF-checked.*
- `deng-2024-longdocurl`: GPT-4o scores 65.8 single-page against 63.2 multi-page; other models score higher on multi-page. *Not PDF-checked.*
- `saadfalcon-2024-pdftriage`: the 1.1% share of cross-page tasks reflects stratified sampling. *Not PDF-checked.*
- `li-2024-selfroute`: 63% of RAG and long-context predictions are identical; RAG failures are mostly multi-step questions. *Not PDF-checked.*
- `li-2025-lara`: RAG's gap to long context is largest on Comparison (−15.22 points at 32k). *Not PDF-checked.*
- `li-2025-lcvsrag`: long context does better on Who/Where/Which factoid questions. *Not PDF-checked.*
- `han-2025-ragvsgraphrag`: plain RAG on MultiHop-RAG scores 92.16 on inference against 30.70 on temporal queries. *Not PDF-checked.*
- `edge-2024-graphrag`: global questions are spread by definition; evidence spread is not measured. *Not PDF-checked.*
- `gunther-2024-latechunking`: a factoid's referent can sit in an earlier chunk (mechanism only). *Not PDF-checked.*
- `anthropic-2024-contextualretrieval`: same mechanism, in a blog post that is not peer-reviewed. *Not PDF-checked.*
- `bhat-2025-chunksize`: smaller chunks suit fact-based datasets and larger chunks suit broad ones (dataset level only). *Not PDF-checked.*
- `jeong-2024-adaptiverag`: complexity labels come from which dataset a question was drawn from; the classifier reaches 54.52%. *Not PDF-checked.*
- `wang-2024-novelqa`: GPT-4 scores 37.58 on detail questions against 32.83 on multi-hop. *Not PDF-checked.*
- `islam-2023-financebench`: some ratio questions need two financial statements; counts were not found. *Not PDF-checked.*
- Metadata only, no claim used: `tang-2021-subquestions`, `jiang-bansal-2019`, `khashabi-2018-multirc`, `rajpurkar-2016-squad`, `deyoung-2020-ei2`, `bai-2024-longbench`, LongBench v2, RAPTOR, Dense X, CRAG, LumberChunker, Meta-Chunking, Merola & Singh, and Koutsiaris. *Not PDF-checked.*

## COULD NOT VERIFY

1. **QASA** (Lee et al., ICML 2023, PMLR 202:19036–19052) is the **highest priority**.
   - Metadata is verified on PMLR. The paper has a taxonomy of surface, testing and deep questions, and may be the only paper that relates question depth to evidence count in scientific papers.
   - Nothing past the abstract was read. The PDFs came back as binary through WebFetch.
   - Searches that failed to find it: arXiv `ti:QASA AND ti:scientific` and `all:"QASA" AND all:"question answering"`; OpenAlex `title.search:QASA` and `search=QASA advanced question answering scientific articles`.
   - The GitHub README (lgresearch/QASA) has no statistics.
2. *(Resolved: LURCH dispersion–depth r = 0.10, 0.02 centred, Fig. 9 p20.)*
3. **SQuAD's share of "multiple sentence reasoning"** (recalled as about 13.6%): the ar5iv conversion and the ACL PDF both failed.
4. **MASH-QA statistics** and **MultiSpanQA** (NAACL 2022): only the abstract or the OpenAlex record was available.
5. **FinanceBench single- versus multi-statement counts:** not found. The n = 5,786 versus 5,897 mismatch is unresolved.
6. **Self-Route failure-category percentages:** they appear only in Fig. 4.
7. **Venues:**
   - MultiHop-RAG, LooGLE and NovelQA have none listed.
   - Han et al. is claimed as KDD '26 only in the v4 HTML.
   - Lehman 2019 was not cross-checked on the ACL Anthology.
8. **Dropped as unreliable extractions:**
   - per-type supporting-fact averages for 2WikiMultiHopQA;
   - per-model MMLongBench-Doc scores other than GPT-4o;
   - read through the summariser and never raw-checked: the LaRA, Bhat, FRAMES, NovelQA, MultiHop-RAG and FinQA Table 3 figures.
9. **PeerQA:** the phrase "not necessarily complementary" is at p19, Appendix E (hyphenated across a line, which defeated the first search); the paraphrase in `bibliography.md` stands.

## Searched and did not find

These are arXiv API queries unless marked otherwise.

| Query | Result |
|---|---|
| `abs:"evidence dispersion" OR "dispersed evidence" OR "scattered evidence"` | 20–25 hits across three runs; only LURCH measures dispersion, and none crosses it with question type |
| `abs:"multi-paragraph evidence" OR "cross-paragraph" AND question`; `abs:"cross-paragraph" AND abs:question` | 2 hits, both models and irrelevant |
| `abs:"single-hop" AND "multi-hop" AND "scientific papers" AND evidence` | 0 |
| `abs:"scientific papers" AND abs:"single-hop"` | 1 hit, irrelevant |
| `abs:"multi-hop" AND shortcut AND "single-hop" AND analysis` | 0; the broader `abs:shortcuts AND hop AND question AND answering` gave 22 hits and no new annotated estimate |
| `ti:"Multiple Answer Spans" OR MASH-QA OR MultiSpanQA` | 0 on arXiv; OpenAlex finds both papers, which are not on arXiv |
| `abs:"evidence" AND abs:"multiple paragraphs" AND abs:"scientific" AND abs:"question"` | 0 |
| `abs:Qasper AND abs:"multi-hop"` | 0 |
| `abs:"multi-paragraph" AND abs:"question types"` | 0 |
| `abs:"multiple sections" AND abs:"question answering" AND abs:"scientific"` | 0 |
| `abs:"full-text" AND abs:"biomedical" AND abs:"question answering" AND abs:"evidence" AND abs:"section"` | 0 |
| `abs:"number of evidence" AND abs:document AND abs:question` | 0 |
| `abs:"multi-evidence" AND abs:document` / `AND abs:question` | 11 hits, open-domain only; no natural single- versus multi-evidence share |
| `abs:Qasper AND abs:evidence` | 14 hits; only 2407.07799 (Buchmann) is relevant |
| `abs:"multiple chunks" AND abs:evidence AND abs:question` | 1 hit (2606.13550); no measurement |
| `abs:"chunk boundaries" AND abs:retrieval` | 13 hits; none measures how often evidence is split across chunks |
| `abs:"cross-chunk" OR abs:"multi-chunk" OR abs:"single-chunk"` | 30 hits; MHTS (2504.08756) produces synthetic queries only |
| `ti:decomposition AND abs:"retrieval-augmented" AND abs:"multi-hop"` | 8 hits; all take their multi-hop label from the benchmark |
| OpenAlex: evidence spread across multiple paragraphs, scientific or long-document QA, question type (3 phrasings) | Only QASPER, QAMPARI, NovelQA and surveys; no statistics broken down by type |

Caveat: the arXiv API matches quoted phrases strictly, so some of these zeros may come from the query syntax. **Not found anywhere:** a cross-tabulation of question type against the number of sections holding the evidence, for naturally occurring questions in any domain.

## Established names

- **Evidence dispersion** and **evidence scope** (Goldman et al. 2024, conceptual), plus **reasoning depth** (LURCH, operationalised). Also "localised vs dispersed evidence" and "single contiguous block" (LURCH).
- **Multi-paragraph evidence** (QASPER); **non-consecutive answer evidence** (PeerQA).
- **Cross-page / multi-page** versus single-page (MMLongBench-Doc, LongDocURL, PDFTriage); **cross-element** (LongDocURL); **cross-section questions** (GraLC-RAG).
- **Long- versus short-dependency** (LooGLE); **context needed** ratings (QuALITY).
- **Multiple-sentence reasoning** (Sugawara, TriviaQA); **synthesis** (NewsQA).
- **Hybrid tabular-textual evidence** (TAT-QA, FinQA); **multi-statement** questions (FinanceBench).
- **Evidence segments per instance**, with "multiple facts / enumeration / subclaims" (Buchmann).
- **Multi-hop**, **multifact**, **bridge vs comparison**, **compositional**, **connected / disconnected reasoning** (MuSiQue, DiRe).
- **Single-hop shortcuts**, **redundant evidence**, **weak distractors** (Min 2019).
- **Spotlight Locating** versus Comparison, Clustering and Chain of Reasoning (Loong); **global / sensemaking queries** (GraphRAG); **query complexity** (Adaptive-RAG); **context loss at chunk boundaries** (late chunking).
- Unverified: **multi-evidence QA** (Zhong et al. 2019, arXiv 1901.00603) and **multi-span** (MultiSpanQA).

**Recommendation:**
- Use **"evidence dispersion"** for the variable. Make it section-level with a qualifier ("section-level evidence dispersion"), because no published term exists for spread across sections.
- Use **"composite / multi-hop"** for the property of the question, and measure the two separately.
- **Never use "multi-span" for evidence location.** In TAT-QA ("Spans") and MultiSpanQA it means several *answer* spans, which can sit inside one passage.

## Next steps

1. **Measure section-level dispersion by answer type from QASPER's release.** The release maps paragraphs to sections and carries answer types and evidence from several annotators. Produce three numbers:
   - the share of answers whose evidence crosses *sections*, not just paragraphs;
   - that share broken down by extractive, abstractive and yes/no answers;
   - how consistently annotators agree on the single- versus multi-section label (the human Evidence-F1 lower bound is 71.62).
   Commit the artifact before any sentence relies on the result.
2. **Read QASA** (PMLR PDF, with pymupdf). Check whether it relates question type to evidence count or evidence location.
3. **Chase the forward citation graph** (OpenAlex `cited_by`) from QASPER, PeerQA, LURCH, Min 2019 and TAT-QA. None of the three surveys did this.
4. **PDF-check the counter-evidence before citing it.** Every entry under "Surveyed but not PDF-checked" stays out of `bibliography.md` and `claims.json` until its claim has been found in the PDF. Start with LongDocURL, IIRC, DROP, StrategyQA, Evidence Inference and the LURCH claim that HotpotQA evidence is localised.

## Note added 2026-11-12

The QASPER 55.5% figure cited above (verdict item 2, the QASPER line under the evidence) does not reproduce from the released data: the paper's definition gives about 31% on every released split and version. See the QASPER entry in `bibliography.md` and `docs/plans/results/qasper-dispersion-2026-11/`. The survey's verdict does not depend on the exact figure: about 31% multi-paragraph and 10% cross-section are still common among questions not written to be composite.
