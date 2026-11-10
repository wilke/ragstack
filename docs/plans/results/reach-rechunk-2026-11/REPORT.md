# Report: does the adopted chunking cost reach?

Confirmatory comparison, run 2026-11-10: fixed 512-token windows (W) against the chunking the project is adopting
(S), on the alias study's 1,091 questions. This is the reader's version: hypothesis, short results summary, how it
was tested, results, conclusion. The record it reads from is [RESULTS.md](RESULTS.md) (the decision sentences,
every table, the gates, the procedural notes) and [`artifacts/reach-report.json`](artifacts/reach-report.json). How
the test data was made is in [TEST-DATA.md](TEST-DATA.md). Deviations are in [DEVIATIONS.md](DEVIATIONS.md).
Unlike those record files, this report is revised in place, as a paper is, and must read as finished whenever it
is read.

## 1. Hypothesis

**The question.** The alias study retrieved with fixed 512-token windows, `fixed_token/512/0`, cut blind to
sentences and sections (**W**). The project is adopting a chunking that cuts at sentence boundaries inside
sections: `sentence/512/0` in tokens, a 512-token target with a 640-token ceiling, section cuts, pieces of at
least 128 tokens (**S**). Does retrieval under S reach the answer as often as retrieval under W?

**Why it was asked.** The confirmatory chunk-boundary test found that the frozen judge withdraws *answers* from
S chunks far more often than from the W windows, when it reads one passage at a time
([report](../alias-boundary-confirm-2026-11/REPORT.md)). That test judged passages and did not retrieve. Rechunk
stage 1 retrieved, but only for pairs chosen from W's retrieved windows. Neither says whether a full retrieval
run under S finds the answer less often.

**The hypothesis as tested** (one-sided; PREREG §1):
- **HQ:** quote@10(S) − quote@10(W) > −3 points. That is non-inferiority with a margin δ of 3 points. If it
  holds, superiority (S > W) is tested next, hierarchically, with no extra α.
- **quote@10** holds for a question when its top 10 retrieved chunks, together, cover at least 90 % of the
  characters of the question's gold quote. It needs no judge, and it scores both chunkings with one function.

A second measure, **judged found@10 (J)**, is reported descriptively: quote@10, or any top-10 chunk the frozen
judge labels *answers*. It was made descriptive before any S outcome existed, for two reasons. For 748 of the
1,091 questions, new S chunks cannot be judged by the frozen harness (they are *orphans*), and "any chunk
answers" was already known to saturate on these questions.

The test was frozen before the S retrieval ran: PREREG sha256 `1e3791ed…4458`, signed tag
`prereg-reach-rechunk-2026-11` at `f78af6ee`.

## 2. Results summary

**Superior.** No validity gate fired.
- **Rates.** Under the default retriever, pooled over the four corpus collections, quote@10 held on **91.5 %** of
  the paired units under S against **90.3 %** under W (`families.Q.counts`).
- **What one point means here.** A unit is one question in one collection; there are 4,356 of them (1,089
  questions × 4). One point is 1 % of those 4,356 units, about 44 units. W reached the quote on 3,935 units,
  S on 3,985. S reached it on 112 units where W did not, and W on 62 where S did not: a net 50 units, **+1.1
  points**.
- **Test.** Non-inferiority at δ = 3 holds (exact one-sided sign-flip over the 408 papers, p printed as 0.0, so
  below 5 × 10⁻⁷). Superiority holds too, p = 0.0079.
- **Size caveat.** The units that differ were only 4.0 % of all units. At that low a discordance, the test's size
  is only asymptotically α. Its simulated size is 0.0545 at 10 % discordance and 0.0615 at 5 %, up to 0.0766
  over the grid at 5 %; below 5 % it was not simulated.
- **J (descriptive).** Judged found@10 sits near 98 % on both sides: 98.1 % under S against 98.0 % under W
  (+0.1 points). It is saturated, so it can show little difference either way.

The result is about **quote@10 reach under the default retriever**. It says nothing directly about answer quality.

## 3. How it was tested

- **Questions.** The alias study's 1,091 accepted questions on 408 papers. Each carries a quote of about 160
  characters from its gold window. **1,089** are Q-eligible: the quote is verbatim in its gold window after
  whitespace normalisation, and the S chunks of the gold document can reach it. Full chain:
  [TEST-DATA.md](TEST-DATA.md).
- **Collections.** The same four corpus collections on both sides: the base corpus alone, and the base corpus
  plus one of three sets of added papers (neighbour, random-1, random-2).
  - **W:** the alias study's committed retrieval runs (nothing new was run).
  - **S:** the four collections built for rechunk stage 1 (452,729 chunks), retrieved anew for all 1,091
    questions (13,092 calls).
- **Retrievers.** Top 50 requested, top 10 read, on each side, with three retrievers:
  - **default:** the served BM25 + dense hybrid, reranked;
  - **norerank:** the same hybrid without the reranker;
  - **BM25** alone.

  The server's configuration hash was the same for both runs.
- **Primary cell.** The default retriever, pooled over the four collections. Each question contributes one paired
  difference (S − W, in −1, 0, +1) per collection, and the questions are clustered by paper.
- **Test.** An exact one-sided sign-flip over papers at α = 0.05: first non-inferiority against the 3-point
  margin, then superiority. Every other cell is secondary and not tested.
