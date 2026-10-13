# Bib inbox 13 — Preferential sampling, presence-only fusion, pooled qrels and label-efficient evaluation

> **Provenance.** Produced 2026-10-13 for the LDRD FY27 pre-proposal (expert-read design, H2) and for
> Paper A's threats-to-validity on pooled qrels. Input: the brief's list (A–F) plus the already-read
> Diggle, Menezes & Su 2010. The PDFs were downloaded to a working directory outside this repo and are
> not committed; text was extracted with `/rag/envs/ragstack/bin/python3 -I` (pymupdf). Metadata came from arXiv `/abs/` pages, Crossref, OpenAlex,
> the NIST TREC proceedings and IDEAS/RePEc. **OpenAlex hit its daily quota (HTTP 429,
> `x-ratelimit-remaining: 0`) part-way through**, so later records came from Crossref and publisher or
> author pages. ACM DL PDFs were bot-walled, so the IR papers were read from NIST, Cornell or author
> copies where those exist. No model endpoint was called. No git repository was touched. Paper A's
> `DRAFT.md`/`claims.json` were read (not edited) to state the pool composition correctly.
>
> **Review.** Unlike [12](12-uq-and-decision-models.md), this file has not had a separate red-team pass;
> the PR review is its first independent check. Its companion is bib inbox 12; both serve the same
> pre-proposal. The Paper A sentence in *Implications* is a draft and has not been checked against the
> qrels strata (see the open items there).

**Status key.** `verified (page)` = title, authors, venue and year read off the publisher page, the
arXiv `/abs/` page or the proceedings site. `verified (record)` = Crossref, OpenAlex, DBLP or RePEc
record only. `abstract` = the abstract was read. `read` = the text was retrieved, and the method,
results and limitations were checked.

**Our question, asked of every paper (Q):** *Does it give a usable remedy when labels or samples are
chosen in a way that depends on the quantity of interest? What does the remedy need: known
inclusion probabilities (IPW/HT), a joint model of selection and outcome (shared process), or a
non-preferential reference sample?*

---

## A–C. The three named papers

### `diggle-menezes-su-2010` (the anchor)
**Geostatistical inference under preferential sampling.** P. J. Diggle, R. Menezes, T. Su. *Journal of
the Royal Statistical Society Series C (Applied Statistics)* 59(2):191–232, 2010 (read paper, with
discussion). Status: verified (page) from the PDF title page · **read** (§1–6).
- Sampling is preferential when the point process that sets the sample locations and the process being
  measured are stochastically dependent. Their model: a stationary Gaussian process S, sample locations
  from a Poisson process with intensity exp(α + βS(x)), measurements Gaussian around μ + S(x). β = 0 is
  non-preferential.
- Simulation: with β > 0, conventional geostatistical prediction is strongly biased upward, because
  high values of S are oversampled.
- Galicia lead biomonitoring, 1997: Monte Carlo likelihood-ratio test of β = 0 gives 27.7 on 1 df
  (p < 0.001); β̂ = −2.198 (SE 0.336). Allowing for preferential sampling widens the predicted surface
  range from 1.286–5.976 to 1.310–7.654.
- (Q) Yes, through a joint (shared-process) model of selection and outcome; it needs the parametric
  form of the selection intensity. It also gives a test of whether sampling was preferential.

### `gelfand-shirota-2019`
**Preferential sampling for presence/absence data and for fusion of presence/absence data with
presence-only data.** A. E. Gelfand, S. Shirota. *Ecological Monographs* 89(3), e01372, 2019.
doi:10.1002/ecm.1372; arXiv 1809.01322 · **verified (page)** (arXiv /abs, Crossref) · **read**.

