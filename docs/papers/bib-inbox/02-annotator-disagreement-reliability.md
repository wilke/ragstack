# 02 — Annotation reliability, assessor disagreement, and what it does to evaluation

Survey for the preprint. Area: how much annotators disagree, whether evaluation survives it,
and what has been proposed instead of a single correct label.

**Status vocabulary**

- **verified** — publisher-deposited metadata retrieved (Crossref DOI record, ACL Anthology BibTeX,
  arXiv API) and/or the publisher landing page fetched. Exact title, full author list, venue and year
  below are copied from what came back, not from memory.
- **read** — the PDF was downloaded and the methods/results extracted (`pymupdf`). Numbers quoted
  below with a page-level claim come from the extracted text.

Every entry below was fetched. Entries that could not be fetched are in
[§ COULD NOT VERIFY](#could-not-verify), not in the body.

**Four questions asked of every paper** (abbreviated in each entry):

- **Who disagrees** — *between* annotators, or *within* one annotator repeated (test-retest)?
- **Unit** — a document, or a span/passage inside a document? Is agreement on *location* measured
  separately from agreement on *presence*?
- **Gold** — single correct label, or graded/probabilistic?
- **Statistic** — what agreement coefficient, and is its behaviour on set- or span-valued
  annotation discussed?

---

## 1. Classical IR — how much do assessors disagree, and do system orderings survive?

### `voorhees-1998` / `voorhees-2000`

**Variations in relevance judgments and the measurement of retrieval effectiveness**
Ellen M. Voorhees.
SIGIR '98, pp. 315–323, DOI `10.1145/290941.291017`;
journal version *Information Processing & Management* 36(5):697–716, 2000, DOI `10.1016/S0306-4573(00)00010-8`.
https://doi.org/10.1145/290941.291017 · https://doi.org/10.1016/S0306-4573(00)00010-8
**verified** (Crossref, both records; note the two are the same title in two venues — cite the IP&M one
for the full treatment).

The founding result of the "disagreement doesn't matter" line: NIST had TREC-4/TREC-6 topics
re-judged by secondary assessors, found substantial disagreement on individual documents, and then
showed the *rankings of systems* computed from the different qrel sets correlate very highly. The
conclusion carried forward for twenty-five years is that Cranfield comparative evaluation is stable
under assessor variation even though absolute scores are not.

- Who disagrees: **between** assessors (different people re-judging).
- Unit: **whole document**, binary relevant/not.
- Gold: single label per document per assessor; no graded pooled object.
- Statistic: pairwise **overlap** (size of intersection over size of union of the relevant sets) for
  the judgments, **Kendall's τ** for the downstream system orderings.

**Weight** — must-cite; it is the claim our result has to be positioned against, and it is a
*document-level, between-assessor* claim, which is exactly the boundary we are pushing past.

### `parry-2025`

**Variations in Relevance Judgments and the Shelf Life of Test Collections**
Andrew Parry, Maik Fröbe, Harrisen Scells, Ferdinand Schlatt, Guglielmo Faggioli, Saber Zerhoudi,
Sean MacAvaney, Eugene Yang.
SIGIR '25, pp. 3387–3397, DOI `10.1145/3726302.3730308`; arXiv:2502.20937.
https://arxiv.org/abs/2502.20937
**verified** (Crossref + arXiv API) · **read** (arXiv PDF v2).

⚠️ **Not a Voorhees paper** — it is titled as a deliberate echo of `voorhees-2000` but the author
list is entirely different. Easy to mis-cite; we nearly did.

A reproduction of Voorhees on a *modern* collection (TREC Deep Learning 2019, short passages, 4-grade
relevance, no narratives). Re-annotated with 8 annotators. Measured inter-annotator agreement
directly: Cohen's κ **0.12–0.27** on the 4-grade scale and **0.28–0.47** binarised; pairwise overlap
**0.11–0.19** (4-grade) and **0.45–0.48** (binary); Fleiss' κ 0.17–0.28 (4-grade). Despite that,
system-order correlation across annotator groups was **τ = 0.879, ρ = 0.972, RBO = 0.888**. Their
novel claim is the other half: some neural models degrade substantially under the new qrels and some
have reached human-ranker effectiveness, so collections can "expire".

- Who disagrees: **between** assessors.
- Unit: **passage** (short documents), but judged as a whole unit — presence only, no location.
- Gold: 4-grade relevance, but a single grade per assessor; pooling is over annotator *subsets* to
  make alternative qrel sets, not into a graded score.
- Statistic: Cohen's κ, Fleiss' κ, raw overlap; τ/ρ/RBO downstream.

**Weight** — high. This is the current state of the art on "low per-item agreement, stable rankings"
and gives us fresh numbers to compare against on short passages, which is our unit's neighbourhood.

### `bailey-2008`

**Relevance assessment: are judges exchangeable and does it matter?**
Peter Bailey, Nick Craswell, Ian Soboroff, Paul Thomas, Arjen P. de Vries, Emine Yilmaz.
SIGIR '08, pp. 667–674, DOI `10.1145/1390334.1390447`.
https://doi.org/10.1145/1390334.1390447
**verified** (Crossref — note Crossref deposits the title truncated at the colon as "Relevance
assessment"; the full title with subtitle is confirmed from the NIST and Microsoft Research landing
pages and the IR Anthology record).

Introduces the gold / silver / bronze assessor taxonomy (topic originator; task expert who did not
originate the topic; neither). Agreement between the three classes is low; system scores and system
rankings differ consistently but by small amounts across the three assessment sets. The practical
reading is that judges are *not* exchangeable in the strict sense but the induced ranking error is
bounded.

- Who disagrees: **between** assessors, and structurally (by assessor class, not just by person).
- Unit: **document**.
- Gold: single label.
- Statistic: overlap / agreement rates plus ranking correlation.

**Weight** — high. The canonical citation for "who the annotator is matters, and here is how much".

### `cormack-2006`

**Statistical precision of information retrieval evaluation**
Gordon V. Cormack, Thomas R. Lynam.
SIGIR '06, pp. 533–540, DOI `10.1145/1148170.1148262`.
https://doi.org/10.1145/1148170.1148262
**verified** (Crossref).

Treats evaluation error as a measurement-precision problem and separates the sources of variance in
an effectiveness estimate (topic sampling, assessment, pooling) rather than asking only "does the
ranking flip".

- Who disagrees: **between** assessors, but framed as variance components.
- Unit: document.
- Gold: single label.
- Statistic: variance/precision estimates, not a κ.

**Weight** — medium-high for us, because variance decomposition is the right shape of argument for a
reliability coefficient; see also `urbano-2013`.

### `carterette-2010`

**The effect of assessor error on IR system evaluation**
Ben Carterette, Ian Soboroff.
SIGIR '10, pp. 539–546, DOI `10.1145/1835449.1835540`.
https://doi.org/10.1145/1835449.1835540
**verified** (Crossref).

⚠️ Title is **"assessor error"**, singular, not "assessor errors" — the plural form is a common
mis-citation.

Models assessor error as a process (over- and under-estimating relevance at different rates) and
simulates its effect on evaluation, asking which kinds of assessor are safe to use for which
decisions.

- Who disagrees: **between** assessors (and simulated error processes).
- Unit: document.
- Gold: single label, with an error model on top.
- Statistic: simulation over ranking correlation.

**Weight** — high; the standard citation for "not all disagreement is symmetric".

### `webber-2012`

**Alternative assessor disagreement and retrieval depth**
William Webber, Praveen Chandar, Ben Carterette.
CIKM '12, pp. 125–134, DOI `10.1145/2396761.2396781`.
https://doi.org/10.1145/2396761.2396781
**verified** (Crossref).

Disagreement between alternative assessors is not uniform with rank depth — it grows as you go
deeper into the ranking, which is where the marginal/ambiguous documents live.

- Who disagrees: **between** assessors.
- Unit: document, conditioned on retrieval depth.
- Gold: single label.
- Statistic: disagreement rates by depth.

**Weight** — medium. Useful for the point that disagreement concentrates on marginal items, which is
the same population where our span-set union is unstable.

### `carterette-2008-preference`

**Here or There: Preference Judgments for Relevance**
Ben Carterette, Paul N. Bennett, David Maxwell Chickering, Susan T. Dumais.
ECIR 2008, *Advances in Information Retrieval*, LNCS, pp. 16–27, DOI `10.1007/978-3-540-78646-7_5`.
https://doi.org/10.1007/978-3-540-78646-7_5
**verified** (Crossref record for the DOI — deposited title truncated to "Here or There"; full title
and abstract confirmed from the Microsoft Research publication page).

Hypothesises and gives assessor-study evidence that *preference* judgments ("A is more relevant than
B") are easier for assessors to make than absolute judgments, and shows you need not compare all
pairs.

- Who disagrees: **between** assessors.
- Unit: document **pairs**.
- Gold: a preference relation rather than an absolute label — the closest classical-IR move toward
  changing the *object* being judged to make it more stable.
- Statistic: assessor agreement and judging time on preferences vs. absolutes.

**Weight** — medium-high as a **precedent for our rhetorical move**: when the discrete object is
unstable, change the object. They changed absolute→relative; we changed set-valued→graded.

### `zobel-1998`

**How reliable are the results of large-scale information retrieval experiments?**
Justin Zobel.
SIGIR '98, pp. 307–314, DOI `10.1145/290941.291014`.
https://doi.org/10.1145/290941.291014
**verified** (Crossref).

The pooling-bias paper: pooled qrels are incomplete, unpooled runs are systematically disadvantaged,
and the size of that bias is estimable.

- Unit: document. Gold: single label. Statistic: estimated relevant-document yield / ranking effects.

**Weight** — medium; background for "the gold set is not the truth, it is a sample".

### `buckley-2004`

**Retrieval evaluation with incomplete information**
Chris Buckley, Ellen M. Voorhees.
SIGIR '04, pp. 25–32, DOI `10.1145/1008992.1009000`.
https://doi.org/10.1145/1008992.1009000
**verified** (Crossref).

Introduces **bpref** and shows that standard measures degrade badly under incomplete judgments while
a measure defined only over judged documents degrades gracefully.

**Weight** — medium; the template for "design the measure so it is robust to the instability you
cannot remove".

### `urbano-2013`

**On the measurement of test collection reliability**
Julián Urbano, Mónica Marrero, Diego Martín.
SIGIR '13, pp. 393–402, DOI `10.1145/2484028.2484038`.
https://doi.org/10.1145/2484028.2484038
**verified** (Crossref). Companion software verified independently: `gt4ireval`,
"Generalizability Theory for Information Retrieval Evaluation", Julián Urbano, CRAN,
DOI `10.32614/CRAN.package.gt4ireval`, described as "tools to measure the reliability of an
Information Retrieval test collection … using Generalizability Theory and map those estimates onto
well-known indicators such as Kendall tau correlation or sensitivity".
Not **read** — the publisher page is behind ACM and the author's PDF mirror 404'd; the description
above is from the Crossref record and the CRAN package page only.

Applies **generalizability theory** — the psychometric framework in which a measurement's reliability
is the ratio of universe-score variance to observed variance, and in which you can *predict the
reliability of an aggregate* of k observations — to IR test collections.

- Unit: topic × system, not document or span.
- Gold: n/a; this is about reliability of an effectiveness estimate.
- Statistic: **generalizability coefficients (Eρ², Φ)** — the same family as Spearman-Brown.

**Weight** — **high for framing.** This is the IR community's own precedent for "reliability of a
pooled measurement", and it means a Spearman-Brown-style coefficient is not foreign vocabulary in
this venue. Cite it together with `wong-2022`.

### `bodoff-2007` / `bodoff-2008`

**Test theory for assessing IR test collections**
David Bodoff, Pu Li. SIGIR '07, pp. 367–374, DOI `10.1145/1277741.1277805`.
Journal version: **Test theory for evaluating reliability of IR test collections**, David Bodoff,
*Information Processing & Management* 44(3):1117–1145, 2008, DOI `10.1016/j.ipm.2007.11.006`.
**verified** (Crossref, both). Note the author list differs between the two versions.

Classical test theory / G-theory applied to the reliability of a test collection: how many topics,
how many assessors, to reach a target reliability.

**Weight** — medium-high, same framing role as `urbano-2013`, earlier.

---

## 2. Within-assessor repetition (test-retest) — the thin part of the literature

### `scholer-2011`

**Quantifying test collection quality based on the consistency of relevance judgements**
Falk Scholer, Andrew Turpin, Mark Sanderson.
SIGIR '11, pp. 1063–1072, DOI `10.1145/2009916.2010057`.
https://doi.org/10.1145/2009916.2010057
**verified** (Crossref).

The one classical-IR design that is genuinely test-retest: duplicate documents are planted in the
assessment pool so the *same* assessor judges the *same* document twice, and self-inconsistency is
measured directly.

- Who disagrees: **within one assessor, repeated** — a real test-retest design.
- Unit: **whole document**. Presence only; no location component.
- Gold: single label.
- Statistic: self-disagreement rate.

**Weight** — **highest in this section, and the single most dangerous paper for a
"test-retest is novel" claim.** Our claim must be narrowed to: test-retest on *span-level / location*
annotation, and test-retest of a *model* rather than a person. Scholer et al. own the document-level
human case.

### `abercrombie-2023`

**Consistency is Key: Disentangling Label Variation in Natural Language Processing with
Intra-Annotator Agreement**
Gavin Abercrombie, Tanvi Dinkar, Amanda Cercas Curry, Verena Rieser, Dirk Hovy.
arXiv:2301.10684 (v1 25 Jan 2023, v2 20 Oct 2025); accepted to the Fourth Workshop on Perspectivist
Approaches to NLP (NLPerspectives), 2025.
https://arxiv.org/abs/2301.10684
**verified** (arXiv API + abs page) · **read** (PDF v2).

Directly argues for intra-annotator agreement as a standard measure, and quantifies how absent it is.
From the paper: a systematic review of the ACL Anthology "returned only 56 relevant publications out
of more than 80,000 papers listed in the Anthology … a tiny fraction (less than 0.07%) … report
measurement of intra-annotator agreement", and the only sub-area where it is reported with any
regularity is machine translation. Their own longitudinal experiment finds annotators "provide
inconsistent responses for more than 25% of items".

Two caveats I checked in the PDF and which must be stated if we quote the 25%:

1. The four tasks are **all 2–3 class label tasks** — offensive language detection
   (Leonardelli et al. 2021), sentiment (Kenyon-Dean et al. 2018), NLI (Williams et al. 2018),
   anaphora referring/non-referring (Poesio et al. 2019). **No span task.**
2. They deliberately "selected 50 items with high disagreement in the original label sets for
   re-annotation", so 25% is a rate on a hard-case subsample, **not** a population estimate.

⚠️ **They also hand us the right vocabulary.** Their Table 3 is headed **"Reliability (Inter-)"** and
**"Stability (Intra-)"** — Krippendorff's own terms — with pairwise raw percentage agreement:

| task | reliability (inter) µ / σ | stability (intra) µ / σ |
|---|---|---|
| Offence | 68.3 / 15.4 | 74.4 / 15.0 |
| Sentiment | 63.6 / 21.7 | 69.2 / 19.5 |
| Entailment | 58.6 / 21.4 | 72.6 / 15.1 |
| Anaphora | 76.2 / 14.3 | 80.5 / 13.0 |
| **Overall** | **66.7 / 19.6** | **74.2 / 16.3** |

"As expected, agreement is higher for stability than reliability for all tasks, although considerably
lower than perfect agreement — just 74.2% overall, and no higher than 80.5% for any task."
Per-annotator stability µ = 74.2%, σ = 4.3%, range 67.5–81.5%; matched by Abercrombie et al. (2023)
at 74.5% on hate speech. Kruskal-Wallis shows tasks differ significantly on both (H = 12.42 and
10.76, both p = 0.01). They report raw percent agreement as primary "as intra-annotator agreement is
typically assumed to be 100%", with Cohen's κ relegated to an appendix. Two of the 56 reviewed papers
study interval length and **both find consistency degrades monotonically with it.**

- Who disagrees: **within one annotator, repeated** (2-week interval in their own study; in the
  reviewed literature the label–relabel interval ranges from minutes to a year, and 15/56 do not
  state it at all).
- Unit: **item-level label**, not span.
- Gold: single label; the contribution is the reliability measure, not a new gold object.
- Statistic: standard agreement coefficients (they recommend applying any of them within-annotator);
  Krippendorff's α in the reviewed MT practice.

**Weight** — **highest for the "test-retest is rare" claim.** This is the evidence, with a number, and
it is a 2023/2025 paper so it is current. It also bounds our claim honestly: test-retest on *labels*
has now been argued for and surveyed; test-retest on *locations* has not.

### `amidei-2020`

**Aligning Intraobserver Agreement by Transitivity**
Jacopo Amidei.
arXiv:2009.13905, 29 Sep 2020.
https://arxiv.org/abs/2009.13905
**verified** (arXiv API). Not read.

Proposes using transitivity of the annotator's own judgments as an intra-observer consistency
criterion.

**Weight** — low-medium; useful as a second citation that intra-observer work exists and is small.

---

## 3. Agreement methodology and its critics

### `artstein-2008`

**Survey Article: Inter-Coder Agreement for Computational Linguistics**
Ron Artstein, Massimo Poesio.
*Computational Linguistics* 34(4):555–596, 2008, DOI `10.1162/coli.07-034-R2`.
https://aclanthology.org/J08-4004/
**verified** (ACL Anthology landing page + Crossref) · **read** (ACL PDF, all 42 pages extracted).

⚠️ The exact title carries the "Survey Article:" prefix as published.

The reference work on agreement coefficients. Four things in it are load-bearing for us, all
confirmed by reading:

1. **Agreement on *where* is almost never measured.** §4.3: "The practice in CL … is to assume that
   the units are linguistic constituents which can be easily identified … and therefore there is no
   need to check the reliability of this process. **We are aware of few exceptions to this
   assumption**." And: "the problem of markable identification is more pervasive than is generally
   acknowledged."
2. **There is essentially one proposal for it.** "The one proposal for measuring agreement on
   markable identification we are aware of is the α_U coefficient, a non-trivial variant of α
   proposed by Krippendorff (1995)."
3. **κ on boundary/no-boundary is known to misbehave.** They give worked tables showing the same
   underlying quality of segmentation yielding K = 0.65 vs K = 0.75 purely from base-rate change, and
   note that counting boundaries "would underestimate the degree of agreement, suggesting low
   agreement even among coders whose segmentations are mostly similar". They also note that Pk and
   WindowDiff "are, however, raw agreement scores not corrected for chance".
4. **Set-valued annotation needs weighted coefficients.** §5: "there are at least two types of coding
   schemes in which this is the case … set-valued interpretations such as those proposed for
   anaphora. At least in the second case, weighted coefficients are almost unavoidable." They
   present Jaccard and Dice distances and Passonneau's MASI as the α weights for set-valued items.

**There is no occurrence of "intra-coder", "intra-annotator", or "test-retest" anywhere in the
42 pages.** I grepped for all three.

- Who disagrees: between coders throughout. Test-retest is simply not in the survey.
- Unit: both — this is the survey that separates *unitizing* from *labelling*.
- Gold: single label per coder; the survey is about measuring agreement, not about the gold object.
- Statistic: π, κ, α, α_U, weighted α with Jaccard/Dice/MASI distances.

**Weight** — **must-cite, and the best single source for the claim that agreement on location is
under-studied and that κ/Jaccard misbehave on span-valued data.** Quote item 1 and item 3.

### `krippendorff-1995`

**On the Reliability of Unitizing Continuous Data**
Klaus Krippendorff.
*Sociological Methodology* 25:47–76, 1995, DOI `10.2307/271061`.
https://doi.org/10.2307/271061
**verified** (Crossref).

The original α_U: unitizing is decomposed into identifying boundaries and selecting units of
interest; disagreement is the squared length of non-overlapping segments, with a penalty for a unit
one coder marked and the other did not touch at all.

- Unit: **continuum / span**, explicitly. This is the theory of location agreement.
- Statistic: α_U.

**Weight** — high. If we report anything span-shaped, α_U is the coefficient a reviewer will ask why
we did not use. (It is also a reason to prefer the graded score: α_U assumes crisp units.)

### `krippendorff-2016`

**On the reliability of unitizing textual continua: further developments**
Klaus Krippendorff, Yann Mathet, Stéphane Bouvry, Antoine Widlöcher.
*Quality & Quantity* 50(6):2347–2364, DOI `10.1007/s11135-015-0266-1` (issue dated 2016; Crossref
`issued` 2015). An erratum exists: *Quality & Quantity* 50(6):2365, DOI `10.1007/s11135-015-0289-7`.
**verified** (Crossref, both the article and the erratum).

Extends α_U, including to multi-category unitizing.

**Weight** — medium-high; cite alongside `krippendorff-1995` and note the erratum.

### `mathet-2017` (γ_cat) — ★ the statistic that already separates *where* from *what*

**The Agreement Measure γ_cat, a Complement to γ Focused on Categorization of a Continuum**
Yann Mathet (sole author).
*Computational Linguistics* 43(3):661–681, 2017, DOI `10.1162/COLI_a_00296`.
https://aclanthology.org/J17-3006/
**verified** (Crossref) · **read** (ACL PDF; the quotes below are verbatim from the extracted text).

⚠️⚠️ **This is the single most important methodological paper for us, and it is barely cited.** Its
whole purpose is the decomposition we claim to be making:

> "γ_cat tries to answer the question: **If annotators had not had to unitize the continuum (put
> units by themselves and categorize them), but only to categorize predefined units on the continuum,
> what would have been their agreement?**"

and

> "The very objective is that γ_cat be **insensitive to disagreements that involve other aspects of
> unitizing than categorization (positions, lengths, etc.)**, contrary to γ."

γ measures the joint thing; γ_cat measures the categorial part with position factored out; therefore
**γ − γ_cat isolates the positional/unitizing component** — which is exactly "agreement about where,
separated from agreement about what". Three stated requirements: insensitivity to positional
discrepancies, to false positives/negatives, and to unit size. It reduces to Krippendorff's α on
pre-defined units (including with missing values), and a per-category variant γ_k is also given.
It corresponds, Mathet says, to the `kα` of Krippendorff et al. (2016).

Worked examples from the paper that are directly about our failure mode: two annotators marking the
same three units at slightly offset positions with identical categories produce ~20% **fake**
categorial disagreement under intersection-based (α-family) or atomisation-based measures; the NER
case "Barack Hussein Obama II" vs "Obama" gives 50% unit-weighted vs an artificial 80%
length-weighted agreement.

**Weight** — **highest in the methodology section.** If we propose a presence/location split, this is
the coefficient a reviewer will say we should have used, and we must engage with it by name. It also
helps us: the existence of γ_cat is *evidence that the split is a recognised, hard problem*, and the
fact that the field still reports token-κ or F1 instead is itself a finding we can report.

### `mathet-2015`

**The Unified and Holistic Method Gamma (γ) for Inter-Annotator Agreement Measure and Alignment**
Yann Mathet, Antoine Widlöcher, Jean-Philippe Métivier.
*Computational Linguistics* 41(3):437–479, 2015, DOI `10.1162/COLI_a_00227`.
https://doi.org/10.1162/coli_a_00227
**verified** (Crossref).
Implementation verified separately: **pygamma-agreement: Gamma (γ) measure for inter/intra-annotator
agreement in Python**, Hadrien Titeux, Rachid Riad, *JOSS* 6(62):2989, 2021,
DOI `10.21105/joss.02989` — note the software's own title says **"inter/intra-annotator"**, i.e. the
machinery for within-annotator span agreement exists even if the experiments do not.

γ is the current best answer to "one coefficient that handles unitizing and categorisation together":
it aligns the annotators' units first, then scores, and is chance-corrected. The argument is that
alignment and agreement must be computed as one process, because any measure needs an alignment and
the alignment must follow the measure's own principles.

**Its Table 2 is a demolition of every statistic we might otherwise reach for on span data**, and
their degradation experiments (§6.3, §6.5) are the empirical backing: as annotations are
progressively corrupted, γ decreases strictly toward ~0, whereas α_U is **not monotone and goes
below 0** for combined positional+categorial errors (from magnitude 0.6) and for false negatives
(from m = 0.3); κ_d is not strictly decreasing; SER is **bounded below by 0.6** purely from lacking
chance correction and is not upper-bounded by 1; `c|uα` "does not react at all, but remains stuck at
1" for splits. F-measure satisfies only the "categorizing" capability; WindowDiff and GHD cover
segmentation only and are **not chance-corrected**.

⚠️ **§3.4.1 names and refutes the thing most people actually do**: the *discretising workaround* —
atomise the text into tokens and run κ or α per token. Its drawbacks: the more gaps in the real
annotations, the more artificial "blank" agreement and hence **artificial inflation**; and
overlapping or embedded units become impossible, since each position must take exactly one category.
**Token-level κ over a document is that workaround**, and this is the published account of its bias.
(Reidsma, Heylen & Ordelman are cited for the further point that it "does not compensate for
differences in length of segments", where "short segments are as important as long segments".)

- Unit: **span**, both boundaries and labels, jointly, with overlap and embedding supported.
- Statistic: γ — deliberately **holistic**, which is precisely what `mathet-2017`'s γ_cat was later
  written to decompose.

**Weight** — **high.** The strongest methodological alternative to what we did, and the source of the
citation we need if we report anything token-level and chance-corrected. Note also that
`artstein-2008` had hoped α_U "may be appropriate for … discourse segmentation" — Mathet et al.
quote that hope and falsify it empirically.

### `passonneau-2006-masi`

**Measuring Agreement on Set-valued Items (MASI) for Semantic and Pragmatic Annotation**
Rebecca Passonneau.
LREC 2006, pp. 831–836. Crossref DOI `10.63317/4nuo6thi27ax`.
**verified** (Crossref; cross-checked against the reference as printed in `artstein-2008`, which cites
it as "Proceedings of LREC, Genoa, pages 831–836").

The distance metric for set-valued annotation: MASI = Jaccard × a **monotonicity** term
(1 if identical, 2/3 if one set subsumes the other, 1/3 if they intersect with both differences
non-null, 0 if disjoint), used as the distance δ inside Krippendorff's α.

⚠️ **The numbers in this paper are the strongest evidence in the whole survey that a set-agreement
figure is mostly a choice of statistic, not a measurement.** On the same five DUC pyramid datasets:

| distance | α |
|---|---|
| nominal | .01 – .24 |
| Jaccard | .39 – .58 |
| MASI | .68 – .80 |

and on the same data, changing only the **coding unit** (Table 1): with **spans** as units,
nominal α = 0, **Jaccard α = −0.44**, MASI α = 0.14; with **words** as units, 0, **.64**, **.81**.
So one dataset yields α anywhere from **−0.44 to +0.81** with no change to the annotations at all.
(Mean 725 words and 92 distinct SCUs per pyramid.)

- Who disagrees: between annotators (two independently built pyramids per docset).
- Unit: **both** — and the paper shows the unit choice dominates the result.
- Statistic: MASI as an α distance; the clearest published demonstration that **plain Jaccard and
  nominal κ/α behave badly on set-valued annotation**, MASI's fix being to make near-misses and
  subsumption cheap.

**Weight** — **high, and it constrains how we may report our span number.** Any single reproduction
figure for a span *set* has to be accompanied by the unit and distance it was computed under,
because this paper shows the choice can move the answer by more than a full point of α.

### `hripcsak-2005`

**Agreement, the F-Measure, and Reliability in Information Retrieval**
George Hripcsak, Adam S. Rothschild.
*Journal of the American Medical Informatics Association* 12(3):296–298, 2005, DOI `10.1197/jamia.M1733`.
https://pmc.ncbi.nlm.nih.gov/articles/PMC1090460/
**verified** (PMC full text) · **read**.

⚠️ Crossref deposits only the first author ("G. Hripcsak"); the full author list is Hripcsak and
Rothschild, confirmed from the PMC record.

Short and decisive. When negative cases cannot be counted — "as in Internet document retrieval or
text markup tasks" — κ **cannot be computed at all**, because there is no meaningful count of true
negatives from which to derive chance agreement. They prove that "the average F-measure among pairs
of experts is numerically identical to the average positive specific agreement among experts", so
mean pairwise F1 *is* a legitimate inter-rater reliability statistic in exactly that setting, and
κ approaches it only as the negative class grows large.

- Unit: **text markup / retrieved set** — precisely the span-set case.
- Statistic: positive specific agreement ≡ mean pairwise F1; argument against κ.

**Weight** — **high.** This is the cleanest citation for "κ is the wrong statistic for span sets, and
here is what to use instead". It also legitimises reporting a pairwise-F1-shaped number for the span
condition.

### `carletta-1996`

**Assessing Agreement on Classification Tasks: The Kappa Statistic**
Jean Carletta.
*Computational Linguistics* 22(2), 1996.
https://aclanthology.org/J96-2004/
**verified** (ACL Anthology BibTeX).

The paper that made κ and the 0.67/0.8 thresholds standard in CL. `artstein-2008` documents that the
0.67 level was described by Krippendorff as "highly tentative and cautious" and that he later
considered α = 0.8 "a pretty low standard".

**Weight** — medium; cite when we say what the field's default expectations are.

### `feinstein-1990`, `byrt-1993`, `powers-2012`

- **High agreement but low Kappa: I. The problems of two paradoxes** — Alvan R. Feinstein,
  Domenic V. Cicchetti, *Journal of Clinical Epidemiology* 43(6):543–549, 1990,
  DOI `10.1016/0895-4356(90)90158-L`. **verified** (Crossref). A companion part II
  ("Resolving the paradoxes") exists in the same journal.
- **Bias, prevalence and kappa** — Ted Byrt, Janet Bishop, John B. Carlin,
  *Journal of Clinical Epidemiology* 46(5):423–429, 1993, DOI `10.1016/0895-4356(93)90018-V`.
  **verified** (Crossref).
- **The Problem with Kappa** — David Martin Ward Powers, EACL 2012, pp. 345–355.
  https://aclanthology.org/E12-1035/ **verified** (ACL Anthology BibTeX).

The κ-paradox literature: with a skewed marginal distribution (our case — most sentences in a
document are not evidence) high observed agreement coexists with a near-zero κ, and κ is sensitive to
prevalence and to rater bias in ways that make cross-study comparison invalid.

**Weight** — medium-high as a *cluster*. Cite one of Feinstein/Byrt plus Powers in a single sentence
to justify not reporting κ on the span condition.

### `bayerl-2011`

**What Determines Inter-Coder Agreement in Manual Annotations? A Meta-Analytic Investigation**
Petra Saskia Bayerl, Karsten Ingmar Paul.
*Computational Linguistics* 37(4):699–725, 2011, DOI `10.1162/coli_a_00074`.
**verified** (Crossref).

Meta-analysis of what actually moves agreement (domain, number of categories, training, annotator
count).

**Weight** — medium; useful for "agreement numbers are not comparable across studies".

### `james-2026`

**Counting on Consensus: Selecting the Right Inter-annotator Agreement Metric for NLP Annotation and
Evaluation**
Joseph James.
arXiv:2603.06865 (v2, first posted 6 Mar 2026); comment says "Accepted LREC 2026".
https://arxiv.org/abs/2603.06865
**verified** (arXiv API). Not read.

⚠️ Single-author, very recent, venue acceptance asserted only in the arXiv comment field — I did not
find an LREC 2026 proceedings record to confirm it. Treat as a preprint.

**Weight** — low-medium; cite only if we need a current "which coefficient should you use" pointer.

---

## 4. Disagreement as signal, not noise

### `aroyo-2015`

**Truth Is a Lie: Crowd Truth and the Seven Myths of Human Annotation**
Lora Aroyo, Chris Welty.
*AI Magazine* 36(1):15–24, 2015, DOI `10.1609/aimag.v36i1.2564`.
https://ojs.aaai.org/aimagazine/index.php/aimagazine/article/view/2564
**verified** (AAAI OJS landing page, abstract retrieved verbatim).

The manifesto. From the abstract: human annotation "is based on an antiquated ideal of a single
correct truth that needs to be similarly disrupted … We propose a new theory of truth, crowd truth,
that is based on the intuition that human interpretation is subjective, and that measuring
annotations on the same objects of interpretation (in our examples, sentences) across a crowd will
provide a useful representation of their subjectivity and the range of reasonable interpretations."

- Who disagrees: **between** annotators (a crowd).
- Unit: sentences, in their examples.
- Gold: **explicitly graded/vector-valued** — "crowd truth" replaces the single label with a
  distribution over the crowd.
- Statistic: their own crowd-truth metrics (worker/sentence/annotation quality vectors) rather than κ.

**Weight** — **must-cite.** This is the named prior idea closest in spirit to replacing a discrete
gold with a pooled graded one. Note carefully: crowd truth pools *across people*, we pool *across
readings of one model* — that is the distinction to draw explicitly.

### `dumitrache-2018` / `dumitrache-2021`

- **Crowdsourcing Ground Truth for Medical Relation Extraction** — Anca Dumitrache, Lora Aroyo,
  Chris Welty, *ACM Transactions on Interactive Intelligent Systems* 8(2):1–20, 2018,
  DOI `10.1145/3152889`. **verified** (Crossref).
- **Empirical methodology for crowdsourcing ground truth** — Anca Dumitrache, Oana Inel,
  Benjamin Timmermans, Carlos Ortiz, Robert-Jan Sips, Lora Aroyo, Chris Welty, *Semantic Web*
  12(3):403–421, 2021, DOI `10.3233/SW-200415`. **verified** (Crossref).

The worked-out CrowdTruth method behind `aroyo-2015`.

**Weight** — medium; cite one of them as the operational version of crowd truth.

### `plank-2022`

**The "Problem" of Human Label Variation: On Ground Truth in Data, Modeling and Evaluation**
Barbara Plank.
EMNLP 2022, pp. 10671–10682, DOI `10.18653/v1/2022.emnlp-main.731`.
https://aclanthology.org/2022.emnlp-main.731/
**verified** (Crossref).

Coins/consolidates **human label variation (HLV)** as the field's term and argues it should be
modelled at every stage rather than removed.

**Weight** — **must-cite**; this is the term of art. See § The strongest framing available.

### `plank-2014`

**Learning part-of-speech taggers with inter-annotator agreement loss**
Barbara Plank, Dirk Hovy, Anders Søgaard.
EACL 2014, pp. 742–751, DOI `10.3115/v1/E14-1078`.
**verified** (Crossref).

The early "use disagreement in the loss" result, on an objective task (POS).

**Weight** — medium.

### `uma-2021`

**Learning from Disagreement: A Survey**
Alexandra N. Uma, Tommaso Fornaciari, Dirk Hovy, Silviu Paun, Barbara Plank, Massimo Poesio.
*Journal of Artificial Intelligence Research* 72:1385–1470, 2021, DOI `10.1613/jair.1.12752`.
https://www.jair.org/index.php/jair/article/view/12752
**verified** (JAIR landing page, abstract retrieved).

The survey of the whole area, with a systematic comparison of training methods. Key reported finding:
evaluation methodology materially changes model rankings, and **soft-label training wins on
high-quality multi-annotated datasets**.

- Who disagrees: **between** annotators throughout.
- Unit: item-level labels (NLP and CV classification).
- Gold: **soft labels / annotator distributions** — the central object of the survey.
- Statistic: surveys agreement coefficients; evaluation via soft metrics (cross-entropy, JSD) as well
  as hard.

**Weight** — **must-cite** as the canonical survey of "graded gold instead of a single label".

### `uma-2021-semeval`

**SemEval-2021 Task 12: Learning with Disagreements**
Alexandra Uma, Tommaso Fornaciari, Anca Dumitrache, Tristan Miller, Jon Chamberlain, Barbara Plank,
Edwin Simpson, Massimo Poesio.
SemEval-2021, pp. 338–347, DOI `10.18653/v1/2021.semeval-1.41`.
**verified** (Crossref).

The shared task that made soft-label evaluation concrete. (A successor, Le-Wi-Di, ran at
SemEval-2023.)

**Weight** — medium.

### `pavlick-2019`

**Inherent Disagreements in Human Textual Inferences**
Ellie Pavlick, Tom Kwiatkowski.
*Transactions of the ACL* 7:677–694, 2019, DOI `10.1162/tacl_a_00293`.
**verified** (Crossref).

Shows NLI disagreement is not annotation noise that more annotators would wash out — the
distributions are genuinely multi-modal.

**Weight** — high; the standard "disagreement is real" citation.

### `nie-2020`

**What Can We Learn from Collective Human Opinions on Natural Language Inference Data?**
Yixin Nie, Xiang Zhou, Mohit Bansal.
EMNLP 2020, pp. 9131–9143, DOI `10.18653/v1/2020.emnlp-main.734`.
**verified** (Crossref).

ChaosNLI: ~100 annotations per item, used to show that model calibration against the *distribution*
is a different and harder target than accuracy against the majority.

- Gold: **empirical distribution over ~100 human labels** — the highest-redundancy human soft gold in
  NLP.
- Unit: item-level label.

**Weight** — **high.** The closest human analogue of our thirty readings, in redundancy if not in
design. Worth stating explicitly that ChaosNLI's redundancy is *across people* and at the *item*
level.

### `passonneau-2014` and `paun-2018`

- **The Benefits of a Model of Annotation** — Rebecca J. Passonneau, Bob Carpenter, *TACL*
  2:311–326, 2014, DOI `10.1162/tacl_a_00185` (an erratum exists). **verified** (Crossref).
- **Comparing Bayesian Models of Annotation** — Silviu Paun, Bob Carpenter, Jon Chamberlain,
  Dirk Hovy, Udo Kruschwitz, Massimo Poesio, *TACL* 6:571–585, 2018, DOI `10.1162/tacl_a_00040`.
  **verified** (Crossref).

Probabilistic annotation models (Dawid-Skene and descendants) that infer a *posterior over the true
label* plus per-annotator error rates, rather than taking a majority vote.

- Gold: **probabilistic**, with an explicit generative model.
- Unit: item-level label.
- Statistic: model-based; they argue agreement coefficients are the wrong tool entirely.

**Weight** — **high.** If a reviewer asks "why a mean over readings rather than a Dawid-Skene-style
model", this is the literature they mean. We should say why pooling is adequate here (identical
annotator ⇒ no per-annotator bias term to estimate).

### `davani-2022`, `cabitza-2023`, `sandri-2023`, `rizzi-2024`, `kurniawan-2026`, `gruber-2024`

- **Dealing with Disagreements: Looking Beyond the Majority Vote in Subjective Annotations** —
  Aida Mostafazadeh Davani, Mark Díaz, Vinodkumar Prabhakaran, *TACL* 10:92–110, 2022,
  DOI `10.1162/tacl_a_00449`. **verified** (Crossref). Multi-annotator architectures that predict
  each annotator rather than the aggregate.
- **Toward a Perspectivist Turn in Ground Truthing for Predictive Computing** — Federico Cabitza,
  Andrea Campagner, Valerio Basile, *AAAI* 37(6):6860–6868, 2023, DOI `10.1609/aaai.v37i6.25840`.
  **verified** (Crossref). The "perspectivist" programme statement.
- **Why Don't You Do It Right? Analysing Annotators' Disagreement in Subjective Tasks** —
  Marta Sandri, Elisa Leonardelli, Sara Tonelli, Elisabetta Jezek, EACL 2023, pp. 2428–2441,
  DOI `10.18653/v1/2023.eacl-main.178`. **verified** (Crossref). A taxonomy of *why* annotators
  disagree — useful because it separates "genuine ambiguity" from "annotator error", which is the
  distinction a test-retest design can actually resolve.
- **Soft metrics for evaluation with disagreements: an assessment** — Giulia Rizzi, Elisa Leonardelli,
  Massimo Poesio, Alexandra Uma, Maja Pavlovic, Silviu Paun, Paolo Rosso, Elisabetta Fersini,
  3rd Workshop on Perspectivist Approaches to NLP (NLPerspectives) @ LREC-COLING 2024, pp. 84–94.
  https://aclanthology.org/2024.nlperspectives-1.9/ **verified** (ACL Anthology BibTeX).
- **Training and Evaluating with Human Label Variation: An Empirical Study** — Kemal Kurniawan,
  Meladel Mistica, Timothy Baldwin, Jey Han Lau, *Computational Linguistics* 52(1):85–111, 2026,
  DOI `10.1162/coli.a.578`. **verified** (Crossref). The current systematic study of HLV training and
  evaluation.
- **More Labels or Cases? Assessing Label Variation in Natural Language Inference** — Cornelia Gruber,
  Katharina Hechinger, Matthias Aßenmacher, Göran Kauermann, Barbara Plank, Third Workshop on
  Understanding Implicit and Underspecified Language (UnImplicit) 2024, pp. 22–32,
  DOI `10.18653/v1/2024.unimplicit-1.2`. **verified** (Crossref) · **read** (ACL PDF).
  Models annotator votes with a Bayesian mixture and, crucially for us, studies the *design* tradeoff:
  "few instances with many labels can predict the latent class borders reasonably well, while the
  estimation fails for many instances with only a few labels". That is a direct argument for **depth
  of repetition over breadth of items** — the same design choice as thirty readings per pair.

**Weight** — `gruber-2024` medium-high (design justification); the rest medium, cite as a cluster for
"the field has moved to graded gold".

---

## 5. Location vs. presence — rationale and evidence-span annotation

### `jakobsen-2023`

**Being Right for Whose Right Reasons?**
Terne Sasha Thorn Jakobsen, Laura Cabello, Anders Søgaard.
ACL 2023 (Volume 1: Long Papers), pp. 1033–1054, DOI `10.18653/v1/2023.acl-long.59`; arXiv:2306.00639.
https://aclanthology.org/2023.acl-long.59/
**verified** (Crossref + ACL + arXiv API) · **read** (ACL PDF).

⚠️ **This is the closest published thing to our presence/location dissociation, and it is
between-annotator.** Their design, in their words: "When annotators disagree on the label of an
instance, it is to be expected that their rationales will subsequently be different. Therefore, to
compare group-group … rationales more fairly, **we focus on the subset of instances where all groups
are in agreement about** [the label]." That is exactly conditioning location agreement on presence
agreement.

Numbers (Figure 4, extracted): with **full label agreement**, group-group rationale agreement by
token-level binary F1 is **0.60–0.67 on DynaSent, 0.52–0.64 on SST-2, and 0.41–0.52 on CoS-E**; the
mean against a randomly paired group is 0.54–0.58. So: labels unanimous, locations agreeing roughly
half the time, and systematically worse on the harder (CoS-E) task.

- Who disagrees: **between** annotators, grouped by demographics (six groups, age × ethnicity).
- Unit: **token-level rationale spans**, with the label handled separately.
- Gold: hard rationale masks per group; no graded pooled object.
- Statistic: **token-level binary F1** and **IOU-F1** (IOU ≥ 0.5 thresholded), i.e. exactly the
  Jaccard-family statistics that `hripcsak-2005` and `artstein-2008` warn about.

**Weight** — **highest in this section.** Cite it as the between-annotator precedent, and be explicit
that it is between-annotator and not repeated-measures. It also gives us a calibration point: their
0.41–0.67 token-F1 on agreed labels is the human ceiling our span-union reproduction should be
compared to, not to 1.0.

### `mathew-2021` (HateXplain)

**HateXplain: A Benchmark Dataset for Explainable Hate Speech Detection**
Binny Mathew, Punyajoy Saha, Seid Muhie Yimam, Chris Biemann, Pawan Goyal, Animesh Mukherjee.
*Proceedings of the AAAI Conference on Artificial Intelligence* 35(17):14867–14875, 2021,
DOI `10.1609/aaai.v35i17.17745`.
**verified** (Crossref). Its aggregation method confirmed independently from `muscato-2026`, which
states: "Mathew et al. (2021) aggregate annotator rationales into soft temperature-scaled attention
distributions".

⚠️ **Prior art for a graded per-token support score.** HateXplain collects rationale spans from
multiple annotators per post and **pools them into a continuous per-token importance distribution**
rather than a span set.

- Who disagrees: **between** annotators (3 per post).
- Unit: **token spans**.
- Gold: **graded per-token**, pooled across annotators.
- Statistic: they report inter-annotator agreement on the *labels*; I did not find a reliability
  coefficient reported for the pooled token distribution itself.

**Weight** — **high, and a threat.** "Pool span annotations into a graded per-unit score" is not new
as a *representation*. What appears to remain open is (a) doing it over repeated readings of one
annotator and (b) reporting a **reliability coefficient for the graded object** and contrasting it
with the reproduction rate of the discrete object. We must make the claim at that precision.

### `muscato-2026`

**Disagreeing Rationales: Rethinking Classification and Explainability Evaluation in Hate Speech
Detection**
Benedetta Muscato, Beiduo Chen, Gizem Gezici, Barbara Plank, Fosca Giannotti.
arXiv:2605.31563, 29 May 2026 (16 pages; no venue stated).
https://arxiv.org/abs/2605.31563
**verified** (arXiv abs page) · **read** (arXiv PDF v1).

⚠️ **The most directly competing framing I found.** From the abstract: "Human disagreement is
ubiquitous and well-known in labeling. However, **variation in explanations, captured through
token-level human rationales, remains far less explored.** At the same time, it is unclear how to
best evaluate human labels and rationales — **or even how to best aggregate rationales beyond
majority vote** — in light of this variation."

They define three rationale representation spaces — HARD (majority-voted binary token mask),
INTERMEDIATE (union over annotators, or random, or all tokens) and SOFT (continuous per-token
importance) — and show softer representations win on both classification and explainability metrics.
Note that their **INTERMEDIATE/union** condition is precisely the "span-set union" object we report
as unstable, and their **SOFT** condition is the graded per-token object.

They also report, as prior work, the presence/location dissociation: "Hong et al. (2025) and Jiang
et al. (2023) complementarily show that **annotators may agree on labels but diverge in
justifications, or vice versa**."

- Who disagrees: **between** annotators.
- Unit: **token-level rationales**, with labels handled separately.
- Gold: **soft (graded) labels and soft (graded) rationales**, explicitly contrasted with hard and
  union.
- Statistic: IOU-F1, token-F1, AUPRC, comprehensiveness/sufficiency, soft variants, JSD; it is an
  evaluation-metric paper, not a reliability-coefficient paper. It is a preprint with no venue.

**Weight** — **high; read it carefully before writing our related work.** It is the paper a reviewer
will say we duplicate. The separation is: they compare *representations for training and evaluating
models*; we are measuring the *reliability of the annotation process itself* under repetition.

### `hofstatter-2020` (FiRA)

**Fine-Grained Relevance Annotations for Multi-Task Document Ranking and Question Answering**
Sebastian Hofstätter, Markus Zlabinger, Mete Sertkan, Michael Schröder, Allan Hanbury.
CIKM '20, pp. 3031–3038, DOI `10.1145/3340531.3412878`; arXiv:2008.05363.
https://doi.org/10.1145/3340531.3412878
**verified** (Crossref + arXiv) · **read** (arXiv PDF).

⚠️⚠️ **The most directly complicating paper on the IR side, and it cuts against us.** FiRA extends
TREC 2019 Deep Learning document judgments with **word-level relevance selections and passage-level
graded labels**, 3-way majority voting, and — crucially — **measures agreement on the word selection
separately from agreement on the relevance class.**

Their Figure 5 and the text around it, verbatim: Cohen's κ between each student and the
majority-aggregated annotation, on the 10 pairs annotated by everyone — "For the **2-class relevance
assignment and the labeling of the relevant word phrases, substantial Kappa agreements are reached,
ranging from 0.5 to 0.8**. For the more difficult task of differentiating between all four relevance
classes, a mediocre agreement ranging from **0.3 to 0.6** is measured."

So in FiRA, **location agreement is roughly as good as binary presence agreement**, and *better* than
graded presence agreement. That is the opposite ordering to ours, and we must address it head-on.
Three things blunt it, all checked in the PDF:

1. κ is computed **between each annotator and the majority aggregate that includes them**, not
   pairwise — a construction that systematically inflates agreement relative to pairwise κ.
2. **n = 10 query–document pairs** for the whole IAA analysis.
3. They "only annotated documents judged to be **overall relevant** by TREC", so the presence
   question was largely pre-settled — which is precisely the conditioning that makes location the
   only live variable, and removes the presence/location contrast.

They also state "We rarely see a full agreement of all judges", and Figure 6 presents "the
distribution of fine-grained word-level annotations on two document snippets" — i.e. **a graded
per-word support profile pooled over annotators, in IR, in 2020** — with the narrative observation
that on one snippet "most annotators agree on two sentences, whereas in the first snippet we see a
greater variability".

- Who disagrees: **between** annotators (students, 3-way majority).
- Unit: **word-level spans plus passage-level grades** — both, explicitly.
- Gold: majority-voted class with a "take the highest relevance class" tie-break heuristic; the
  per-word distribution is shown but not made the gold.
- Statistic: **Cohen's κ, computed separately for word selection, 2-class and 4-class.**

**Weight** — **highest in this section for the IR framing, and a genuine complication.** Someone has
already measured location agreement separately from presence agreement on TREC data and found
location *not* worse. Our contrary result needs the differences stated: repeated readings of one
model vs different people; pairwise reproduction vs κ-against-own-majority; unrestricted pairs vs
pre-filtered relevant documents; 10 pairs vs our sample.

### `mcdonnell-2016` and `kutlu-2020`

- **Why Is That Relevant? Collecting Annotator Rationales for Relevance Judgments** —
  Tyler McDonnell, Matthew Lease, Mucahid Kutlu, Tamer Elsayed, *Proceedings of the AAAI Conference
  on Human Computation and Crowdsourcing* (HCOMP) 4:139–148, 2016, DOI `10.1609/hcomp.v4i1.13287`.
  **verified** (Crossref).
- **The Many Benefits of Annotator Rationales for Relevance Judgments** — Tyler McDonnell,
  Mucahid Kutlu, Tamer Elsayed, Matthew Lease, IJCAI 2017, pp. 4909–4913,
  DOI `10.24963/ijcai.2017/692`. **verified** (Crossref). Note the author order differs from the
  HCOMP paper.
- **Annotator Rationales for Labeling Tasks in Crowdsourcing** — Mucahid Kutlu, Tyler McDonnell,
  Matthew Lease, Tamer Elsayed, *Journal of Artificial Intelligence Research* 69:143–189, 2020,
  DOI `10.1613/jair.1.12012`. **verified** (Crossref). The extended journal treatment.

Requires crowd assessors to highlight the text passage that justifies each relevance judgment. The
rationale is used as a quality signal and to explain disagreement — i.e. location is collected
alongside presence, in IR, on relevance judgments.

**Weight** — high; the IR-side precedent for "collect the location too", and the obvious place a
reviewer will point.

### `deyoung-2020` (ERASER)

**ERASER: A Benchmark to Evaluate Rationalized NLP Models**
Jay DeYoung, Sarthak Jain, Nazneen Fatema Rajani, Eric Lehman, Caiming Xiong, Richard Socher,
Byron C. Wallace.
ACL 2020, pp. 4443–4458, DOI `10.18653/v1/2020.acl-main.408`.
https://aclanthology.org/2020.acl-main.408/
**verified** (Crossref).

The benchmark that standardised rationale evaluation: IOU-F1, token-F1, AUPRC (plausibility) and
comprehensiveness/sufficiency (faithfulness). Also the source of the **soft-scoring** track — AUPRC
over continuous token scores — which is the metric family that admits a graded rationale at all.

- Unit: **spans**. Gold: hard rationale spans, with a soft-score evaluation path.
- Statistic: IOU-F1 (thresholded Jaccard), token-F1, AUPRC.

**Weight** — high; the metric vocabulary everyone in this area uses. Our 0.47 span-set reproduction
number should be expressed in a statistic from this family so it is comparable.

Two things in the ERASER table matter more than the means. **κ is computed per token as the mean
agreement of each annotator with the majority vote**, not pairwise — annotator-vs-consensus, which is
systematically higher, and it is exactly the discretising workaround `mathet-2015` §3.4.1 refutes.
And the standard deviations are enormous: CoS-E 0.619 **±0.308**, BoolQ 0.618 **±0.194**, e-SNLI
0.743 ±0.162, FEVER 0.854 ±0.196 (over just **24 documents**). Per-document rationale agreement is
all over the map. Evidence Inference has **no agreement figure at all** — "we would expect agreement
to be high, but have not collected redundant comprehensive annotations."

### `zaidan-2007` — ★ the earliest clean where-vs-whether design

**Using "Annotator Rationales" to Improve Machine Learning for Text Categorization**
Omar F. Zaidan, Jason Eisner, Christine Piatko.
NAACL-HLT 2007, pp. 260–267.
https://aclanthology.org/N07-1033/
**verified** (ACL Anthology) · **read** (ACL PDF).

⚠️ **Twenty years old, and it already does the conditioning.** Four annotators, 150 movie reviews.
Class agreement is high — accuracies 92–97% against the Pang & Lee labels, **4-way agreement on the
class for 89% of documents**. Rationale agreement is then reported **separately**, with the caption
stating: *"In computing pairwise agreement on rationales, we ignored documents where A_i and A_j
disagreed on the class."*

Rationale agreement is deliberately **not** κ but an asymmetric overlap rate ("% of A_i's rationales
that at least one of A_j's rationales overlaps"), because annotator thoroughness varies enormously:
A1 marked 5.02 rationales/doc (91.4% also marked by someone else), A2 10.14 (80.9%), AX 6.52
(90.9%), AY 11.36 (75.5%). Pairwise cells range **39.7%–80.1%**. Note the **2.3× spread in how much
text annotators highlight for the same documents** — the thorough annotator has high "recall", the
sparse one high "precision". They also flag cases where the same span was highlighted by one
annotator as support for *positive* and by another as support for *negative*.

- Who disagrees: **between** annotators. (e) **Span** (substrings). (f) **Yes, explicitly** — class
  agreement and rationale agreement are separate numbers, the latter conditioned on the former.
  (g) No graded gold. (h) Overlap-based asymmetric precision/recall, chosen *because* κ misbehaves.

**Weight** — **high.** The cleanest precedent for the design, two decades old and essentially never
followed up. Cite it first in the related-work paragraph.

### `sen-2020` (YELP-HAT)

**Human Attention Maps for Text Classification: Do Humans and Neural Networks Focus on the Same
Words?**
Cansu Sen, Thomas Hartvigsen, Biao Yin, Xiangnan Kong, Elke Rundensteiner.
ACL 2020, pp. 4596–4608, DOI `10.18653/v1/2020.acl-main.419`.
https://aclanthology.org/2020.acl-main.419/
**verified** (ACL Anthology) · **read** (ACL PDF).

⚠️ **The only NLP paper found that makes pooled span operators first-class objects.** Three
annotators per review highlight sentiment-bearing words; they define **CAM (Consensus Attention Map)
= bitwise AND** of the three maps and **SAM (Super Attention Map) = bitwise OR**.

The numbers are the point, because the *label* is near-unanimous (human classification accuracy
0.94–0.96) while the *spans* are not. Yelp-50: annotators select k = 10, 12, 12 words each;
**CAM = 5, SAM = 22**. Yelp-200: k = 26, 27, 25; CAM = 11, SAM = 45. So **only ~40–50% of a given
annotator's highlighted words survive unanimity, and the union is roughly double any individual's
selection.** Human-to-human "behavioral similarity" 0.69–0.75 on a scale where 0.5 = no similarity,
declining with document length.

- Who disagrees: between annotators. Unit: token. Presence agreed, location not — reported as both.
- Gold: AND/OR pooling, i.e. a **two-point** graded scale, not a continuous score; no model fitted.
- Statistic: overlap-based "behavioral similarity", no chance correction beyond the 0.5 floor.

**Weight** — **high.** This is the between-annotator version of "union vs intersection are very
different objects and neither is the annotation", with concrete counts. Our span-set *union* is
precisely their SAM.

### `cheng-2023` (MDACE)

**MDACE: MIMIC Documents Annotated with Code Evidence**
Hua Cheng, Rana Jafari, April Russell, Russell Klopfer, Edmond Lu, Benjamin Striner,
Matthew R. Gormley. ACL 2023; arXiv:2307.03859.
https://arxiv.org/abs/2307.03859
**verified** (arXiv) · **read** (PDF).

Professional medical coders annotate evidence spans for ICD codes. Krippendorff's α chosen
*"as it allows for assigning multiple labels to a span"*. Initial α **0.53** (Inpatient) / **0.24**
(Profee); after adjudication **0.97 / 0.96**.

⚠️ **The revealing detail:** the stated first source of low initial agreement is that *"the coders
annotated the same or similar evidence from different locations in the same chart"* — pure *where*
disagreement with no *whether* disagreement — and adjudication resolves such cases **as agreements**.
The phenomenon we are measuring is here made to disappear by a convention, in a paragraph.

**Weight** — medium-high; a vivid one-sentence illustration that the field's standard practice
*erases* location disagreement rather than measuring it.

### `hong-2025` (LiTEx) and `jiang-2023`

- **LiTEx: A Linguistic Taxonomy of Explanations for Understanding Within-Label Variation in Natural
  Language Inference** — Pingjun Hong, Beiduo Chen, Siyao Peng, Marie-Catherine de Marneffe,
  Barbara Plank, EMNLP 2025, pp. 34053–34073, DOI `10.18653/v1/2025.emnlp-main.1728`.
  **verified** (Crossref) · **read** (ACL PDF).
- **Ecologically Valid Explanations for Label Variation in NLI** — Nan-Jiang Jiang, Chenhao Tan,
  Marie-Catherine de Marneffe, Findings of EMNLP 2023, pp. 10622–10633,
  DOI `10.18653/v1/2023.findings-emnlp.712`. **verified** (Crossref) · **read** (ACL PDF).
- A 2026 successor exists: **Agree, Disagree, Explain: Decomposing Human Label Variation in NLI
  through the Lens of Explanations** — Pingjun Hong, Beiduo Chen, Siyao Peng,
  Marie-Catherine de Marneffe, Benjamin Roth, Barbara Plank, Findings of ACL 2026, pp. 26922–26934,
  DOI `10.18653/v1/2026.findings-acl.1342`. **verified** (Crossref). Not read.

These establish the term **within-label variation**: annotators pick the *same* label for *different*
reasons. Jiang et al. built LIVENLI (1,415 free-text explanations over 122 MNLI items, ≥10 per item)
and report that explanations "confirm that people can systematically vary on their interpretation and
highlight within-label variation". Hong et al. quantify it with a taxonomy: **613 of 1,002 items
(61.2%) received more than one taxonomy category across explanations** despite a shared label, and
they note that "the same spans on the NLI item can be highlighted for different reasons".

- Who disagrees: **between** annotators.
- Unit: free-text explanation (LiTEx also relates it to highlighted spans).
- Gold: a taxonomy category distribution; not a graded per-unit score.
- Statistic: inter-annotator agreement on the taxonomy; similarity measures (n-gram, POS-n-gram,
  cosine, BLEU, ROUGE-L) between explanations.

**Weight** — **high for terminology.** "Within-label variation" is the existing name for "agreement
on the verdict, disagreement on the reasoning", and we should use it rather than coin something. Note
the difference: their "reasoning" is free text, ours is *location*, which is measurable without
paraphrase-matching — that is an advantage worth stating.

---

## 5b. Evidence sets in fact verification and attributed QA — where the field *already* reports presence and location with two different statistics

### `thorne-2018` (FEVER) — ★★

**FEVER: a Large-scale Dataset for Fact Extraction and VERification**
James Thorne, Andreas Vlachos, Christos Christodoulopoulos, Arpit Mittal.
NAACL-HLT 2018, pp. 809–819, DOI `10.18653/v1/N18-1074`.
https://aclanthology.org/N18-1074/
**verified** (ACL Anthology BibTeX) · **read** (ACL PDF; I extracted and checked these numbers
myself).

⚠️⚠️ **FEVER separates the verdict from the evidence location, and uses two different statistics
precisely because κ has no denominator for an evidence set.** Verbatim from the PDF:

- Verdict: "inter-annotator agreement of **0.6841 in Fleiss κ** … in claim verification
  classification" (5-way agreement on a random 4% sample). Compared in-text against Bowman et al.'s
  κ = 0.7 for SNLI, "a simpler task, since the annotators were given the premise/evidence to verify a
  hypothesis against **without the additional task of finding it**."
- Evidence location: super-annotators with no time limit searched all of Wikipedia for every possible
  evidence sentence; regular annotators scored **95.42% precision and 72.36% recall** against that.
  All but two annotators achieved >90% precision; all but nine >70% recall.
- The author validation is the sharpest line: of 227 examples, "most of the examples that were
  annotated incorrectly were cases where **the label was correct, but the evidence selected was not
  sufficient** (only 4 out of 227 examples were labeled incorrectly)."
- The system error analysis found the pipeline retrieved **new, valid evidence the annotators had
  never marked in 21.85% (n = 210) of claims**.
- The scoring rule is explicitly **disjunctive over evidence sets** — "Some claims may be equally
  supported by different pieces of evidence; in this case **one complete set** of sentences should be
  predicted" — while multi-hop claims require all sentences of a set. They concede "it is not
  feasible to ensure that the evidence selection annotations are complete."

- Who disagrees: **between** annotators (5-way for labels, vs super-annotators for evidence).
- Unit: **sentence-level evidence sets inside documents** — our unit.
- Gold: hard, disjunctive sets; no graded object.
- Statistic: **Fleiss κ for the verdict, uncorrected P/R for the evidence, never mixed.**

**Weight** — **must-cite, and among the three most important entries in this file.** It is the
canonical evidence-set benchmark; it already shows verdicts are reliable while evidence location has
~72% recall against a thorough search; and its disjunctive scoring rule is an implicit admission that
the span set is not a well-defined object. Our result is the repeated-measures, single-annotator
version of what FEVER's numbers already hint at.

### `min-2020` (AmbigQA)

**AmbigQA: Answering Ambiguous Open-domain Questions**
Sewon Min, Julian Michael, Hannaneh Hajishirzi, Luke Zettlemoyer.
EMNLP 2020; arXiv:2004.10645.
https://arxiv.org/abs/2004.10645
**verified** (arXiv) · **read** (PDF).

⚠️ **A clean set-valued analogue of the presence/location gap, with a 28-point number.** Two
independent annotators agree at **60.8 F1** on *which set of answers exists*, while the *validity*
judgment — "is this a valid answer" — is far more reliable: F1 **89.0** between co-authors and
workers on 50 validations, and validators passed all annotations for 76% of questions. Over 50% of
dev/test examples contain multiple QA pairs.

- Gold: a set; no graded object. Statistic: set F1, no chance correction.

**Weight** — high. "Is this a valid element" is reliable; "what is the complete set" is not — the
same shape as our result, in the answer-set rather than the span-set domain.

### `rajpurkar-2016` / `rajpurkar-2018` (SQuAD)

- **SQuAD: 100,000+ Questions for Machine Comprehension of Text** — Pranav Rajpurkar, Jian Zhang,
  Konstantin Lopyrev, Percy Liang, EMNLP 2016; arXiv:1606.05250.
- **Know What You Don't Know: Unanswerable Questions for SQuAD** — Pranav Rajpurkar, Robin Jia,
  Percy Liang, ACL 2018 (Short), pp. 784–789. https://aclanthology.org/P18-2124/

**verified** (arXiv / ACL Anthology) · **read** (both PDFs).

⚠️ **One sentence in SQuAD is the whole span-vs-label problem, and it is filed as a nuisance.**
Human performance is measured by treating a second annotator's answer as a prediction against the
others: **EM 77.0%, F1 86.8%** — i.e. two humans give the *same* answer span exactly only 77% of the
time. Their own diagnosis: *"Mismatch occurs mostly due to **inclusion/exclusion of non-essential
phrases** (e.g., monsoon trough versus movement of the monsoon trough) rather than fundamental
disagreements about the answer."* SQuAD 2.0 collects ~4.8 answers per question, resolves by majority
vote with ties broken toward answering and toward **shorter** answers, and reports human EM/F1
86.9/89.5 and 82.3/91.2.

- Gold: single span by majority vote with explicit length-biased tie-breaking.
- Statistic: EM and token F1 only. **No chance correction and no reliability coefficient anywhere in
  either paper.**

**Weight** — **high rhetorically.** The most-used span benchmark in NLP concedes a 23-point exact-
match gap between two humans on the same question and attributes it to boundary quibbles — which is
the location instability we are measuring, named and then set aside.

### `rashkin-2023` (AIS)

**Measuring Attribution in Natural Language Generation Models**
Hannah Rashkin, Vitaly Nikolaev, Matthew Lamm, Lora Aroyo, Michael Collins, Dipanjan Das,
Slav Petrov, Gaurav Singh Tomar, Iulia Turc, David Reitter.
*Computational Linguistics* 49(4):777–840, 2023, DOI `10.1162/coli_a_00486`.
**verified** (Crossref by DOI). ⚠️ A **duplicate Crossref record** exists under
DOI `10.1162/coli_a_00490` (same title, same 10 authors, "pp. 1–66", dated 2023-07-06) — that is the
Just-Accepted version. **Cite `_00486`.** I confirmed both records exist.

The AIS framework: two binary questions, *Interpretability* and *Attributable to Identified Sources*.
Table 7 reports three statistics side by side and they tell different stories — CNN/DM
Interpretability F1 .83 / pairwise agreement .80 / **Krippendorff α .46**; AIS .92/.89/**.69**;
and against expert consensus, CNN/DM Interpretability α = **−0.04, worse than chance**.

- Unit: **the whole (response, source) pair — no localisation at all.** This is the pure *whether*
  case with no *where*: the framework asks only whether the whole response is supported by the whole
  source.
- Statistic: Krippendorff's α, pairwise agreement and F1-vs-consensus, side by side.

**Weight** — **high, as the baseline our unit improves on.** The field's standard attribution
framework is document-level; the fact that AIS deliberately declines to localise is the gap our
per-sentence object fills. The F1 .83 vs α .46 contrast on the same data is also a very clean
illustration for the statistics discussion.

### `liu-2023` and `bohnet-2022`

- **Evaluating Verifiability in Generative Search Engines** — Nelson F. Liu, Tianyi Zhang,
  Percy Liang, Findings of EMNLP 2023; arXiv:2304.09848. **verified** · **read** (PDF).
  Headline: **only 51.5% of generated sentences are fully supported by their citations, and only
  74.5% of citations support their associated statement.** Annotator agreement (n = 250 pairs) is
  pairwise-% and F1-vs-majority only, **no chance correction**: Citation Supports 82.0/91.0,
  Statement Supported 82.2/91.1.
- **Attributed Question Answering: Evaluation and Modeling for Attributed Large Language Models** —
  Bernd Bohnet, Vinh Q. Tran, Pat Verga, Roee Aharoni, Daniel Andor, Livio Baldini Soares,
  Massimiliano Ciaramita, Jacob Eisenstein, Kuzman Ganchev, Jonathan Herzig, Kai Hui, Tom Kwiatkowski,
  Ji Ma, Jianmo Ni, Lierni Sestorain Saralegui, Tal Schuster, William W. Cohen, Michael Collins,
  Dipanjan Das, Donald Metzler, Slav Petrov, Kellie Webster. arXiv:2212.08037. **verified** (arXiv
  preprint; author list as printed) · **read** (PDF). Adopts AIS verbatim, scores by **majority vote
  of 5 raters**, and **reports no inter-annotator agreement coefficient at all.**

**Weight** — medium-high as a pair: attribution evaluation at scale currently runs on uncorrected
percent agreement or on nothing, at document granularity.

---

## 5c. Test-retest on spans outside NLP — it is routine in medical imaging

This is the asymmetry worth one paragraph in the paper: the design we are running is standard
practice in radiology under the names *intra-observer variability* and *intra-rater Dice*, and absent
from NLP and IR.

### `li-2010` — the only NLP test-retest on a location task

**Enriching Word Alignment with Linguistic Tags**
Xuansong Li, Niyu Ge, Stephen Grimes, Stephanie M. Strassel, Kazuaki Maeda.
LREC 2010, Valletta, pp. 2189–2194, DOI `10.63317/2ioujs4uaq4c`.
http://www.lrec-conf.org/proceedings/lrec2010/pdf/670_Paper.pdf
**verified** (Crossref + LREC landing page) · **read** (PDF).

LDC/IBM Chinese–English word alignment, 32,823 sentence pairs. Agreement is defined over **links** —
a link is a claim about *where* a correspondence sits — as precision/recall/F. Both axes are
reported on the same task, which makes them comparable: **inter**-annotator F 86.4/87.1/84.3/82.3
(junior, first round) rising to 96.5/95.7/90.8/91.2 (senior, post-QC); **intra**-annotator by
interval (Table 9) — **1 week F 98.15; 2 weeks F 97.07; 1 month F 94.51.**

- Who disagrees: **both, in the same paper.** Unit: link/location. Statistic: **P/R/F only, no chance
  correction, no reliability coefficient.**

**Weight** — **high.** The nearest NLP neighbour to our design, and it shows intra > inter on a
location task (94.5–98.2 vs 90.8–96.5 post-QC) — a result we should expect and may need to explain,
since it cuts against a naive "the model is less self-consistent than people are with each other".
It also shows self-consistency **degrades monotonically with the interval**, which has no analogue
for a stateless model and is worth noting as a disanalogy.

### `covert-2022`

**Intra- and inter-operator variability in MRI-based manual segmentation of HCC lesions and its
impact on dosimetry**
Elise C. Covert, Kellen Fitzpatrick, Justin Mikell, Ravi K. Kaza, John D. Millet, Daniel Barkmeier,
Joseph Gemmete, Jared Christensen, Matthew J. Schipper, Yuni K. Dewaraja.
*EJNMMI Physics* 9(1):90, 2022, DOI `10.1186/s40658-022-00515-6`.
https://pmc.ncbi.nlm.nih.gov/articles/PMC9772368/
**verified** (Crossref + PMC full text).

⚠️ **The design template, done properly, in another field.** Three board-certified radiologists,
20 lesions; two of the three each did **3 contouring sessions separated by 1-month intervals**;
140 observations, 7 reads per lesion. **Intra-observer Dice 0.85 (SE 0.006) vs inter-observer Dice
0.79 (SE 0.009), p < 0.001.** ICCs reported for the downstream quantity too: volume ICC 0.992 intra /
0.967 inter; mean dose 0.995 / 0.987.

**Weight** — **high.** Cite it for "this design is standard elsewhere" and for the ICC-on-the-derived-
quantity move, which is structurally what we do with the graded score.

### `abhishek-2025` and `ribeiro-2019`

- **What Can We Learn from Inter-Annotator Variability in Skin Lesion Segmentation?** —
  Kumar Abhishek, Jeremy Kawahara, Ghassan Hamarneh, MICCAI ISIC Skin Image Analysis Workshop 2025;
  arXiv:2508.09381. **verified** (arXiv) · **read** (PDF). 2,394 images, **5,111 masks, 15
  annotators**. Explicit methodological statement: *"Although previous IAA studies have used Cohen's
  kappa and Fleiss' kappa, these metrics measure categorical agreement and **fail to capture spatial
  overlap** between annotations; thus, we adopt the Dice metric."* Confirms **"intra-annotator
  agreement is significantly higher than inter-annotator agreement"**. And — the part most useful to
  us — they train a model to **predict a lesion's Dice-IAA from the image** and use IAA prediction as
  an auxiliary task that improves diagnosis. That is the strongest published form of "disagreement
  about *where* is itself signal".
- **Handling Inter-Annotator Agreement for Automated Skin Lesion Segmentation** — Vinicius Ribeiro,
  Sandra Avila, Eduardo Valle, ISIC Skin Image Analysis Workshop 2019; arXiv:1906.02415.
  **verified** (arXiv) · **read** (PDF). Per-pixel Cohen's κ, median ~0.72–0.75; shows that
  "conditioning" the gold (morphological opening/closing, convex hull) substantially raises agreement
  without changing the ground truth much, while bounding boxes make it worse. Note per-pixel κ is
  legitimate here **only because an image bounds the negative class** — exactly the condition
  `hripcsak-2005` says text spans lack.

**Weight** — `abhishek-2025` high; `ribeiro-2019` medium-high (agreement is a function of the
granularity you demand, which is the argument a graded gold rests on).

### `warfield-2004` (STAPLE) — ★ the graded pooled gold, for images, since 2004

**Simultaneous Truth and Performance Level Estimation (STAPLE): An Algorithm for the Validation of
Image Segmentation**
Simon K. Warfield, Kelly H. Zou, William M. Wells.
*IEEE Transactions on Medical Imaging* 23(7):903–921, 2004, DOI `10.1109/TMI.2004.828354`.
**verified** (Crossref by DOI). ⚠️ **Not read** — IEEE paywall. The mechanism description below is
second-hand, from `abhishek-2025` and `ribeiro-2019` which cite it, and should be checked before we
characterise it in print.

An EM algorithm that jointly estimates a **probabilistic ("soft") reference segmentation** and each
rater's sensitivity/specificity from a collection of segmentations, instead of taking a majority
vote.

**Weight** — **high, and the sharpest framing available for the graded object.** "Pool several
annotations of *where* into a probabilistic per-unit truth, with rater quality modelled" is 22 years
old in medical imaging and has **no text-span equivalent**. It is both the best analogy to cite and
the reason to state our text-side contribution carefully.

### `titeux-2021` (pygamma-agreement)

**pygamma-agreement: Gamma (γ) measure for inter/intra-annotator agreement in Python**
Hadrien Titeux, Rachid Riad. *JOSS* 6(62):2989, 2021, DOI `10.21105/joss.02989`.
**verified** (Crossref) · **read** (5 pp).

⚠️ The title advertises **"inter/intra-annotator"** agreement, but the body only ever discusses
inter-rater use. The tooling to run γ and γ_cat on a test-retest span design exists and is packaged;
nobody has published the study.

**Weight** — medium, but a nice concrete line: the instrument is on the shelf, unused.

---

## 6. Graded / pooled gold objects that already exist

### `nenkova-2004` / `nenkova-2007` (the Pyramid method)

- **Evaluating Content Selection in Summarization: The Pyramid Method** — Ani Nenkova,
  Rebecca Passonneau, HLT-NAACL 2004, pp. 145–152.
  https://aclanthology.org/N04-1019/ **verified** (ACL Anthology BibTeX).
- **The Pyramid Method: Incorporating Human Content Selection Variation in Summarization Evaluation**
  — Ani Nenkova, Rebecca Passonneau, Kathleen McKeown, *ACM Transactions on Speech and Language
  Processing* 4(2), Article 4, 2007, DOI `10.1145/1233912.1233913`. **verified** (Crossref record
  fetched by DOI; the `subtitle` field confirms the full title, which the bare `title` field
  truncates to "The Pyramid Method").

⚠️ **The oldest and strongest precedent for "the gold is a graded object pooled over readings".**
Content units (SCUs) are identified in multiple human summaries and each SCU is **weighted by how
many of the humans expressed it**; the score of a candidate is computed against that weighted
pyramid rather than against any single reference. The whole point is that no single reference is the
truth and the stable object is the *weight*.

- Who disagrees: **between** annotators/summarisers.
- Unit: a **content unit located in text** — so both presence and (loosely) location.
- Gold: **graded**, by pooled count. Explicitly so.
- Statistic: pyramid score; the reliability question they ask is how many reference summaries are
  needed for the ranking to stabilise.

**Weight** — **must-cite, and the best single "someone already did the graded-pooled-gold move"
reference.** The difference to state: pyramid weights pool across *different people writing different
summaries*; and the pyramid's stability question is about the *score*, not about a reliability
coefficient on the graded object per se.

### `lin-2006`

**Will pyramids built of nuggets topple over?**
Jimmy Lin, Dina Demner-Fushman.
HLT-NAACL 2006, pp. 383–390, DOI `10.3115/1220835.1220884`.
https://aclanthology.org/N06-1049/
**verified** (Crossref).

Carries the pyramid idea into TREC QA nugget evaluation: nugget "vitality" is a binary assessor
call that is known to be unstable, so replace it with a pooled weight from multiple assessors and
ask whether system rankings survive.

**Weight** — high. This is *the* IR-side instance of "the discrete binary judgment was unstable, so
we replaced it with a graded pooled one" — the exact shape of our contribution, in 2006, on nuggets.
Cite it in the framing paragraph.

### `voorhees-2003-qa`

**Overview of the TREC 2003 Question Answering Track**
Ellen M. Voorhees. NIST Special Publication 500-255, 2003, DOI `10.6028/NIST.SP.500-255.qa-overview`.
**verified** (Crossref).

Background for the nugget-assessment instability that `lin-2006` responds to.

**Weight** — low-medium; cite only as the source of the nugget protocol.

### `wong-2022` (k-rater reliability)

**k-Rater Reliability: The Correct Unit of Reliability for Aggregated Human Annotations**
Ka Wong, Praveen Paritosh.
arXiv:2203.12913, 24 Mar 2022.
https://arxiv.org/abs/2203.12913
**verified** (arXiv abs page) · **read** (arXiv PDF v1).

⚠️ **This is the established name and the established machinery for exactly the quantity we report.**

The argument: when a dataset's labels are *aggregates* of k ratings, reporting inter-rater
reliability (IRR, the reliability of a single rating) is the wrong unit of analysis and understates
the data's reliability. They propose **k-rater reliability (kRR)**, computed as ICC(k), and note that
the **Spearman–Brown prophecy formula** gives ICC(k) from ICC(1):

> ICC(k) = k · ICC(1) / (1 + (k − 1) · ICC(1))

citing Warrens (2017) and de Vet et al. (2017) for the proof that Spearman–Brown and ICC(k) are
equivalent in expectation. Their empirical demonstration, on two fresh replications of WordSim-353:
**ICC(1) = 0.590 → ICC(13) = 0.950** analytically, 0.953 by bootstrap.

- Who disagrees: **between** raters (crowd).
- Unit: item-level continuous rating (word-pair similarity).
- Gold: **graded, and explicitly an aggregate whose reliability is the thing being reported.**
- Statistic: **ICC(1), ICC(k), Spearman–Brown**; bootstrap and empirical-replication estimators.

**Weight** — **highest for framing.** It supplies (a) the name *k-rater reliability*, (b) the
justification for quoting a Spearman–Brown figure at all, and (c) a published precedent of the same
shape as our result — a single reading only moderately reliable, the k-fold aggregate highly
reliable. Cite it the first time the Spearman–Brown number appears.

### `lalor-2016`

**Building an Evaluation Scale using Item Response Theory**
John P. Lalor, Hao Wu, Hong Yu.
EMNLP 2016. **verified** (Crossref; note Crossref renders the third author's name in lower case as
"hong yu" — the correct form is Hong Yu).

Item response theory applied to NLP evaluation: items get difficulty/discrimination parameters
estimated from many human responses, rather than being treated as equally informative.

**Weight** — medium; another psychometrics-in-NLP precedent to cite alongside `wong-2022`.

---

## 7. Reliability of LLM-produced annotations

### 7a. Repeated sampling from one model — the closest prior art we have

### `reiss-2023`

**Testing the Reliability of ChatGPT for Text Annotation and Classification: A Cautionary Remark**
Michael V. Reiss.
arXiv:2304.11085 (17 Apr 2023); OSF preprint DOI `10.31219/osf.io/rvy5p`.
https://arxiv.org/abs/2304.11085
**verified** (arXiv + Crossref OSF record) · **read** (arXiv PDF, I re-extracted and checked every
number below myself).

⚠️ **The single closest prior art in this whole survey to "repeat the model, pool the readings,
report a reliability coefficient".**

234 website texts, binary news/not-news, 10 instruction paraphrases × 10 repetitions × 2 temperatures
(0.25 and 1.0) = 46,800 classifications on `gpt-3.5-turbo`. Verbatim from the PDF:

- "comparing the classification output of each prompt for both temperature settings under the first
  (no pooling) regime (i), the consistency between both sets of output is **α = 0.75**, failing
  Krippendorff's recommendation for reasonable reliabilities"; "when pooling classification outputs
  of **ten repetitions** and taking the majority output for each temperature setting, consistency
  increases to **α = 0.91**."
- Prompt-paraphrase variation, all 45 pairs of the ten instructions: "**min α = 0.24, mean α = 0.43,
  max α = 0.7** for classification regime (i)" — the wording changes were as small as "classify"
  vs. "rate". **Between-prompt variance dwarfs within-prompt variance.**
- A separate figure reports within-identical-prompt repetition consistency (n = 2340), higher at the
  lower temperature.

Precision note I checked because it is easy to over-claim: the headline 0.75 → 0.91 is agreement
*between the two temperature settings*, with ten repeats pooled inside each — not a pure
same-configuration test-retest coefficient. The pure repetition arm is reported separately.

- Who disagrees: **within one model, repeated** (primary design), plus a between-prompt arm.
- Unit: **whole document**, binary.
- Gold: pooled to a **hard majority label**, not a graded one — the pooling is a denoising step, not
  a new gold object.
- Statistic: **Krippendorff's α** against the 0.80 threshold.

**Weight** — **highest in this section and a direct threat.** "Repeat the LLM, pool, and watch
reliability rise past threshold" is published, with α, in 2023. What is still open: pooling into a
**graded** object rather than a majority label, and doing it on **location** rather than a
document-level binary.

### `barrie-2024` (Prompt Stability Score)

**Prompt Stability Scoring for Text Annotation with Large Language Models**
Christopher Barrie, Elli Palaiologou, Petter Törnberg.
arXiv:2407.02039 (v1 2 Jul 2024).
https://arxiv.org/abs/2407.02039
**verified** (arXiv API, full author list).

⚠️ **Author correction against the brief:** this is **Barrie, Palaiologou & Törnberg** — *not*
"Barrie/Palmer/Spirling". Arthur Spirling is not an author. Do not cite it the other way.

Formalises two coefficients over Krippendorff's α: **intra-PSS**, α over **30 repetitions of an
identical prompt** on the same items, and **inter-PSS**, α across PEGASUS-generated paraphrases at
varying temperatures. Six datasets; GPT-3.5-turbo primary. Intra-PSS generally > 0.8; inter-PSS
degrades sharply with paraphrase temperature and is worst for loosely-defined constructs. Shipped as
a Python package.

- Who disagrees: **both, deliberately separated and named** — the only paper here that does so as its
  contribution.
- Unit: **document**. No span, no location.
- Gold: none proposed; this scores the *instrument*, not the data.
- Statistic: **Krippendorff's α**.

**Weight** — **high.** If we name our within-model repeated-reading axis, "intra-" prompt/annotator
stability already has a name and a coefficient. Cite it rather than coin a term. Its 30 repetitions
is also the same order as our thirty readings — a good precedent for the design, not just the idea.

### `stureborg-2024`

**Large Language Models are Inconsistent and Biased Evaluators**
Rickard Stureborg, Dimitris Alikaniotis, Yoshi Suhara.
arXiv:2405.01724 (2 May 2024).
https://arxiv.org/abs/2405.01724
**verified** (arXiv) · **read** (arXiv PDF; Tables 4 and 7 extracted and checked myself).

Compares an LLM's self-consistency across repeated samples directly against human inter-annotator
agreement on SummEval. Table 4: human inter-annotator **α = 0.659**, GPT-4 inter-sample
**α = 0.587**, and the paper states "the self-consistency is worse than the consistency between
multiple human annotators."

⚠️ **Baseline check — the headline is an average that hides a reversal.** Table 7 gives the four
dimensions: Coherence human 0.559 / GPT-4 **0.646**; Consistency 0.899 / 0.630; Fluency 0.726 /
0.484; Relevance 0.453 / **0.589**. So GPT-4 is *more* self-consistent than humans are with each
other on **two of four dimensions** (coherence and relevance), and much less on the two where humans
agree strongly. "LLM self-consistency is worse than human agreement" is true only on the mean.
If we cite this, cite the per-dimension table, not the average.

Also in the paper, and relevant to any multi-criterion design: successive attribute scores within one
generation correlate at Pearson **r = 0.979** for GPT-4 vs **r = 0.315** for humans — the model's
"four independent ratings" are close to one rating repeated (anchoring).

- Who disagrees: **within-model repeated** ("inter-sample", N=10) *and* between (vs humans), in the
  same table — the comparison we want to make.
- Unit: summary-level rating. No location.
- Gold: no graded pooled gold proposed.
- Statistic: **Krippendorff's α**, Kendall τ.

**Weight** — **high.** This is the template for the comparison "model test-retest vs human
inter-rater, same coefficient, same table". And the r = 0.979 anchoring result is a caution for any
scheme that reads several judgments out of one generation.

### `atil-2024`

**Non-Determinism of "Deterministic" LLM Settings**
Berk Atil, Sarp Aykent, Alexa Chittams, Lisheng Fu, Rebecca J. Passonneau, Evan Radcliffe,
Guru Rajan Rajagopal, Adam Sloan, Tomasz Tudrej, Ferhan Ture, Zhe Wu, Lixinyu Xu, Breck Baldwin.
arXiv:2408.04667 (v1 6 Aug 2024; v5 2 Apr 2025).
https://arxiv.org/abs/2408.04667
**verified** (arXiv API, 13-author list).

⚠️ **Title correction against the brief:** "LLM Stability: A detailed analysis with some surprises"
returns **nothing** on arXiv. The real paper is the one above.

10 runs per input at **temperature 0, top-p 1, fixed seed**, over 5 models and 8 MMLU/BBH tasks.
Defines **TARr@N** (all N raw strings identical) and **TARa@N** (all N parsed answers identical).
Accuracy spreads up to 15 points across runs; GPT-4o college math TARr@10 = 50%, TARa@10 = 76%.
No model delivered repeatable accuracy across all tasks at nominally deterministic settings.

- Who disagrees: **within-model repeated** — this is the whole paper.
- Unit: question/item.
- Gold: none.
- Statistic: **raw agreement rates only** — no chance correction, no psychometrics. That absence is
  precisely the gap `wong-2022`-style coefficients fill.

**Weight** — **high; this is the citation for "temperature 0 is not deterministic".** Do not assert
that from first principles.

### `he-2025` (why temperature 0 is not deterministic)

**Defeating Nondeterminism in LLM Inference**
Horace He and collaborators, Thinking Machines Lab, 10 September 2025.
https://thinkingmachines.ai/blog/defeating-nondeterminism-in-llm-inference/
**verified** (post fetched). A lab blog post, not peer-reviewed — cite as such.

⚠️ **Corrects the explanation we would otherwise have written.** The common "concurrency plus
floating-point non-associativity" story is *incomplete*: individual GPU kernels are run-to-run
deterministic. The actual cause is that kernels are not **batch-invariant** — reduction order depends
on batch size, and server-side batch size varies with load. Experiment: 1,000 identical temperature-0
requests to Qwen3-235B-A22B-Instruct-2507 produced **80 unique completions**, the most common
appearing 78 times, first divergence at **token 103**; with batch-invariant kernels all 1,000 were
identical, at ~2.1× latency.

**Weight** — medium-high, and **load-bearing for one sentence of our methods**: if we say why
repeated temperature-0 readings differ at all, this is the current correct mechanism, and a reviewer
who knows it will catch the float-non-associativity hand-wave.

### `ouyang-2023`

**An Empirical Study of the Non-determinism of ChatGPT in Code Generation**
Shuyin Ouyang, Jie M. Zhang, Mark Harman, Meng Wang.
arXiv:2308.02828 (5 Aug 2023).
https://arxiv.org/abs/2308.02828
**verified** (arXiv API).

Five requests per prompt at temperatures 0, 0.5, 1.0. Only 24.24%–52.44% of tasks gave equal test
outputs across all five runs; **at temperature 0, 18.29%–43.64% of problems still had zero output
equivalence across requests.**

**Weight** — medium; a second, earlier citation for the same "temperature 0 ≠ deterministic" point,
in a different domain.

### `song-2024`

**The Good, The Bad, and The Greedy: Evaluation of LLMs Should Not Ignore Non-Determinism**
Yifan Song, Guoyin Wang, Sujian Li, Bill Yuchen Lin.
arXiv:2407.10457 (15 Jul 2024).
https://arxiv.org/abs/2407.10457
**verified** (arXiv API).

Repeated sampling at 16–128 samples per item; greedy-vs-mean-of-samples gap exceeds 10 points on
GSM8K and HumanEval. Argues evaluation should report standard deviations, not point estimates.

**Weight** — medium. Note this is about LLM *evaluation* generally, not judge self-consistency
specifically; see COULD NOT VERIFY for the judge-specific paper the brief asked for.

### `messing-2026`

**Hidden Measurement Error in LLM Pipelines Distorts Annotation, Evaluation, and Benchmarking**
Solomon Messing.
arXiv:2604.11581 (submitted 13 Apr 2026, revised 13 May 2026).
https://arxiv.org/abs/2604.11581
**verified** (arXiv API + abs page). Not read in full.

Decomposes LLM-pipeline uncertainty into judge-model, temperature and prompt-phrasing components and
defines a "Total Evaluation Error". Reports that naive standard errors are 40–60% smaller than
TEE-corrected ones, and — the striking bit — that naive 95% CI coverage *degrades as n grows*,
because the dominant error is systematic rather than sampling.

- Statistic: variance components and CI coverage. It does **not** use ICC, Cronbach's α or formal
  G-theory names, but the framing is generalizability theory in all but vocabulary.

**Weight** — medium-high for framing; it is the LLM-era analogue of `urbano-2013` and worth one
sentence. Preprint, 2026, unreviewed — flag as such.

### 7b. LLM relevance judgment in IR

The uniform pattern across this whole subsection: **weak per-label agreement with humans, strong
leaderboard agreement** — and **every one of these runs the judge exactly once per query–document
pair**, almost always at temperature 0, then reports agreement as if the label were a fixed quantity.

### `faggioli-2023`

**Perspectives on Large Language Models for Relevance Judgment**
Guglielmo Faggioli, Laura Dietz, Charles L. A. Clarke, Gianluca Demartini, Matthias Hagen,
Claudia Hauff, Noriko Kando, Evangelos Kanoulas, Martin Potthast, Benno Stein, Henning Wachsmuth.
ICTIR '23, pp. 39–50, DOI `10.1145/3578337.3605136`; arXiv:2304.09161.
https://doi.org/10.1145/3578337.3605136
**verified** (Crossref + arXiv + full text).

The position paper with the four-point human/machine spectrum (Human Judgment → AI Assistance →
Human Verification → Fully Automated). Its pilot: GPT-3.5 vs human assessors, Cohen's κ = 0.38 on
TREC-8 and 0.40–0.49 on TREC-DL 2021; binarised κ = 0.26 on the TREC 2021 DL re-judging. Asymmetric
error — ~90% agreement on non-relevant documents but only ~47% on relevant ones.

⚠️ **Fabrication risk flagged during verification:** the paper contains **no community survey with
respondent counts**. It is opposing essays by the co-authors ("In Favor", "Against", "A Compromise").
If any draft attributes a survey percentage to it, that is invented.

**Weight** — high; the framing citation for the whole area, plus a real κ.

### `thomas-2024`

**Large Language Models can Accurately Predict Searcher Preferences**
Paul Thomas, Seth Spielman, Nick Craswell, Bhaskar Mitra.
SIGIR 2024, pp. 1930–1940, DOI `10.1145/3626772.3657707`; arXiv:2309.10621.
https://doi.org/10.1145/3626772.3657707
**verified** (Crossref + arXiv + full text).

⚠️ **Read as a variance result, this is one of the most useful papers in the survey.** GPT-4 at
**temperature 0, top-p 1**: across **32 prompt variants κ ranged 0.20–0.64**; then, taking the best
prompt and generating **42 semantically-equivalent paraphrases**, mean κ ranged **0.50–0.72**. So
*paraphrasing a fixed prompt moves κ by ~0.22 at temperature 0*. Prompt-feature effects on κ:
aspects +0.21, narrative +0.06, description +0.01, role −0.04, multiple judges −0.13. 90 unparseable
outputs in 96,000 calls.

⚠️ **Baseline check on the headline.** "As accurate as human labellers" is against **third-party
crowd labellers**, with the gold being first-party feedback from the actual searchers — a fair
comparison on that axis, but not against expert assessors. The deployment claim (+28% accuracy vs
typical crowd workers at Bing) is against the same crowd baseline.

- Who disagrees: between (LLM vs humans). The 42-paraphrase sweep is **between-prompt**, *not*
  within-prompt test-retest — they do not repeat an identical prompt.
- Unit: document/search result. Gold: single graded label. Statistic: Cohen's κ, MAE, AUC, RBO.

**Weight** — **high.** Best available evidence that at temperature 0 the *prompt*, not the sampling,
is the dominant variance source — which is a question a reviewer will ask about our thirty readings.

### `upadhyay-2024` (UMBRELA) and `upadhyay-2025`

- **UMBRELA: UMbrela is the (Open-Source Reproduction of the) Bing RELevance Assessor** —
  Shivani Upadhyay, Ronak Pradeep, Nandan Thakur, Nick Craswell, Jimmy Lin, arXiv:2406.06519.
  **verified** (arXiv + full text). GPT-4o, 0–3, temperature 0: four-point Cohen's κ
  **0.308–0.373** ("fair"), binarised κ 0.418–0.499, but system-ranking Kendall τ **0.873–0.944** and
  Spearman ρ 0.973–0.992.
- **A Large-Scale Study of Relevance Assessments with Large Language Models** — Shivani Upadhyay,
  Ronak Pradeep, Nandan Thakur, Daniel Campos, Nick Craswell, Ian Soboroff, Jimmy Lin, ICTIR '25,
  pp. 358–368, DOI `10.1145/3731120.3744605`. **verified** (Crossref). ⚠️ The earlier arXiv version
  (arXiv:2411.08275, *…: An Initial Look*) has a **different author list** — use the Crossref list
  for the published paper. TREC 2024 RAG: τ 0.867–0.890 at nDCG@20, 0.922–0.944 at nDCG@100; the LLM
  is systematically **more generous** (384 passages called "perfectly relevant" that humans graded
  lower, vs 263 agreements on that label).

**Weight** — high; the canonical "weak κ, strong τ" numbers.

### `clarke-2024`

**LLM-based relevance assessment still can't replace human relevance assessment**
Charles L. A. Clarke, Laura Dietz.
arXiv:2412.17156 (22 Dec 2024).
https://arxiv.org/abs/2412.17156
**verified** (arXiv + full text).

The rebuttal, and the one with teeth. Overall τ = 0.89 over 75 systems, but **τ = 0.51 over the top
20** (24% of pairs swap) and 0.56 over the top 15. A deliberately-crafted exploit run ranked **5th
under UMBRELA and 28th under NIST qrels**. In a circularity simulation (all systems adopt UMBRELA as
re-ranker, then get judged by UMBRELA) top-10 τ falls to 0.38 and **top-5 τ = −0.40**.

**Weight** — **high.** The correct citation for "a high aggregate τ conceals failure exactly where
the decisions are made" — which is also the right caution about reading `voorhees-2000` and
`parry-2025` too comfortably.

### `rahmani-2024-llmjudge` and `rahmani-2024-judgeblender`

- **LLMJudge: LLMs for Relevance Judgments** — Hossein A. Rahmani, Emine Yilmaz, Nick Craswell,
  Bhaskar Mitra, Paul Thomas, Charles L. A. Clarke, Mohammad Aliannejadi, Clemencia Siro,
  Guglielmo Faggioli. LLM4Eval workshop @ SIGIR 2024; arXiv:2408.08896. **verified** (arXiv + full
  text). The shared-task dataset (TREC 2023 DL passage; dev 25 queries/7,263 pairs, test 25
  queries/4,423 pairs; 0–3). Across 39 submissions: "**low variability in Kendall's τ but greater
  variability in Cohen's κ**" — the same decoupling, now measured across independent teams.
- **JudgeBlender: Ensembling Judgments for Automatic Relevance Assessment** — Hossein A. Rahmani,
  Emine Yilmaz, Nick Craswell, Bhaskar Mitra. arXiv:2412.13268. **verified** (arXiv + full text).
  ⚠️ **The nearest thing in IR to a pooled graded relevance label.** PromptBlender (one model, many
  prompts) and LLMBlender (different models) aggregate by majority vote or **average voting** (mean
  of judges' scores → a continuous graded label). Best Cohen's κ **0.2619**, best Krippendorff's
  α **0.4887**, best Kendall's τ **0.9612**. Note what that means: **ensembling improved leaderboard
  τ far more than it improved per-label agreement.** And the pooling is across *heterogeneous* judges,
  never across repeated samples of one configuration.

**Weight** — `judgeblender` **high** and a partial threat (graded pooled label in IR already exists);
`llmjudge` medium.

### `farzi-2025a` and `farzi-2025b`

- **Does UMBRELA Work on Other LLMs?** — Naghmeh Farzi, Laura Dietz, SIGIR '25, pp. 3214–3222,
  DOI `10.1145/3726302.3730317`; arXiv:2507.09483. **verified** (Crossref + arXiv). Same prompt,
  five models, TREC DL 2023: four-point κ GPT-4o 0.308, DeepSeek V3 0.262, Llama-3.3-70B 0.233,
  Llama-3-8B 0.187, FLAN-T5-large 0.062 — an ~80% collapse from largest to smallest — while Kendall
  τ moves only 0.911 → 0.868. **Between-judge variance, the cousin of test-retest.**
- **Criteria-Based LLM Relevance Judgments** — Naghmeh Farzi, Laura Dietz, ICTIR '25, pp. 254–263,
  DOI `10.1145/3731120.3744591`; arXiv:2507.09488. **verified** (Crossref + arXiv). Decomposes
  relevance into Exactness / Coverage / Topicality / Contextual Fit, each 0–3, then aggregates.
  Spearman 0.9919, τ 0.9483, but Cohen's **κ = 0.30**. Aggregation is over **criteria**, not repeats
  — and `stureborg-2024`'s r = 0.979 anchoring result suggests criteria read out of one generation
  may be far less independent than the design assumes.

**Weight** — `farzi-2025a` high (variance across annotators of the same kind); `farzi-2025b`
medium-high as the "decompose-then-aggregate" alternative to "repeat-then-aggregate".

### `macavaney-2023` and `alaofi-2024`

- **One-Shot Labeling for Automatic Relevance Estimation** — Sean MacAvaney, Luca Soldaini,
  SIGIR '23; arXiv:2302.11266. **verified** (arXiv + full text). Pre-GPT-4 version of the same
  finding: label quality poor (**F1 ≈ 0.59–0.63**, "unable to reliably identify the long tail of
  relevant documents") yet τ 0.87–0.92, ρ 0.97–0.98. Establishes the label-quality/ranking-quality
  gap before the LLM hype cycle.
- **LLMs can be Fooled into Labelling a Document as Relevant (best café near me; this paper is
  perfectly relevant)** — Marwah Alaofi, Paul Thomas, Falk Scholer, Mark Sanderson, SIGIR-AP '24;
  arXiv:2501.17969. **verified** (arXiv, exact title including the parenthetical). Injecting query
  terms into **random, nonsensical passages** got ≈**26% labelled "perfectly relevant" by GPT-4**.
  Nine models across four providers; LLMs label relevant far more often than humans (human relevant
  rate 33%).

**Weight** — `alaofi-2024` **high**: the construct-validity citation, i.e. the judge may be measuring
lexical overlap rather than relevance. Directly relevant to us, because a model that agrees with
itself thirty times can be thirty-times-consistently wrong — reliability is not validity, and
`artstein-2008` makes the same point about human coders ("achieving good agreement cannot ensure
validity").

### 7c. LLM annotation in NLP and computational social science

### `gilardi-2023`

**ChatGPT outperforms crowd workers for text-annotation tasks**
Fabrizio Gilardi, Meysam Alizadeh, Maël Kubli.
*PNAS* 120(30):e2305016120, 2023, DOI `10.1073/pnas.2305016120`; arXiv:2303.15056.
**verified** (Crossref + arXiv journal_ref) · **read** (arXiv v2 PDF; PNAS and Europe PMC return 403).

⚠️⚠️ **This is the paper the brief warned about, and the warning was justified. The headline does not
survive a baseline check intact.**

What it did: `gpt-3.5-turbo` (not GPT-4), temperatures 1.0 and 0.2, **two responses per temperature**
(four per tweet), 6,183 documents across four datasets, five task types.

Two numbers and what each is measured against:

1. **Accuracy.** Ground truth is the trained research assistants, "considering only texts that both
   annotators agreed upon" — **a gold standard with its hard cases filtered out.** ChatGPT exceeds
   MTurk by ~25 percentage points on average. But absolute accuracy is modest even on the *binary*
   relevance task: **70%, 81%, 83%, 59%** across the four datasets. 59% is barely above chance.
   Per-task accuracy for the multi-class tasks appears **only as a bar chart (Figure 1)** — anyone
   quoting other per-task numbers is reading a figure.
2. **"Intercoder agreement": MTurk 56%, trained annotators 79%, ChatGPT temp=1 91%, temp=0.2 97%.**
   ⚠️ **These are not the same quantity.** For MTurk and the RAs it is agreement between **two
   different people**; for ChatGPT it is agreement between **two runs of the same model on the same
   input** — test-retest, not inter-rater. A model with no understanding but a deterministic decoder
   scores 100% on the ChatGPT version. It is also **raw percent agreement, uncorrected for chance** —
   no κ, no α anywhere in the paper — so on a skewed binary task 91% can coexist with a near-zero κ.

**Weight** — **must-cite, but as the cautionary example, not as support.** It is also, awkwardly,
*the* precedent for putting model test-retest and human inter-rater agreement in the same comparison
— and the reason we must not do it the way they did. If we compare our within-model repeatability to
a human inter-annotator number, we have to say in the same breath that they are different quantities
(and use a chance-corrected statistic, which they did not).

### `kristensen-mclachlan-2025`

**Are chatbots reliable text annotators? Sometimes**
Ross Deans Kristensen-McLachlan, Miceal Canavan, Márton Kárdos, Mia Jacobsen, Lene Aarøe.
*PNAS Nexus* 4(4):pgaf069, April 2025, DOI `10.1093/pnasnexus/pgaf069`; arXiv:2311.05769.
https://academic.oup.com/pnasnexus/article/4/4/pgaf069/8100281
**verified** (OUP article page).

The in-family rebuttal (PNAS Nexus answering PNAS). 1,000 US-news tweets, two binary tasks, human
gold at Krippendorff α = 0.86 / 0.84. Across GPT-3.5-turbo, GPT-4, FLAN-T5-XXL, Llama-3.1-8B and
StableBeluga2-13B: **a supervised DistilBERT wins on both tasks**, matched only by GPT-4 on one, and
on the other "no other model comes close" — GPT-4 recall fell to **0.1** under a zero-shot generic
prompt. Verdict: "LLMs are currently too brittle and potentially unreliable for scientific research."

⚠️ **The baseline Gilardi never ran** is a fine-tuned small supervised classifier, and it wins.

**Weight** — **high.** Cite it every time `gilardi-2023` is cited.

### `pangakis-2023`

**Automated Annotation with Generative AI Requires Validation**
Nicholas Pangakis, Samuel Wolken, Neil Fasching.
arXiv:2306.00176 (31 May 2023).
https://arxiv.org/abs/2306.00176
**verified** (arXiv + full text).

The breadth result: **27 annotation tasks across 11 datasets**, >200,000 texts, GPT-4. LLM-vs-human
accuracy 0.674–0.981 (median 0.85) and **F1 0.059–0.969 (median 0.707)**; **nine of 27 tasks had
precision or recall below 0.5**; within a *single* dataset F1 ranged 0.259–0.811. Reports **no κ and
no α**, which is itself notable given the label skew.

**Weight** — high. The point for us: task-level heterogeneity is so large that any aggregate "LLMs
annotate as well as humans" number is meaningless, and per-task reliability must be reported.

### `ziems-2024`

**Can Large Language Models Transform Computational Social Science?**
Caleb Ziems, William Held, Omar Shaikh, Jiaao Chen, Zhehao Zhang, Diyi Yang.
*Computational Linguistics* 50 (accepted 25 Oct 2023); arXiv:2305.03514.
https://arxiv.org/abs/2305.03514
**verified** (arXiv, journal comment confirms CL).

25 English CSS benchmarks stratified across **utterance, conversation and document levels** — one of
the few papers that varies the unit of analysis deliberately. Zero-shot LLMs generally underperform
fine-tuned RoBERTa; one notable exception, misinformation detection, where the best LLM reached
**κ = 0.55 vs the dataset's inter-human κ = 0.51.** Conclusion: LLMs cannot replace human annotation
zero-shot but can participate in partnership with humans.

**Weight** — medium-high.

### `tornberg-2023`

**ChatGPT-4 Outperforms Experts and Crowd Workers in Annotating Political Twitter Messages with
Zero-Shot Learning**
Petter Törnberg.
arXiv:2304.06588 (13 Apr 2023).
https://arxiv.org/abs/2304.06588
**verified** (arXiv + full text).

500 tweets, **5,000 model runs: 5 repetitions at each of two temperatures.** Reports "substantially
higher levels of reliability than the human coders", measured with **Krippendorff's α**. Same
structural caveat as `gilardi-2023` — the LLM's α is over its own repeats, the humans' α over
different people — but at least chance-corrected and with 5 repeats rather than 2.

**Weight** — medium-high; a second instance of the model-test-retest-vs-human-inter-rater conflation,
done more carefully.

### `ollion-2024`

**The dangers of using proprietary LLMs for research**
Étienne Ollion, Rubing Shen, Ana Macanovic, Arnault Chatelain.
*Nature Machine Intelligence* 6(1):4–5, 2024, DOI `10.1038/s42256-023-00783-6`.
Preprint: *ChatGPT for Text Annotation? Mind the Hype!*, OSF DOI `10.31235/osf.io/x58kn`.
**verified** (Crossref, both records). Not read — do not quote figures from it without fetching.

The reproducibility critique: closed models are versioned silently and deprecated, so an annotation
pipeline built on one cannot be replicated.

**Weight** — medium; relevant to us because a thirty-reading reliability figure is tied to a model
snapshot, and we should say which.

---

## Contradicts or complicates us

Ordered by how much damage each could do.

1. **`wong-2022` — the thing we did has a name, and the name is not ours.**
   "k-rater reliability", computed as ICC(k) / Spearman–Brown, is an *existing, published* proposal
   for reporting the reliability of an aggregated annotation, with a worked example (ICC(1) 0.590 →
   ICC(13) 0.950) of the same qualitative shape as ours. We must not present "report reliability of
   the pooled object rather than of one reading" as novel. What is still ours: the aggregation is
   over **repeated readings of one annotator**, not over distinct raters, and the comparison is
   against a **set-valued** object's reproduction rate rather than against ICC(1) of the same
   quantity.

2. **`mathew-2021` (HateXplain) — a graded per-token support score pooled over annotations already
   exists as a data representation.** HateXplain pools multiple annotators' rationale spans into a
   soft temperature-scaled per-token distribution. `muscato-2026` systematises this as the SOFT
   rationale space and shows it beats the HARD (majority) and INTERMEDIATE (union) spaces. Our "the
   stable object is a graded per-unit support score, not a span set" is therefore **not novel as a
   representation**. Narrow the claim to the *reliability measurement*: who has reported a reliability
   coefficient for the graded object side by side with the reproduction rate of the discrete one?

3. **`jakobsen-2023` — someone already conditioned location agreement on presence agreement.**
   They restrict to instances with **full label agreement** and then measure rationale agreement
   across annotator groups, getting token-F1 of 0.41–0.67. That is the between-annotator version of
   our presence/location dissociation, published at ACL 2023. Our dissociation is therefore a
   *replication in the within-annotator, repeated-reading regime* of a known between-annotator
   effect — which is still worth reporting, but must be framed that way.

4. **`scholer-2011` — test-retest on relevance judgments is not new in IR.** Planting duplicate
   documents so the same assessor judges the same document twice is a 2011 SIGIR design. Our
   test-retest novelty claim survives only if it is scoped to *span/location* annotation and to a
   *model* as the repeated annotator.

5. **`abercrombie-2023` — test-retest is rare, but it is no longer unremarked.** They surveyed the
   ACL Anthology, found 56/80,000+ papers (<0.07%) reporting intra-annotator agreement, and argued it
   should be standard. If we say "test-retest is rare", cite them for the number instead of asserting
   it — and note their tasks were all 2–3-class labels, never spans, and that their 25% inconsistency
   is measured on a deliberately high-disagreement subsample.

6. **`nenkova-2004` / `lin-2006` — "replace the unstable discrete judgment with a pooled graded one"
   is a 2004–2006 idea.** The Pyramid method weights content units by how many humans produced them;
   Lin & Demner-Fushman apply it to TREC QA nuggets precisely because the binary vitality judgment
   was unstable. This is the intellectual ancestor of our move and it is two decades old. Cite it
   rather than let a reviewer find it.

7. **`mathet-2015` (γ) and `krippendorff-1995` (α_U) — there are purpose-built coefficients for span
   agreement, and we did not use them.** γ handles unitizing and categorisation jointly and is
   chance-corrected; the `pygamma-agreement` implementation advertises **intra**-annotator support.
   Expect "why not γ?" and have an answer.

8. **`hong-2025` / `jiang-2023` — "within-label variation" is the existing term** for agreement on the
   verdict with divergence in the reasoning. Using a new phrase for it will read as not knowing the
   literature.

9. **`parry-2025` — the "disagreement doesn't break rankings" result has just been re-confirmed on
   modern short-passage collections** (κ as low as 0.12 on 4-grade relevance, τ = 0.879 on system
   order). Any claim that our instability threatens evaluation must contend with this.

**Baseline check.** The one dramatic-multiple claim in the papers I read myself is `wong-2022`'s
ICC(1) 0.590 → ICC(13) 0.950. That is *not* a gain against a zero baseline: the single-rater baseline
is 0.590, a real and non-trivial number, and the jump is exactly what Spearman–Brown predicts
analytically (the bootstrap estimate 0.953 agrees). It is a correct and unsurprising result, not an
inflated one. Its implication for us is the reverse of flattering: a large Spearman–Brown coefficient
at k = 30 is *expected* given a moderate single-reading ICC, so the interesting number in our result
is the single-reading reliability and the *contrast* with the span-set reproduction rate — not the
0.92 on its own.

---

## The strongest framing available

**Use the existing names. There are three, and they nest.**

1. **k-rater reliability (kRR)** — `wong-2022`. This is the established name for "the reliability of
   the aggregate, not of one reading", and it comes with ICC(k) and Spearman–Brown as the standard
   estimators. This is the frame for our 0.92. Say "k-rater reliability" and cite Wong & Paritosh the
   first time the number appears, then Warrens/de Vet via them for the ICC(k)≡Spearman–Brown identity.

2. **Generalizability theory** — `urbano-2013`, `bodoff-2007`/`bodoff-2008`. The IR community's own
   vocabulary for decomposing measurement variance and predicting the reliability of a pooled
   measurement. Using it signals that the reliability claim is psychometrics, not a correlation we
   liked the look of. `urbano-2013` also maps generalizability coefficients onto Kendall τ, which is
   the currency of `voorhees-2000` and `parry-2025` — that is the bridge between our number and the
   classical IR result.

3. **Human label variation / within-label variation** — `plank-2022`, `hong-2025`. HLV is the NLP
   term for "there is no single correct label"; **within-label variation** is the term for our exact
   phenomenon — agreement on the verdict, divergence in the justification. Do not coin a new phrase.

**The framing sentence I would write**, using only established terms:

> Presence is a *reliable* judgment; location exhibits **within-label variation** (Hong et al., 2025;
> Jiang et al., 2023). Where the discrete object — the span set — has low **k-rater reliability**, a
> graded per-sentence support score pooled over k readings has high kRR (Wong & Paritosh, 2022),
> in the same way that a graded pooled content-unit weight was substituted for an unstable binary
> vitality judgment in summarisation and QA evaluation (Nenkova & Passonneau, 2004;
> Lin & Demner-Fushman, 2006).

**The best single prior work to position against** is `lin-2006` for the *move* (unstable binary
judgment → pooled graded weight, in IR, with the ranking consequences worked out) and `wong-2022` for
the *statistic*. `nenkova-2004` is the older and more famous version of the move; `aroyo-2015` is the
manifesto version.

**On statistics:** report a Jaccard/IOU-family number for the span condition because that is what
`deyoung-2020` made standard and what `jakobsen-2023` reports, but cite `hripcsak-2005` for why it is
mean pairwise F1 (≡ positive specific agreement) and not κ — negatives are not countable in a span
task — and cite `artstein-2008` §4.3 and the κ-paradox cluster (`feinstein-1990`, `byrt-1993`,
`powers-2012`) for why a low κ on a low-prevalence span task is uninformative. That turns "we used a
convenient metric" into "we used the metric the methodology literature prescribes for this data
shape".

**The honest novelty statement**, after all of the above:

> Between-annotator versions of each piece exist — test-retest at the document level
> (Scholer et al., 2011), rationale disagreement conditioned on label agreement
> (Jakobsen et al., 2023), graded per-token pooling (Mathew et al., 2021), and kRR as the reliability
> unit (Wong & Paritosh, 2022). What is not in the literature is the *within-annotator, repeated-
> reading* version of the location/presence dissociation, or a side-by-side reliability comparison of
> the graded and the set-valued object off the same readings.

---

## Searched and did not find

Queries run against the arXiv API (`search_query` shown) that returned **zero** results, establishing
that the obvious phrasings are unoccupied:

| Query | Hits |
|---|---|
| `abs:"intra-annotator" AND abs:"span"` | 0 |
| `abs:"intra-annotator" AND abs:"rationale"` | 0 |
| `abs:"test-retest" AND abs:"relevance judgment"` | 0 |
| `abs:"same annotator" AND abs:"span" AND abs:"reliability"` | 0 |
| `abs:"graded evidence" AND abs:"sentence"` | 0 |
| `abs:"per-sentence" AND abs:"support score"` | 0 |
| `abs:"Spearman-Brown" AND abs:"annotat"` | 0 |
| `abs:"sentence-level" AND abs:"support" AND abs:"attribution" AND abs:"agreement"` | 0 |

Searched and found only adjacent, non-competing work:

- `abs:"test-retest" AND abs:"annotation"` — returns `amidei-2020` plus **medical-imaging**
  segmentation papers (FatSegNet, SVRDA). Within-rater span/region consistency is a normal thing to
  measure in *radiology*, under the names intra-observer variability and Dice/Jaccard intra-rater
  agreement; it is not a thing in NLP or IR. That asymmetry is worth one sentence in our paper.
- `abs:"test-retest" AND abs:"large language model"` — returns clinical-classification validation and
  purchase-intent simulation, i.e. test-retest of an LLM used as a *measurement instrument for
  something else*, not of an LLM as an annotator of evidence.
- Full-text grep of `artstein-2008` (42 pages) for `intra-coder`, `intra-annotator`, `test-retest`,
  `stability` — **zero occurrences of all four**. The standard agreement survey does not consider
  within-annotator repetition at all.

Not covered here by design (they belong to other files in this inbox): attribution/citation
evaluation for RAG, faithfulness metrics, LLM-as-judge for generation quality except where it bears
on annotation reliability.

---

## COULD NOT VERIFY

*(none in the body above — every entry listed was fetched. Items that failed verification are
recorded here.)*

- **Urbano, Marrero & Martín (SIGIR 2013)** — metadata verified via Crossref and the author's CRAN
  package page, but the **full text was not retrieved** (ACM DL returns 403 to this client; the
  author's PDF mirror `julian-urbano.info/files/publications/017-…pdf` returns a 404 page). The
  description above is deliberately confined to what the title, Crossref record and package
  description support. Re-check before citing a specific number from it.
- **James (2026), "Counting on Consensus"** — arXiv record verified; the claimed LREC 2026 acceptance
  is asserted only in the arXiv comment field and I did not locate a proceedings record. Cite as a
  preprint or not at all.
- **ACM Digital Library pages generally** returned HTTP 403 to this client, and **dblp** returned a
  bot-check page. All ACM-published items above were therefore verified through **Crossref's
  publisher-deposited DOI metadata** (which is ACM's own deposit) rather than through the DL page.
  Where Crossref truncates a title at the colon — it does this for `bailey-2008`, `carterette-2008-
  preference` and `nenkova-2007` — the full title was recovered from a second source, noted inline.
