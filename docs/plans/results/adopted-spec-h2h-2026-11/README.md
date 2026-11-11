<!-- experiment: Adopted chunking spec head-to-head (S vs ft512o64s vs ft512o64; S-regex segmenter) | 2026-11-11 | DRAFT · NOT FROZEN -->
# Adopted-spec head-to-head: does S beat the OA eval's pick?

**Status: DRAFT, not frozen (PREREG v5, 2026-11-11).** Nothing of §7 has run. The sentinel dry run and the
study-text sentinel pass of 2026-11-11 both passed (PREREG §2); every §9.1 pin is in `frozen-inputs.json`
(none left null); the tooling is committed and tested. The freeze waits on the owner's statement and the
bookkeeping under *What remains for the freeze*.

The question (PREREG §1): on the OA eval's frozen decision set (n = 2,035, hit@5, setting (a)), is the adopted
production chunking **S** (`sentence` 512 tokens, overlap 0, `size_tolerance` 0.25, `min_piece` 128,
`structure_cuts sections/1`, Punkt) non-inferior to, and then superior to, the OA eval's pick **ft512o64s**
(`fixed_token` 512/64 + section cuts), with **ft512o64** (the server default) as the baseline and drift gate,
and does the regex sentence segmenter (**S-regex**, the same spec on a worker without `nltk`) change the
answer? **sent512s** is a secondary arm. The decision maps to {deploy S, deploy ft512o64s}; the owner ruled
cost order **B** (a tie deploys S unless the frozen ladder finds ft512o64s superior), and the sentence always
states what the other order would have decided.

| file | tier | what |
|---|---|---|
| [PREREG.md](PREREG.md) | record, once frozen | hypotheses, arms, images, gates, the decision rule under both cost orders, the §4.5 sentences, power, procedure, freeze, owner decisions |
| [frozen-inputs.json](frozen-inputs.json) | record, once frozen | every §9.1 pin that is a hash or a count: shard files, question-id lists, `noise.json`, configs, the two workflow texts, the two images, the `.def`, the sentinel counts, the reused-arm census, the model ids, the re-embed sample hash (nothing null) |
| [ingest-bulk-h2h.cwl](ingest-bulk-h2h.cwl) | record, once frozen | the study workflow text: `cwl/ingest-bulk.cwl` at v1.6.25 plus a per-shard stderr `File` output; the S text as committed, the S-regex text = the six image-name lines renamed (both sha256 in `frozen-inputs.json`) |
| [ragstack-tools-nonltk.def](ragstack-tools-nonltk.def) | record, once frozen | the recipe of the S-regex image (`ragstack-tools-v1.6.24-b2.sif`): the v1.6.24 tools def without the `chunking` extra, with build-time assertions and a variant label |
| [config-diff.json](config-diff.json) | record, once frozen | the key-by-key diff of the decision (3-arm) and reporting (5-arm) harness configs against the Addendum C configs, per setting; written by `h2h_report.py configs` |
| [power.py](power.py), [power.json](power.json) | record, once frozen | the §5 planning numbers (standard library, deterministic, no data) |
| `python/scripts/eval/h2h_report.py`, `python/scripts/eval/h2h/` | code | the §9.4 script: `report`, `dryrun`, `reembed`, `configs`; tests in `python/tests/eval/test_h2h_report.py` |
| `RESULTS.md`, `artifacts/h2h-report.json`, `REPORT.md`, `DEVIATIONS.md` | after the run | not yet written |

## What is pinned (2026-11-11, from files and the dry run)

- **Versions.** `dev` server v1.6.25 (`67e53fd`; `GET /v1/version` 2026-11-11), tools tag v1.6.24 (`5d65bead`), GoWe v0.26.1.
- **Images.** S: `ragstack-tools-v1.6.24-b1.sif`, build 1, sha256 `ecf81440…`, `punkt-abbrev-v1`. S-regex:
  `ragstack-tools-v1.6.24-b2.sif`, build 2, sha256 `413f6ec3…`, `regex-abbrev-v1`, built 2026-11-11 from
  the `.def` beside this file (sha256 in `frozen-inputs.json`), placed in the dev worker group's image
  directory by management; never in the release store, never named by a registered workflow.