*Model.* Presence/absence (PA) is a binary surface at points: Y(s) = 1(Z(s) > 0). The sampling
locations S are a log-Gaussian Cox process with log λ(s) = wᵀβ + η(s). Preferential sampling (PS)
enters as a shared process: (d) Z = xᵀα + δη + ε, and (c) adds an independent GP ω. *Fusion.*
Presence-only (PO) points are a second LGCP, degraded by availability U(s) and sampling effort T(s)
following Chakraborty et al. 2011. The PA latent gets δ_PA·η_PA + δ_PO·η_PO (models e, f). Their
central argument is that PA and PO are *incompatible* data types: a PA "probability of presence" is
not a normalised PO intensity. So fusing by inducing PA from a PO point process (Fithian et al. 2015,
Dorazio 2014, Pacifici 2017) is, in their words, flawed.

*Results* (IPANE: 4,314 sites, 7 invasive plants; GBIF PO: 193–440 points per species). In model (d),
δ is clearly non-zero for 4 of 7 species: GB 0.225 [0.179, 0.274], JB 0.085, MR 0.037, AO −0.076
(under-sampled presences). **Once ω is added (model c), no δ is significant.** The flexible spatial
term absorbs the PS signal. On a 20 % random holdout (879 sites), Tjur R² improves mainly from ω,
not from η. (d) beats (a) "consequentially" only for GB (0.162 vs 0.126). (c) beats (b) modestly,
e.g. GM 0.164 vs 0.135. In fusion model (e), every δ_PO is significant and no δ_PA is. Predictive
performance is "essentially indistinguishable" across models other than (a), and (f) is best.
*Limitations they state or that follow from the text:* the effort surface is assumed (T = 1 in any cell
with ≥ 1 PO point of any species). Detection error (false absence or presence) is "beyond the scope".
The method is plants only, single species, with custom MCMC (NNGP, k = 15) and no software.
**(Q) Joint-model remedy.** It needs a model for *where* sampling happened (a point process over a
defined region) and works only through a shared latent field. It gives **no** IPW route. The paper's own
numbers show the PS term adds little once a flexible latent field is present. The PA/PO
incompatibility argument applies directly to our "LLM marks = presence-only, expert read =
presence/absence" analogy (see Implications iii).

### `shirota-gelfand-2022`
**Preferential sampling for bivariate spatial data.** S. Shirota, A. E. Gelfand. *Spatial Statistics*
51:100674, 2022. doi:10.1016/j.spasta.2022.100674; arXiv 2202.09168 · **verified (page)** · **read**
(intro, review, design, summary).

The paper extends PS to two responses with sampling sets S₁, S₂ that are identical, overlapping or
disjoint, each possibly preferential, and studies co-kriging. Its main point for us is that **PS can
bias inference on the dependence between responses (the cross-covariance), not only on means**, and
a shared-process specification "can clarify the true dependence behavior". To expose PS they fit on
**the 50 % of locations with the largest response values**, the spatial analogue of sending only the
hardest items to experts. The real data are 589 forest plots, with mean diameter (MDBH) and trees per
hectare (TPH) as the two responses. The models are compared by RMSE and CRPS.
**(Q) Joint model again, no weights.** It is relevant because H2 is a *dependence* estimand
(reliability vs correctness). Outcome-dependent selection can distort exactly that.

### `moreira-menezes-wise-2024`
**Presence-Only for Marked Point Process Under Preferential Sampling.** G. A. Moreira, R. Menezes,
L. Wise. *JABES* 29(1):92–109, 2024 (online 2023). doi:10.1007/s13253-023-00558-x · **verified
(record)** (Crossref, OpenAlex) · **read** (CC-BY copy from the U. Minho repository; no arXiv
version found).

