# Queued: Leg B and Leg C pilots (Phase-0 items 4, 5, and the §7a oracle)

**Trigger:** launch when the stage-1 grid run completes. Resource profile is Scout LLM +
CPU, with one short crossencoder pass — so it does not contend with the grid's embedding
fleet, but it is queued behind it per the sequential order agreed.

## Why these two legs matter more than usual now
Stage 1 on Leg A is **provisional**: CDS relevance is document-level and topical, and Leg A
has a *measured* preference for coarse, aboutness-carrying configs. Legs B and C control
evidence position **by construction**, so they are the only thing that can tell us whether
Leg A's coarse-wins direction is a real retrieval effect or that bias. Until they run, no
config may be pruned.

## Leg B pilot — 50 generated queries
- Generate from **deep structural sections** of PMC OA articles (`/rag/oa/corpus/xml/`,
  1,439,753 files). **Never from any chunker's output** — generating from chunks would bias
  the protocol toward the generating chunker, which is the whole circularity threat.
- Reuse the paraphrase-first two-pass + IDF-overlap machinery in `g1_make_queries.py`.
- **Abstract-answerability rejection pass**: discard any query answerable from title+abstract.
  This is what stops Leg B inheriting Leg A's defect.
- Record source section and its token offset by construction → position-of-evidence is known,
  not inferred.
- Measure: yield rate, per-filter hit rates, and a **manual read of all 50** — the pilot's
  point is to find out whether the generated queries are actually good, which no metric shows.

## Leg C pilot — 50 citances
- Mine in-text citation contexts; the cited article is the relevant document.
- Resolve via **pmid + pmcid + doi** (the recorded 11.8% is a pmid-only floor — measure the
  true rate).
- Filter to pairs whose evidence sits in the cited paper's **body**, not its abstract.
- Measure: true resolvability rate, position-filter survival rate.

## Also outstanding from Phase-0 (§13.5 of the plan says these have no number on any leg)
- **§7a section-level oracle** — crossencoder position-of-evidence histogram. Cheap:
  ~658 pairs/s measured, so a 2,000-pair sample is seconds. This closes checks 2 and 5.
- **Empirical σ_d** for the power table — currently assumed, not measured. The stage-1 run
  should provide per-query arrays to derive it from.

## Constraints (unchanged)
- Dev tenant only (`:24041` / `:24043`); never `:6333` / `:9200`.
- `/rag/envs/ragstack/bin/python`, `HF_HOME=/rag/cache`, `PYTHONPATH` pinned.
- GPUs 6 and 7 are RESERVED — do not use, do not start endpoints.
- Nothing written under `/rag/`; scratch only.
- Read-only against `/rag/oa`.