- **Workflow texts.** S: `ingest-bulk-h2h.cwl` as committed. S-regex: the same file with each of the six
  `ragstack-tools-v1.6.24-b1.sif` lines replaced by `ragstack-tools-v1.6.24-b2.sif` (a plain string
  replacement; `test_study_workflow_texts_pinned` checks that nothing else differs). Both sha256 are in
  `frozen-inputs.json`. Why a study copy: GoWe keeps only the last 256 KB of a task's stderr in its database,
  and the once-per-process `NLTK Punkt is unavailable` warning is written early, so on a 16,000-document
  shard the tail alone cannot carry the §6 gate; the study copy declares `stderr:` on the `ingest_shard` tool
  and stages the full stream out as `<shard>.ingest-stderr.txt` (GoWe tees into the file and the tail).
  Consequence: **S does not run dev's registered workflow id**; both arms are direct submits of the study
  text, as the Addendum C arms were direct submits of dev's text.
  No server-side gate inspects the submitted text; the image is enforced by the receipt gates and the pinned
  sha256. Comments aside, the study text is the v1.6.25 base plus exactly the stderr edits (a test checks it).
- **Input.** The 16 main shards (241,930 records) and the sentinel shard (40 records: 5 articles, 4 tables,
  31 figures), sha256 per file. Sentinel doc ids also in the main shards: **0**. Sentinel chunks under the S
  spec: **168** on both arms (dry run), with **identical chunk ids** across the two segmenters.
- **Question sets.** Decision n = 2,035, sensitivity n = 2,335, as sorted-id-list sha256 (one id per line,
  LF-terminated, UTF-8); 2,437 rows in all.
- **Configs.** Per setting a decision config (ft512o64 baseline rank 1, S rank 2, ft512o64s rank 3; Order B)
  and a reporting config (those three + S-regex rank 4 + sent512s rank 5, scored only), derived from the
  Addendum C configs by `h2h_report.py configs`: only `arms`, `out_dir` and the `_` comment keys differ
  (`config-diff.json`); `decide` is asserted equal (k 5, `min_stratum_n` 30, `bonferroni`,
  `paired_ladder`), `margins`, `api` (request fields, `top_k`), `score.exclude_noise_subset`, `noise`,
  `sample`, `passages` and every path key are copied. The configs themselves are private (they hold hosts and
  paths); their sha256 and the frozen `noise.json` sha256 per setting are in `frozen-inputs.json`.
- **Reused arms, model ids, re-embed sample.** Read 2026-11-11; see *The dry run* below and `frozen-inputs.json`.
- **Passages segmenter.** `punkt-abbrev-v1` (so the H3 bias favours S, PREREG §3).
- **Create body.** No `embedding` key: dev's config default resolves `Salesforce/SFR-Embedding-Mistral`
  4096-d `openai`; the registry entry's `chunk_params` and `spec_hash` (`29236085`) are in
  `frozen-inputs.json`.

## The dry run (2026-11-11)

Two scratch collections on `dev` with the exact spec, the sentinel shard ingested into each: S through dev's
registered v1.6.25 text, S-regex through the same text stamped with the b2 receipt. Both: 40 documents, 0
failed, 168 chunks; `image_commit` equal, `image_build` 1 vs 2, `segmenter_version` `punkt-abbrev-v1` vs
`regex-abbrev-v1`; the full `chunking` object (`v 1, sentence, 512, 0.25, 128, ceiling 640, sections/1,
window_budget 4080`); worker stderr via the GoWe logs endpoint 0 vs 1 warning lines (1.5 / 1.9 KB);
Qdrant = Elasticsearch = receipt = 168; identical `chunk_params` on the two registry entries; the scratch
collections purged (`?purge=true`; a plain `DELETE` is refused with 409 when no other entry claims the store),
listing clean, stores 404. `h2h_report.py dryrun` reproduces every check from the saved records (private).
The identical chunk ids are an observation on 40 documents and what they imply for H3 is stated in PREREG §2.