The paper models two kinds of preferential sampling at once. Fishing events are presence-only points,
and the catch (mark) is itself preferential because fishers set nets on big schools. The model is
X ~ IPP(q·p·λ\*) observed, X′ ~ IPP(q·(1−p)·λ\*) unobserved, and U ~ IPP((1−q)·λ\*), with
logit p = W_obs β + γS(s) shared with the log-normal mark. Data augmentation gives an exact Poisson
likelihood. Sardine data, south Portugal: 1,024 observed events. γ is "strongly positive". The model
predicts **22,500–30,500 unobserved schools** and 19,860–35,383 t of unobserved biomass.
*Limitations they state:* the computation is slow and convergence is poor, and the NNGP covariance
parameters are not sampled, so uncertainty is understated. The data are not public. They suggest
fusing with a systematic survey to "insert unbiased data", and warn that this fusion is hard
(they cite Gelfand & Shirota 2019).
**(Q) Joint model with thinning.** Identification rests entirely on the parametric observability
model. The unobserved count is a 20–30× extrapolation from the observed one. The thing that would
anchor it, a non-preferential reference sample, is named as future work.

---

## D. Forward citations of Diggle 2010 (OpenAlex `cites:W2147351230`, 374 citing works; 8 kept)

### `pati-reich-dunson-2011` — *Biometrika* 98(1):35–48. doi:10.1093/biomet/asq067 · verified (record) · abstract.
This is the LGCP-locations model with the log-intensity entered as a regressor in the outcome model.
It proves posterior propriety for the PS coefficient and consistency of the location density and the
mean. **(Q)** Joint model, with consistency results, for continuous geostatistics only.

### `gelfand-sahu-holland-2012` — "On the effect of preferential sampling in spatial prediction." *Environmetrics* 23(7):565–578. doi:10.1002/env.2169 · verified (record) · abstract.
The paper argues that the harm of PS lies in the *predictive surface* more than in the parameters.
It simulates an "operating" site-selection intensity against complete spatial randomness. On ozone
data, roughly random sites validate much better than preferential ones. Vedensky et al. report that
it finds an informative covariate "does little to remedy the bias". **(Q)** It is a diagnostic and
design argument. The remedy it points to is a random design.

### `zidek-shaddick-taylor-2014` — "Reducing estimation bias in adaptively changing monitoring networks with preferential site selection." *Ann. Appl. Stat.* 8(3):1640–1670. doi:10.1214/14-AOAS745; arXiv 1412.1303 · verified (page) · abstract.
The setting is a finite superpopulation of candidate sites. Selection over time is modelled by logistic
regression, and the estimated inverse probabilities go into a **Horvitz–Thompson-style** estimator
(as described by Hsiao & Waller and Vedensky). Applied to UK black smoke 1970–1996, it shows
"dramatic reductions" in the estimated number of non-compliant sites. **(Q)** An IPW remedy with
*estimated* inclusion probabilities. It needs a finite frame and a selection model.

### `watson-zidek-shaddick-2019` — "A general theory for preferential sampling in environmental networks." *Ann. Appl. Stat.* 13(4):2662–2700. doi:10.1214/19-AOAS1288 · verified (record) · abstract.
This is a spatio-temporal joint model of the process and of site selection (including site removal),
fitted in R-INLA. It finds that the UK network was reduced in a response-biased way, with sites moved
to the most polluted locations. **(Q)** Joint model. It also *detects* PS (see Watson 2020/2021
tests, in *Searched*).

### `vedensky-parker-holan-2023` — "A Look into the Problem of Preferential Sampling through the Lens of Survey Statistics." *The American Statistician* 77(3):313–322 (online 2022). doi:10.1080/00031305.2022.2143898; arXiv 2203.05507 · verified (page) · **read**.
This is the bridge paper. Survey informative sampling (IS) comes with **known** selection probabilities.
Spatial PS rarely does, which is why the two literatures diverged. In simulation, a pseudo-likelihood
with **known** weights 1/p(sᵢ) (PKW) has the lowest prediction MSE. Kernel-estimated weights (PEW)
are "most reasonable in practice". The shared-process model (Pati) wins only when the data are
generated from it. They relay Conn, Thorson & Johnson 2017 (*Methods Ecol. Evol.* 8(11):1535–1546,
verified (record), abstract): **sampling according to a covariate and then including that covariate in
the model makes the design ignorable.** **(Q)** Yes. The cleanest remedy is known inclusion
probabilities. Selection on recorded covariates alone is ignorable.

