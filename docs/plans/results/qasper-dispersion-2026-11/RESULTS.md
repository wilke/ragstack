# QASPER: how often does answer evidence span more than one section? (EXPLORATORY)

**EXPLORATORY, descriptive, not pre-registered.** Nothing was frozen before the data was seen.
The definitions, sensitivity cuts and the added test-split group were chosen while looking at
the data. Read every number here as a description of QASPER's annotations, not as a test of
anything.

- **Date:** 2026-11-11
- **Artifact:** [`artifacts/qasper-dispersion.json`](artifacts/qasper-dispersion.json). Every
  number below is taken from it.
- **Script:** [`qasper_dispersion.py`](qasper_dispersion.py), tests in
  [`test_qasper_dispersion.py`](test_qasper_dispersion.py).
- **Provenance:** commit `c972ba8e`, `dirty: false`, `untracked: 0`, version `v1.6.26+c972ba8e`.
  The run was offline: no LLM, no live services.
- **Background:** [`docs/papers/bib-inbox/15-evidence-dispersion-question-type.md`](../../../papers/bib-inbox/15-evidence-dispersion-question-type.md),
  *Next steps* item 1.

## 1. Hypothesis

The owner's working assumption is that **questions whose evidence spans more than one document
section are usually composite or complex**. The literature check found no published
section-level figure for any dataset. This run measures the first half of what would be needed:

- how often the evidence crosses sections at all;
- whether that rate differs by QASPER answer type.

**Answer type is not question composition.** Extractive, abstractive and yes/no describe the
*form of the answer*. They say nothing about whether the question combines several facts. This
run does not code question complexity: there is no LLM coding and no manual coding. So it
cannot test the assumption. It can only say how large the "spans sections" population is, and
whether it leans toward any answer form.

## 2. Results summary

Pool: train+dev, **n = 3,321** answerable answers with text-only evidence.

| answers whose evidence spans... | share | Wilson 95% CI |
|---|---|---|
| more than one paragraph | 31.4% (1,043) | 29.9–33.0 |
| more than one section, full path | 14.2% (470) | 13.0–15.4 |
| more than one top-level section | 10.1% (337) | 9.2–11.2 |

- **Cross-section evidence is the minority case.** About **1 answer in 10** has evidence in
  more than one *top-level* section. Of the multi-paragraph answers, about a third (32.3%,
  337 of 1,043) cross a top-level boundary.
- **Abstractive answers cross sections most often:** 14.5% at top level, against 8.6% for
  extractive. The confidence intervals do not overlap. Yes/no answers sit in between at
  11.7%, with an interval that overlaps both.
- **Annotators mostly agree on the label, but chance agreement is high.** For the top-level
  single- vs multi-section label, 85.3% of 559 multi-annotated questions agree, yet Fleiss
  κ is only 0.22. The label is rare, so most of that agreement is expected by chance.
- **The paper's 55.5% multi-paragraph figure does not reproduce.** Every released version and
  split gives 30–32% under the paper's stated definition (§ 4.1). The gap is unexplained.

## 3. How it was measured

### Data

**QASPER v0.3**, the official AllenAI release, licensed CC BY 4.0. The URL is the one in the
`allenai/qasper` Hugging Face loading script (`_URL_TRAIN_DEV`).

| archive | URL | sha256 |
|---|---|---|
| `qasper-train-dev-v0.3.tgz` | https://qasper-dataset.s3.us-west-2.amazonaws.com/qasper-train-dev-v0.3.tgz | `a28fdf966db827bcee3d873107d6b6669864fb7ca8fbf73a192f5e39191bdb5a` |
| `qasper-test-and-evaluator-v0.3.tgz` (secondary group) | https://qasper-dataset.s3.us-west-2.amazonaws.com/qasper-test-and-evaluator-v0.3.tgz | `72a52a41193e2838b8074f80ac074b94f956b84886c36a61c58a7df4171bdd72` |
| `qasper-train-dev-v0.1.tgz` (only for the 55.5% check) | https://qasper-dataset.s3.us-west-2.amazonaws.com/qasper-train-dev-v0.1.tgz | `cd0cb8911342966fcc3eb91947af149cb7cf80b4f253ff9a6f0333f4752080dd` |

The sha256 of each extracted JSON file is recorded in the artifact (`dataset.files`,
`reproduction_55_5.v0.1.*.sha256`).

**The primary pool is train+dev** (`splits.pooled`). The public test split carries most of
QASPER's multi-annotator questions, so it is reported separately as `pooled_with_test`. It
was added after looking at the data.

### Unit and denominators