- **J.** Lists without a quote hit were judged on demand with the alias study's frozen judge: two Qwen models,
  a Gemini 4 Argon tiebreak, and a Claude Opus 5.5 drift reference that sets no label.
  - Existing labels were reused where valid.
  - Orphan pairs could not be judged, and count as not answering.
  - A re-judge of a 5 % sample of W's labels checks for judge drift.
- **Gates on Q.** Any of these would have made the result *not evaluable*:
  - a failed or incomplete retrieval leg;
  - an S run whose settings differ from W's;
  - a collection whose census differs from its build record;
  - an input check (record and eligible counts, chunk texts, the frozen input hashes).

  None fired.
- **Power.** At the sizing assumptions, δ = 3 had power 0.97 at 10 % discordance and 1.00 at 5 %
  ([`power.json`](power.json) `recommendation`; PREREG §5). The observed discordance was 4.0 %.

## 4. Results

**The primary cell** (tested; `families.Q`):

| | W reaches the quote | W does not |
|---|---|---|
| **S reaches the quote** | 3,873 | **112** |
| **S does not** | **62** | 309 |

| quantity | value |
|---|---|
| units / questions / papers | 4,356 / 1,089 / 408 |
| quote@10, S / W | 91.5 % / 90.3 % |
| difference | +1.1 points (+1.148) |
| discordance | 4.0 % |
| non-inferiority at δ = 3 | holds, p < 5 × 10⁻⁷ (printed 0.0) |
| superiority | holds, p = 0.0079 |
| gates | none fired |

**Other retrievers and collections** (secondary, not tested; `families.Q.secondary`):

| cell | S | W | difference (points) |
|---|---|---|---|
| norerank, pooled | 81.9 % | 79.6 % | +2.3 |
| BM25, pooled | 79.7 % | 77.6 % | +2.1 |
| default, base | 94.9 % | 94.2 % | +0.6 |
| default, neighbour | 89.4 % | 87.7 % | +1.7 |
| default, random-1 | 90.8 % | 90.0 % | +0.8 |
| default, random-2 | 90.8 % | 89.4 % | +1.4 |
| default, G questions (term in a short form only) | 88.4 % | 86.7 % | +1.7 |
| default, C questions | 96.7 % | 96.4 % | +0.3 |

In every secondary cell S reaches the quote at least as often as W. These cells were not tested.

**Context per query** (descriptive): under the default retriever, the top 10 hold 4,142 tokens on average under
S against 4,777 under W (SFR-Embedding-Mistral tokenizer; `context_tokens`). S's top 10 are about 13 % shorter.

**Where the differing units sit** (descriptive; `discordant_by_gold_window_coverage_under_S`). This splits the
units by how much of the W gold window the best-overlapping S chunk holds. Where that S chunk holds less than
half the window, S reached the quote alone on 27 units and W alone on 1.

**J, judged found@10** (descriptive, not tested; `descriptive.J`):

| quantity | value |
|---|---|
| units / questions / papers | 4,364 / 1,091 / 408 |
| J, S / W | 98.1 % / 98.0 % (difference +0.1 points; 42 S only, 38 W only) |
| S pairs this cell needs without a label | 71 of 2,758 (all orphans); W 0 of 3,149 |
| favourable bound (those 71 scored *answers*) | S 98.3 %, difference +0.3 points |
| judge drift check | agreement 0.97 overall, 0.93 on W's *answers* labels (threshold 0.90): no drift caveat |

J is near its ceiling on both sides, and it cannot separate the chunkings. Its secondary cells add little:
- **norerank:** +0.2 points.
- **BM25:** −1.4 points. But 18.8 % of the S pairs BM25's J needs are unjudged orphans, and every one of them
  counts as not answering. That works against S by construction.

## 5. Conclusion

- **What is shown.** Under the default retriever, the adopted chunking reaches the gold quote in the top 10 at
  least as often as the fixed windows did. It does not lose 3 points or more, and it reaches the quote slightly
  more often: 91.5 % against 90.3 %, about 50 more units of 4,356. The test was pre-registered, and no validity
  gate fired. The caveat printed with the sentence applies: at the observed 4.0 % discordance, the test's
  nominal size is only asymptotic.
- **What is not shown.**
  - **Answer quality.** quote@10 credits the quote's text wherever it is retrieved. It does not check that a
    reader or a model would find the answer, or that the text came from the gold document.
  - **Judged reach.** Judged found@10 is saturated near 98 % and descriptive, so it neither supports nor
    contradicts the quote@10 result.
  - **Other retrievers.** The norerank and BM25 gains are secondary and were not tested.
  - **Human relevance.** The labels behind J are the frozen judge's. The alias study's judge-validity flag
    (owner–judge agreement 0.872 against 0.90) applies to them.
- **Which way the known biases point.**
  - Every quote was drawn from inside one W window, so W never needs two chunks to reach it.
  - Chance matches at chunk edges are slightly more available to W, whose windows end mid-sentence.

  Both favour W, against the hypothesis (PREREG §3, §8).
  - The W runs are older (server 1.6.22 against 1.6.25), but the server configuration hash, the retrievers and
    the depths are the same.
  - J's gateway calls ran on a second gateway (D-1). That touches J only, and the drift check found no drift
    caveat.
- **What it means in practice.** For these questions and collections, the labels the confirmatory test saw S
  chunks lose under single-passage judging do not show up as a loss of quote@10 reach. Adopting S does not cost
  quote@10 reach under the default retriever, and its top 10 carry about 13 % fewer tokens. An evaluation that
  judges single passages would still register answers moved or split across S chunks as losses. That is a
  property of single-passage judging, not of retrieval reach as measured here.