### `hsiao-waller-2026` — "Inverse sampling intensity weighting for preferential sampling adjustment." *JABES* (2026). doi:10.1007/s13253-026-00730-z; arXiv 2503.05067 · verified (page) · **read** (intro, method).
This is two-stage ISIW: estimate the sampling intensity (kernel, optionally with a kriged-response
covariate), then use inverse weights in a Vecchia-approximated weighted likelihood, **winsorized**
against extreme weights. ISIW out-predicts shared-process models when the sampling design is
misspecified, and accurate parameter estimation correlated little with predictive accuracy. They note
that kernel intensity estimators "are not consistent for the true intensity without knowledge of
spatial covariates". **(Q)** IPW with *estimated* intensities. It is more robust than joint models,
but its weakest point is estimating π.

### `conroy-2023` — "A Shared Latent Process Model to Correct for Preferential Sampling in Disease Surveillance Systems." Conroy, Waller, Buller, Hacker, Tucker, Novak. *JABES* 28(3):483–501. doi:10.1007/s13253-023-00535-4 · verified (record) (Crossref, RePEc) · abstract.
The shared latent process links sampling locations with positive and negative specimen counts.
Simulations show reduced bias and better risk maps. Applied to plague (*Y. pestis*) surveillance in
California rodents. **(Q)** Joint model. This is the nearest disease-surveillance analogue for our bio
collaborators.

### `pollock-2025` — "Estimating Disease Prevalence from Preferentially Sampled, Pooled Data." Pollock, Hoegh, Irvine, de Wit, Reichert. *J. Data Science* 23:542–559 (2025). doi:10.6339/25-JDS1191 · verified (record) · abstract.
Bayesian PS correction for **pooled** presence/absence tests (coronavirus in Mexican free-tailed bats,
California). A naive model is biased, and theirs is "unbiased regardless of the degree of preferential
sampling" (simulation). **(Q)** Joint model. It is the closest published analogue to pooled
environmental samples such as wastewater composites, though it is not wastewater.

*Verified but not selected:* Dinsdale & Salibián-Barrera, "Methods for preferential sampling in
geostatistics", *JRSS C* 68(1):181–198 (doi:10.1111/rssc.12286, abstract). It shows that earlier
Monte Carlo likelihood approximations "may not be approximating the desired function" and recommends
numerical (Laplace/INLA-type) approximations, on the Galicia data. Useful if we ever fit Diggle's
model, but it offers no new remedy.

---

## E. IR pooling bias and sampling-based evaluation

### `zobel-1998` — "How reliable are the results of large-scale information retrieval experiments?" SIGIR 1998, pp. 307–314. doi:10.1145/290941.291014 · verified (record) · **read** (Cornell course copy).
Relative system rankings hold up. Pooling bias "does not appear to have a significant impact" on
comparing new systems. But "at best 50 %–70 % of the relevant documents have been discovered", and
"assuming unjudged documents to be irrelevant is questionable". Recall is overestimated. **(Q)** No
estimator. It diagnoses the closed-world assumption and proposes a pooling allocation that finds more
relevant documents.

### `buckley-voorhees-2004` — "Retrieval evaluation with incomplete information." SIGIR 2004, pp. 25–32. doi:10.1145/1008992.1009000 · verified (record) · **read** (NIST copy).
bpref scores on judged documents only, by how often judged-non-relevant documents rank above
relevant ones. Robustness was tested by **random** down-sampling of qrels (to 1 %). **(Q)** bpref is
robust to *random* incompleteness. Nothing in it addresses preferential incompleteness, and no
inclusion probabilities are used.

### `buckley-dimmick-soboroff-voorhees-2007` — "Bias and the limits of pooling for large collections." *Information Retrieval* 10(6):491–508. doi:10.1007/s10791-007-9032-x · verified (record) · **read** (NIST copy).
As collections grow, a fixed-depth pool becomes a *biased* sample. On AQUAINT, judged relevant
documents favour topic-title words (titlestat_rel 0.719 vs 0.588 on Disks 4&5). They propose random
or stratified sampling deeper in the rankings, but note "how this interacts with bias is unknown" and
that "it is currently unknown how to construct a fair sample" by down-sampling. **(Q)** It names the
problem and points to sampling with known probabilities, without solving it.