- **Unit:** one answer, that is, one annotation. A question can have several answers.
- **Primary denominator:** answers that meet all four conditions:
  - they are answerable (extractive, abstractive or yes/no);
  - their evidence is non-empty;
  - no evidence item is a `FLOAT SELECTED` table or figure;
  - every text evidence string maps to the paper.

Path through the filters, for train+dev:

| step | answers |
|---|---|
| all answers | 4,439 |
| minus unanswerable | 444 removed, leaving 3,995 answerable |
| minus empty evidence | 135 removed |
| minus any table/figure evidence | 539 removed |
| text-only | 3,321 |
| unmapped | 0 |
| **primary n** | **3,321** |

With the test split added, the primary n is **6,040**.

### Mapping evidence to sections

Each text evidence string is mapped by three rules, applied in order:

1. exact match to a `full_text` paragraph;
2. whitespace-normalised match;
3. the string equals a section heading.

Train+dev has 6,378 text evidence strings:

| rule | strings |
|---|---|
| exact paragraph match | 5,883 |
| whitespace-normalised match | 219 |
| section heading | 276 |
| unmatched | 0 |

So **7.8% of strings do not match exactly** (`mapping.unmatched_exact_share`). Once the two
fallback rules are applied, **none are unmatched**.

The heading rule handles annotators who selected a heading as evidence. Such a string counts as
one location in the section it names.

Eleven answers have evidence whose text appears in more than one section. Each is resolved to
the earliest section, mirroring QASPER's own rule of preferring earlier paragraphs. A
sensitivity cut excludes them (§ 4.2).

### Section levels

S2ORC section names in QASPER are hierarchical, for example `Experiments ::: Setup`.
Hierarchical names occur in 363 of 888 train papers and 91 of 281 dev papers. Two levels are
reported:

- **Full path:** `section_name` as given. A subsection is a different section from its parent.
- **Top level:** the first `:::` component. `Experiments` and `Experiments ::: Setup` are the
  same section.

One train paper repeats a full section name. At the full-path level its two entries merge
(`structure.train.papers_with_duplicate_full_names`).

### Statistics

- **Wilson 95% intervals** throughout.
- **Paper-cluster bootstrap** for the pooled headline and by-type shares (2,000 replicates,
  seed 20261111), because answers within a paper are not independent. The bootstrap intervals
  are within about 0.3 points of the Wilson intervals (`paper_cluster_boot95`).
- **Annotator agreement:** Fleiss κ in its variable-raters, pairwise form, computed over the
  eligible answers of each question that has at least 2. The annotators differ from question
  to question, so Cohen's κ does not apply. With exactly two raters, this κ equals Scott's π.

## 4. Results

### 4.1 The 55.5% reproduction check

*Anchor (independent review, 2026-11-11).* The paper's other §3 and Table 1 figures reproduce exactly from the same
release: the answer-type shares 51.8 / 24.2 / 13.9 / 10.2 % over all 7,993 answers of the three splits, the 11.6 %
table/figure share, and the 3.0 % "no evidence" share among *No* answers over the 7,183 answerable ones. So the
paper's denominators are known and match the release; the 55.5 % is the one figure that does not. A wide search of
alternative definitions (per-question union, floats included, highlighted items or sentences, any/max annotator,
v0.1) found nothing at 55.5 %; the nearest are the test-split union (57.6 %) and multi-sentence highlights (61.7 %),
both different definitions.

The QASPER paper says, in section 3: *"Among the answerable questions with text-only evidence,
55.5% of the answers have multi-paragraph evidence."* Computed literally from the JSON (more
than one distinct evidence string; `reproduction_55_5`):

| release / split | text-only answers | multi-paragraph |
|---|---|---|
| v0.3 train | 1,981 | 32.3% |
| v0.3 dev | 1,340 | 30.1% |
| v0.3 test | 2,719 | 31.4% |
| v0.1 train (the version current at publication) | 1,981 | 32.3% |
| v0.1 dev | 1,340 | 30.1% |

Looser readings tried while looking for the gap also stay well below 55.5%:

- more than one *highlighted* evidence item: 26–28%;
- tables and figures included: 34–36%;
- per question, the union of paragraphs over all annotators: train 33.0%, dev 44.6%, test
  57.6%. Test is the only split near 55.5%, and only because it has the most annotators per
  question.

**The figure is not reproduced, and the gap is unexplained.** These causes are ruled out:

- the release version (v0.1 and v0.3 agree);
- the split;
- the mapping (the literal check uses raw evidence strings);
- answer-level vs question-level counting, except for the multi-annotator union on test.

The rest of this document rests on the released evidence fields as they are, and is internally
consistent with them. Anyone quoting "55.5%" should know that the released data gives about
31%.