**Study-text sentinel pass (2026-11-11, after the dry run).** The two study texts (sha256 as pinned; S-regex =
the six image-name lines renamed) submitted on the sentinel shard against two further scratch collections with
the same spec: both completed, 40 documents, 0 failed, 168 chunks, chunk ids and `chunking` objects identical
to the dry run's; the staged `oa-eval-part-00.ingest-stderr.txt` present for both ingest tasks, file and logs
endpoint agreeing (S 1,501 B / 0 lines; S-regex 1,765 B / 1 line); Qdrant = ES = 168; scratch collections
purged, listing clean, stores 404. The study-text precondition of PREREG §7 step 1 is met.

**Read-only live reads (2026-11-11), now in `frozen-inputs.json`:** dev `/v1/version` 1.6.25 / `67e53fd`;
reused-arm census (registry = Qdrant = ES, sentinel documents included) ft512o64 705,204, ft512o64s 859,280
(= 859,087 + 193 sentinel, from its 17 receipts), sent512s 850,312; embedding model
`Salesforce/SFR-Embedding-Mistral` (vLLM, all six endpoints, `max_model_len` 4096); reranker
`BAAI/bge-reranker-v2-m3` (crossencoder sidecar `/health`; that dev uses this sidecar is inferred from the
deployment, not read from dev's config); serving build and reranker image not exposed by the endpoints;
re-embed sample drawn (seed 20261111) from the 859,280 distinct chunk ids of the ft512o64s receipts
(sentinel included, which changes nothing: every chunk has a stored vector), `ids_sha256 41d5f254…`.

## What remains for the freeze (PREREG §9.1–§9.2)

| item | who | notes |
|---|---|---|
| Place the private configs (+ `noise.json` copies), the manifest template and the private re-embed `ids.json` in the private repository | operator | the configs' and the sample's sha256 are already in `frozen-inputs.json` |
| Harness changes since d2a8b9b | operator, at the freeze commit | list them here from `git log -- python/scripts/eval/oa_chunk_eval` |
| `power.json` regenerated from a clean tree at the freeze commit (`python3 -I -B power.py --out power.json`; must be byte-identical) | operator | `test_power_reproduces_prereg_section_5` already checks the committed file equals what `power.py` writes |
| **The freeze statement** | owner, in writing | names the commit, cost order **B**, server **v1.6.25**, tools tag **v1.6.24**; then `sha256sum PREREG.md` + UTC time into a private `PREREG.sha256`, the signed tag `prereg-adopted-spec-h2h-2026-11` with every §9.1 sha256, and this file's status line rewritten once |

## What runs after the freeze (PREREG §7)

1. Management creates `oa-eval-s` and `oa-eval-sregex` on `dev` (admin, owner's typed go) with the create
   body of §7 step 2; the operator records the listing and the tenant-wide count.
2. Two direct `gowe submit`s of the study text (S: the committed file; S-regex: the b2-renamed copy), the
   sentinel shard first — `h2h_report.py dryrun --arm … --receipt … --stderr-file … --logs-json …
   --qdrant-count … --es-count … --collection-entry … --expect-build 1|2 --expect-sentinel-chunks 168` — then
   the 16 main shards. Every ingest task leaves a receipt and a stderr file in its stage-out directory.
3. `retrieve` with the five-arm reporting config per setting (48,740 calls); copy the three decision arms'
   ledgers into the decision `out_dir`; `score` both configs; `decide` the decision config, per setting.
4. The operator fills a private manifest (paths of the receipts, stderr files, store counts, shard files,
   cosines; see the `report` module doc) and runs
   `h2h_report.py report --manifest … --frozen-inputs frozen-inputs.json --prereg PREREG.md
   --prereg-sha256 <frozen sha> --out artifacts/h2h-report.json`. Its output is the decision; the §4.5
   sentence and the H3 sentence go verbatim into `RESULTS.md`; `REPORT.md` follows in the owner's five-part
   order.

Until the script has written its output no one reads an S or S-regex per-question file or score (PREREG §6,
*Operator exposure*).