### `yilmaz-aslam-2006` (infAP) — "Estimating average precision with incomplete and imperfect judgments." CIKM 2006, pp. 102–111. doi:10.1145/1183614.1183633 · verified (record) · **read** (NIST TRECVID draft, "Inferred AP").
AP is recast as the expectation of a random experiment and estimated from a **uniform random sample
of the depth-100 pool**. The pool is explicitly assumed to contain "all documents assumed to be
relevant". **(Q)** Yes, with known inclusion probability (uniform), **but only inside the pool**.
Documents outside the pool are still non-relevant by assumption.

### `aslam-pavlu-yilmaz-2006` (statAP) — "A statistical method for system evaluation using incomplete judgments." SIGIR 2006, pp. 541–548. doi:10.1145/1148170.1148263 · verified (record) · **read: slides only** (author's site; the paper PDF was not reachable).
Measures are treated as outcomes of a random experiment. A **non-uniform** sampling distribution over
documents is chosen for low variance, and the estimate is "unbiased by statistical design". Buckley
et al. 2007 describe the sample as drawn "according to a distribution over pairs of pooled
documents". **(Q)** Yes: an importance-sampling (HT-type) estimator with **known, non-uniform**
inclusion probabilities. It is the IR ancestor of active testing.

### `yilmaz-kanoulas-aslam-2008` (xinfAP / infNDCG) — "A simple and efficient sampling method for estimating AP and NDCG." SIGIR 2008, pp. 603–610. doi:10.1145/1390334.1390437 · verified (record) · content via the TREC CDS overviews only.
### `voorhees-2014` — "The effect of sampling strategy on inferred measures." SIGIR 2014, pp. 1119–1122. doi:10.1145/2600428.2609524 · verified (page) (NIST record) · abstract (paraphrase on the NIST page).
Top-K pooling is competitive but not best. A deep, very sparsely sampled stratum is a poor choice.
Designs that include a top-10 stratum make better collections.

### `sakai-2007` — "Alternatives to Bpref." SIGIR 2007, pp. 71–78. doi:10.1145/1277741.1277756 · **verified (record) only.**
The paper was not read, so its "condensed list" method is **not** described here from the source. The
brief's characterisation is unchecked.

### TREC CDS overviews (the qrels Paper A rests on) · verified (page) · **read**
Simpson, Voorhees & Hersh, *TREC 2014 CDS overview*. Roberts, Simpson et al., *TREC 2015*. Roberts,
Demner-Fushman et al., *TREC 2016* (trec.nist.gov, trec23/24/25). Judgments are a **two-stratum
sample**: every document ranked 1–20 (2016: 1–15) by any run, together with **a 20 % sample** of the
remaining documents ranked up to 100 by some run. The design follows Voorhees 2014, for inferred
measures. The primary metric is infNDCG. 2014: 102 runs, 34,949 judgments, 1,265 per topic on
average (908–1,669). **(Q)** The official qrels **already carry known inclusion probabilities** (1, 0.2
or 0 per stratum) **within the run pool**. Outside depth 100 of every submitted run, π = 0, and no
weighting can recover those documents.

---

## F. Active / importance-weighted evaluation with expert labels

### `kossen-2021-active-testing` — ICML 2021; arXiv 2103.05331 · verified (page) · **read** (§2–3, App. D). Also in [08](08-adaptive-budget-escalation.md).
Test points are acquired with a **stochastic** proposal q(i_m), which may depend on everything seen so
far. The LURE estimator weights each loss by v_m = 1 + (N−M)/(N−m)·(1/((N−m+1)q(i_m)) − 1).
**Unbiasedness condition:** selection is random with *recorded* q. "There are no restrictions on how q
can depend on the data", and positivity is needed wherever the loss is non-zero. The optimal proposal
is q ∝ expected loss. **Sampling hard items more is variance-optimal, provided it is randomised and
weighted.**