### 4.2 Headline shares (descriptive)

Primary pool, train+dev, n = 3,321. `splits.pooled.text_only`.

| level | share | Wilson 95% | paper-cluster bootstrap 95% |
|---|---|---|---|
| multi-paragraph | 31.4% | 29.9–33.0 | 29.6–33.2 |
| multi-section, full path | 14.2% | 13.0–15.4 | 12.8–15.6 |
| multi-section, top level | 10.1% | 9.2–11.2 | 9.0–11.3 |

Number of top-level sections per answer: 1 section for 2,984 answers, 2 for 287, 3 for 39, and
4 or more for 11.

Given multi-paragraph evidence (n = 1,043):

- 45.1% span more than one full-path section;
- 32.3% span more than one top-level section.

Sensitivity cuts (`text_only_exact_match_only`, `text_only_excluding_ambiguous`):

| cut | n | multi-paragraph | full path | top level |
|---|---|---|---|---|
| exact-match answers only | 3,072 | 27.6% | 11.6% | 9.0% |
| excluding the 11 ambiguous answers | 3,310 | 31.2% | 13.9% | 9.9% |

The exact-match cut drops answers that used a heading or had whitespace drift. Those are
disproportionately multi-evidence answers, so this cut is a lower bound.

The train+dev+test pool (n = 6,040) gives 31.4%, 13.8% and 9.9% for the three levels.

### 4.3 By answer type (descriptive)

Train+dev. Each cell is the share with its Wilson 95% interval.

| answer type | n | multi-paragraph | multi-section, full path | multi-section, top level |
|---|---|---|---|---|
| extractive | 2,252 | 29.9% (28.1–31.9) | 12.9% (11.6–14.4) | 8.6% (7.5–9.9) |
| abstractive | 640 | 39.8% (36.1–43.7) | 18.9% (16.1–22.1) | 14.5% (12.0–17.5) |
| yes/no | 429 | 26.6% (22.6–31.0) | 13.5% (10.6–17.1) | 11.7% (9.0–15.0) |

With test (n = 4,041 / 1,189 / 810):

| answer type | multi-paragraph | full path | top level |
|---|---|---|---|
| extractive | 29.8% | 12.5% | 8.4% |
| abstractive | 40.8% | 18.8% | 14.1% |
| yes/no | 25.9% | 13.3% | 11.5% |

Two patterns:

- **Abstractive answers lead at every level.** At top level the gap over extractive answers is
  about 6 points, roughly 1.7×.
- **Yes/no answers have the *lowest* multi-paragraph rate, but more top-level crossings than
  extractive answers.** When a yes/no answer cites more than one paragraph, those paragraphs
  are in different top-level sections 43.9% of the time (50 of 114). The rate is 28.8% for
  extractive answers and 36.5% for abstractive answers.

### 4.4 Annotator agreement on the label (descriptive)

Train+dev: 559 questions have at least 2 eligible answers. 826 questions have at least 2
answers of any kind; the rest drop out on answerability, tables/figures or empty evidence.

| label | all annotators agree | Fleiss κ | positive rate |
|---|---|---|---|
| multi-paragraph | 75.5% | 0.43 | 0.31 |
| multi-section, full path | 83.0% | 0.33 | 0.15 |
| multi-section, top level | 85.3% | 0.22 | 0.10 |

- Annotators chose the identical set of section entries for **65.7%** of these questions.
- With test (1,579 questions): top level 85.1% agree, κ 0.25; full path 81.7%, κ 0.33.

The high raw agreement mostly reflects the low base rate: two annotators who both say "single
section" agree by default. κ in the 0.2–0.3 range means the multi-section label is **noisy at
the level of a single annotation**. That is consistent with QASPER's human Evidence-F1 lower
bound of 71.6.

### 4.5 Distance between the sections (descriptive)

Answers with evidence in more than one full-path section (n = 470):

- all their sections are consecutive `full_text` entries in **46.4%**;
- 28.3% stay inside one top-level section, that is, sibling subsections or parent and child;
- the span in entries has a median of 2 and an interquartile range of 1–4.

For top-level multi-section answers (n = 337), the top-level sections are consecutive in
42.4%.

**Proxy only, for "the same finding restated":**

- 32.6% (110 of 337) of top-level multi-section answers include a paragraph from the paper's
  first section, usually the Introduction. For single-section answers, 13.8% sit in the first
  section.
- Dropping the first section leaves **71.5%** of top-level multi-section answers still
  multi-section.

The abstract is a separate field, and no evidence string matched it. This proxy cannot tell
restatement from genuine combination. It only bounds how much of the cross-section evidence
could be intro plus body.