### `kossen-2022-ase` — "Active Surrogate Estimators." NeurIPS 2022; arXiv 2202.06881 · verified (page) · abstract.
The estimate interpolates the errors of unlabelled points with a learned surrogate (XWED acquisition)
instead of a Monte Carlo estimator. **(Q)** Unbiasedness then depends on the surrogate, not on the
design. Lower variance, but it is a model-based remedy.

### `sawade-2010-are` — "Active Risk Estimation." Sawade, Landwehr, Bickel, Scheffer. ICML 2010, pp. 951–958 · verified (record) (DBLP / mlanthology via search; the icml.cc PDF link returns 404) · content only through Kossen 2021 App. D.
The optimal importance-sampling distribution is q\* ∝ p(x)·√∫(L − R)² p(y|x) dy. It samples *with*
replacement, which Kossen et al. call suboptimal for finite pools. **(Q)** IPW with a known proposal.

### `farquhar-2021` — "On statistical bias in active learning: how and when to fix it." ICLR 2021; arXiv 2101.11665 · verified (page) · abstract. It is the source of LURE.

### `zrnic-candes-2024` — "Active Statistical Inference." ICML 2024; arXiv 2403.03208 · verified (page) · **read** (§2, §7.2). Also in [08](08-adaptive-budget-escalation.md).
The label for item i is collected with probability π(Xᵢ) ∝ model uncertainty. The estimator
θ̂ = (1/n)Σ[f(Xᵢ) + (Yᵢ − f(Xᵢ))ξᵢ/π(Xᵢ)] (AIPW) is **unbiased for any π, given known π > 0**.
They **mix π with a uniform rule (τ-mixing) so π stays bounded away from 0**. **(Q)** This is the
direct template for "LLM judge everywhere, expert on a π-sample".

---

## Work that challenges us

- **Gelfand & Shirota's own numbers.** The PS term is significant only without a flexible latent field.
  With ω, every δ is null and the predictive gain is small. A joint "selection + outcome" model may
  buy little over a good outcome model, so our case for PS machinery must rest on *design*, not
  modelling.
- **Lu, Xu, Yang & Guan 2025**, "On ignorability of preferential sampling in geostatistics" (arXiv
  2511.03158, verified (page), **read** intro and conclusion). Under Diggle-type models, OLS slopes and
  kernel variograms stay **unbiased and consistent even when PS is ignored**. **Only the intercept is
  biased**, by the cross-covariance at lag 0. Read for us: a *conditional* relationship (correctness
  given reliability) can survive outcome-related selection under strong distributional assumptions.
  A *marginal* accuracy (the intercept or prevalence) cannot. It is a preprint, and Gaussian/LGCP-specific.
- **Hsiao & Waller 2026.** Correct parameter estimation was nearly uncorrelated with good prediction.
  A remedy that fixes one may not fix the other.
- **Gelfand & Shirota 2019 vs Fithian et al. 2015** (*Methods Ecol. Evol.* 6(4):424–438, verified
  (record)). The fusion literature disagrees about whether PA and PO can share one model. Our proposed
  fusion would inherit that dispute.
- **Zhang, Hung, Ding & Yeh 2026**, "Quantifying and auditing LLM evaluation via positive–unlabeled
  learning" (arXiv 2606.19057, verified (page), abstract). Already frames selective human supervision
  of LLM judges as PU learning, which pre-empts any novelty claim for "presence-only" framing of
  judge labels.
- **Ao, Chen, Gao, Li & Simchi-Levi 2026**, "Best arm identification with LLM judges and limited human"
  (arXiv 2601.21471, verified (page), abstract). Proxy + inverse-propensity-weighted residuals, with
  audits concentrated on unreliable contexts. It is published prior art for our escalation-to-expert
  design.

## Searched and did not find

- **Wastewater or metagenomic surveillance under preferential sampling.** OpenAlex
  `cites:W2147351230` + `title_and_abstract.search:wastewater` returned 0. arXiv "wastewater preferential
  sampling" returned 0. A web search found only representativeness studies (sewershed vs census
  population; population-weighted sub-city means, e.g. medRxiv 10.1101/2023.10.06.23296680) and no PS
  or IPW estimator. Nearest: Pollock 2025 (pooled samples), Conroy 2023 (zoonotic surveillance), and
  Hopkins, Teichman, Graham & Willis 2025 (arXiv 2510.23920, abstract). The last allows *both units and
  categories* to be preferentially sampled in multi-category (compositional, microbiome) mean ratios,
  using doubly robust TMLE. It is the closest metagenomics-side hit and is worth handing to the bio
  collaborators.
- **"preferential sampling" + "relevance judgments"** (arXiv): 0. IR uses its own vocabulary (pooling
  bias, incomplete judgments, inferred measures), and the two literatures do not cite each other.
- **"presence-only" + annotation/LLM** (arXiv): 42 hits, all irrelevant. "positive-unlabeled LLM judge":
  1 hit (Zhang 2026, above).
- **Sakai 2007 full text**, **statAP paper PDF**, **Sawade 2010 PDF**: ACM DL bot wall; the icml.cc
  link returns 404. **COULD NOT VERIFY content** for Sakai 2007 (queries: web "Alternatives to Bpref"
  condensed list; Semantic Scholar DOI, no abstract). The record is verified.
- Watson 2020 (arXiv 2003.01319, "A fast Monte Carlo test for preferential sampling", PStestR; abstract)
  and Watson 2021 (*Spatial Statistics* 43:100500, verified (record)) were found but not kept among the
  eight. They provide a test usable with n ≈ 50 for non-Gaussian data.

## Established names for what we invented

| our phrase | established name | anchor |
|---|---|---|
| experts read items chosen by difficulty | **informative sampling** (survey); **preferential sampling** (spatial); **outcome-dependent / response-biased sampling**; MNAR selection | Vedensky 2023; Diggle 2010 |
| weight expert labels by 1/P(selected) | **Horvitz–Thompson / IPW**; with model predictions, **AIPW**; for a mean, **prediction-powered inference** | Horvitz & Thompson, *JASA* 47:663–685, 1952 (verified (record)); Angelopoulos et al., *Science* 382:669–674, 2023 (verified (record)); Zrnic & Candès 2024 |
| send uncertain items to experts, stay unbiased | **active testing** / **active risk estimation** / **active statistical inference** | Kossen 2021; Sawade 2010; Zrnic & Candès 2024 |
| unmarked sentences ≠ absent | **presence-only** (ecology); **positive–unlabeled (PU) learning**, SCAR assumption | Gelfand & Shirota 2019; Elkan & Noto, KDD 2008 (verified (record)) |
| unjudged = non-relevant | **closed-world / completeness assumption** of pooling | Buckley & Voorhees 2004; Zobel 1998 |
| a random stratum outside the pool ("bias bound") | **stratified sampling for inferred measures** | Yilmaz, Kanoulas & Aslam 2008; Voorhees 2014 |
| selection only on recorded features is harmless | **ignorable design** / selection on observables (MAR given X) | Conn 2017 via Vedensky 2023 |

## Implications

**(i) Expert-sampling design for the LDRD (150–200 items).** Use a **stratified random design with
recorded inclusion probabilities**, not a hand-picked or threshold-escalated gold set.
1. Stratify on quantities computed *before* any expert sees an item: judge reliability at budget k
   (deciles or tertiles), the H1 features, topic, and the bio sub-domain. Oversample the low-reliability
   strata. Kossen and Zrnic show that oversampling where the judge is likely wrong is variance-optimal
   **if** the draw is random and π is recorded.