### 4.6 Including tables and figures (descriptive)

Answerable answers with any evidence and all text mapped: n = 3,860. Of these, 539 cite a
table or figure and 238 cite only tables or figures (`with_figures_tables.all`).

| measure | share | n |
|---|---|---|
| multi-location (paragraphs plus floats) | 35.6% | 3,860 |
| multi-section, top level, floats ignored | 10.3% | 3,622 with text |
| multi-section, top level, floats counted as one extra section | 16.5% | 3,860 |
| multi-section, full path, floats ignored | 14.6% | 3,622 with text |
| multi-section, full path, floats counted as one extra section | 20.0% | 3,860 |

The release does not place a figure or table in a section, so the true value lies between the
two treatments.

## 5. Conclusion (descriptive only)

**What is shown.** In QASPER's released annotations:

- cross-section evidence is a minority, about 10% at top level and about 14% at full path, of
  answerable text-evidence answers;
- most multi-paragraph evidence stays inside one top-level section;
- the rate is higher for abstractive answers than for extractive ones.

**What is not shown.** Nothing here measures whether cross-section questions are *composite*:

- **Answer type is not question composition.** The abstractive lead is consistent with
  synthesis questions producing free-form answers. It is equally consistent with annotators
  paraphrasing whenever they cite more than one place.
- Per § 4.5, a third of cross-section answers involve the first section. Some may restate one
  finding rather than combine two.

**What it means for the owner's assumption.**

- *"Cross-section questions are usually composite"* is a statement about a small population,
  about 1 in 10 answers. Even if it held, it would say little about most questions.
- In the other direction, it cannot be read off QASPER answer types: yes/no answers, the least
  "composite-looking" form, cross top-level sections slightly *more* often than extractive
  answers.
- The assumption needs question-composition coding (the later step) on the cross-section
  subset and on a single-section control.

### Caveats

1. **Paragraph is not section.** QASPER annotators selected sentences, which the release maps
   to whole paragraphs. They never chose sections. Section membership comes from S2ORC's
   LaTeX-derived structure, and the top-level vs full-path choice moves the rate by about 4
   points.
2. **Annotation noise.** The human Evidence-F1 lower bound is 71.6, and κ for this label is
   0.22–0.33 (§ 4.4). Any per-answer label is noisy. The shares are more stable than the
   individual labels.
3. **Writers saw only the title and abstract.** The question writers never saw the full text,
   which the literature links to more dispersed evidence. QASPER has no in-dataset variation on
   this, so it is a confound to note, not to analyse. The rates here may not transfer to
   questions written with the text in view.
4. **The 55.5% discrepancy** (§ 4.1) is unexplained. It does not affect the internal
   comparisons here, but it does mean the paper's headline does not describe the released data.
5. **Exploratory.** Several cuts were examined and the test-split group was added after seeing
   train/dev. No significance claims are made.

## Test data: from source to analysed sample

1. Download the three archives above into an empty scratch directory. Extract them there. Run
   the script with `python -I` on them.
2. **Splits:**

   | split | papers | questions | answers |
   |---|---|---|---|
   | train | 888 | 2,593 | 2,675 |
   | dev | 281 | 1,005 | 1,764 |
   | test | 416 | 1,451 | 3,554 |

   Source: `structure`.
3. **Filters** to the primary denominator (§ 3) produce n = 3,321 for train+dev and 6,040
   with test (`splits.*.denominators`). Nothing is sampled; every eligible answer is used.
4. **Transformations:** whitespace normalisation, collapsing `\s+` to a single space and
   stripping the ends, applied only in the fallback mapping. Section level is derived by
   splitting on `" ::: "`.
5. The bootstrap seed is 20261111. Otherwise the script is deterministic: two runs gave
   identical output apart from `provenance`.
6. **No QASPER question, answer or paper text is committed.** Only aggregates and one quoted
   sentence of the QASPER paper.

Reproduce from the repo root on a clean tree:

```
PYTHONPATH=python python3 -I docs/plans/results/qasper-dispersion-2026-11/qasper_dispersion.py \
  DATA/x docs/plans/results/qasper-dispersion-2026-11/artifacts/qasper-dispersion.json \
  --test-dir DATA_TEST/x \
  --repro-dir "v0.1=DATA_V01/x,https://qasper-dataset.s3.us-west-2.amazonaws.com/qasper-train-dev-v0.1.tgz"
```

You need Python 3.11 or later with `pydantic`. The run used the `ragstack` conda environment,
Python 3.12.13. The script records the provenance of *its own* checkout and refuses if
`ragstack` imports from anywhere else. An installed `ragstack` from another checkout was caught
this way during development (commit `c972ba8e`).