2. Keep **every π ≥ a floor** (τ-mixing with uniform, e.g. τ = 0.2), so no stratum has π = 0 and
   weights stay bounded. Pre-register the π table and a winsorization rule (Hsiao & Waller).
3. Estimate marginal quantities (overall judge accuracy, expert–judge agreement) with HT/IPW, or AIPW
   with the judge as f(X). Estimate H2, the calibration of reliability against correctness, as a
   regression of correctness on reliability. Selection on reliability itself is then ignorable for that
   conditional curve (Conn 2017; consistent with Lu 2025). Report a weighted and an unweighted fit, and
   treat a disagreement between them as a sign of model misspecification.
4. **Escalated items** (sent to experts by the policy because the judge was unsure) are usable for H2
   **only if** escalation was a recorded function of pre-expert covariates. Then they are a stratum with
   known π (1 above the threshold). **On their own they cannot estimate H2**: below the threshold π = 0,
   so the calibration curve is unidentified there (positivity fails). Shirota & Gelfand show that this
   kind of response-adjacent selection can distort the dependence estimate. Pool them with the random
   strata and use the design weights. Never let a human screener's "this looks wrong" choose items.

**(ii) Threats-to-validity sentence for Paper A (pooled qrels).** *Corrected in review.* Every Paper A
pair is a TREC-judged-relevant document (grade ≥ 1). The pre-registration (`docs/plans/results/design/SPEC-confirmation-run.md`
§6.3) defines *pooled* pairs as every grade ≥ 1 document in the union of the top 20 of Paper A's own
scoring arms, and *bias-bound* pairs as up to 10 further grade ≥ 1 documents per topic drawn from
outside **those** pools. So the bias bound measures how much judged evidence Paper A's retrieval pools
miss; it says nothing about documents TREC never judged, and Paper A contains no unjudged documents.
The inherited threat is that the population is restricted to TREC-judged-relevant documents, which TREC
selected by an unequal-probability design. A draft sentence:
> *Every pair uses a document TREC CDS assessors judged relevant. Those judgments cover a stratified
> sample of the submitted runs' top-ranked documents: every document ranked 1–20 (1–15 in 2016) by any
> run, plus a 20 % sample of the rest to depth 100. Our population is therefore the judged-relevant
> documents, not all relevant documents, and documents from the 20 % stratum entered it with probability
> 0.2 (Zobel 1998; Buckley et al. 2007). We report results unweighted and weighted by the inverse of the
> stratum probability.*

The stratum is available: `trec.nist.gov/data/clinical/qrels-sampleval-2014.txt` has the format
`topic 0 docid stratum grade`, with stratum 1 (all judged) or 2 (sampled) and grade −1 for unjudged
(PR review, 2026-10-13: 23,105 stratum-1 documents, all judged; 72,922 stratum-2 documents, of which
14,844, i.e. 0.204, judged). Weighting Paper A's pairs by stratum is therefore mechanical; the 2015 and
2016 files should be checked for the same column before it is done. The count of rankings in the
development pools is not in the confirmation artifact (`conf-a/pool_counts.json` gives 76 for the
confirmation run), so the sentence does not quote it.

**(iii) Is presence-only/presence-absence fusion a viable H2 method?** **Not as the primary analysis.**
Gelfand & Shirota's coherent fusion needs a point-process model for the presence-only source and a
shared latent field. In their data the presence-only term was significant, but prediction was
"essentially indistinguishable". Moreira's version identifies the unobserved part only through a
parametric observability model and extrapolates 20–30×. For us, the LLM's unmarked sentences are
closer to PU data with a *known* labelling mechanism (we control the reading protocol), so the simpler
remedy is design: the expert full read on a π-sample is the presence/absence reference sample that
every one of these papers wishes it had. Fusion is worth at most a **secondary, exploratory** model:
LLM marks as presence-only, expert reads as presence/absence, linked by a shared per-document latent.
Report it only if it beats the AIPW estimate on held-out expert items. Any novelty claim must
cite Zhang et al. 2026 (PU framing of LLM evaluation).
