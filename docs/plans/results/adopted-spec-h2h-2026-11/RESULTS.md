# Results: the adopted chunking spec (S) head-to-head with the OA eval's pick (ft512o64s) and the server default (ft512o64); the regex segmenter (S-regex) (run 2026-11-11/12)

**Outcome: tie at H2, decision S under the frozen cost order B** (PREREG §4.1 row 3, *S (tie, Order B)*): S is
non-inferior to ft512o64s at the 2.0-point margin (H1 holds), not shown superior (H2 fails), and ft512o64s is not
shown superior to S, so the tie goes to the cheaper arm. Gate G1 held; no validity gate fired (`settings.a.gates`
and `files`-level gates are empty). **H3: S may be deployed on any worker** (on this corpus): S-regex − S is within
the margin and the stratum guard holds; the chunk-id overlap between the two segmenters is 99.2 %. Under cost
order A the decision would have been ft512o64s. The three secondary settings agree with (a) on the decision
(`secondary_settings_disagreeing_with_a` is empty). Pre-registration: [`PREREG.md`](PREREG.md), frozen at
`f4d4ef9a` (sha256 `31687ee4…6f82`, signed tag `prereg-adopted-spec-h2h-2026-11`; freeze record in
[`README.md`](README.md)).

Every number below is read from the committed [`artifacts/h2h-report.json`](artifacts/h2h-report.json), the
frozen script's output; the field is named in backticks after the number. Counts marked `ingest-summary:` are
from [`artifacts/ingest-gates-summary.json`](artifacts/ingest-gates-summary.json), a counts-only summary the
write-up derived from the private run records (ingest gate file, GoWe status listings, sentinel checks, re-embed
cosines). Question counts behind a rate (*S only*, *ft512o64s only*) were derived by the write-up from the private
per-question files and reproduce the report's point estimates and discordances exactly; they are marked ‡.
Operator times marked † come from the private run directory's timestamps and logs and are in no committed
artifact. Deviations: [`DEVIATIONS.md`](DEVIATIONS.md) D-1 to D-6.

## Provenance

| step (PREREG §7) | UTC | record | commit |
|---|---|---|---|
| 1 freeze | 2026-11-11 22:30:22 (private `PREREG.sha256`); tag signed 22:31:08 | the signed tag; the freeze record now in `README.md` (D-6) | `f4d4ef9a` (merge of #1004) |
| 2 `dev` `/v1/version` and the GoWe fleet read; the two collections created | 22:31:31; 22:31:53† | private `version-at-run.json`, `gowe-fleet-at-run.json`, `create-*.json` | — |
| 2 sentinel shard, both arms: submitted; records read; `dryrun` checks passed | 22:32:38; 22:33:53; 22:34:03† | private `sentinel/<arm>/`; `ingest-summary: sentinel` | — |
| 2 main shards, both arms: submitted; S complete; S-regex complete | 22:34:17; ≈ 23:33:47; ≈ 23:48:47† (submit time + GoWe wall 3,569.8 s / 4,469.6 s) | private receipts, staged stderr, status listings; `ingest-summary: arms` | — |
| 2 re-embed gate: `reembed fetch` (the one live step) | 22:38:32† | private `reembed/cosines.json` (`files.reembed`) | `d9051640`, the fix of D-1 |
| 2 ingest gates read (store counts, task records) | 23:50:47 – 23:51:15† | private `main/ingest-gates.json`; `ingest-summary: arms` | — |
| 3 retrieval, 48,740 calls (12,185 per setting, five arms) | first attempt 23:52:08 made 0 calls (D-2); 23:53:12 – 00:27:51 (a, d, b, c in that order) | private retrieval ledgers; `settings.<s>.missing_by_arm` | `f4d4ef9a` |
| 4 `score` (five-arm, then three-arm) and `decide`, per setting | 00:29:03 – 00:31:44† (a, d, b, c), exit 0 each | private `score.json`, `per_question.jsonl`, `decision.json` | `f4d4ef9a` |
| 5 `report`, once | 00:33:47†, exit 0 | `artifacts/h2h-report.json` | `f4d4ef9a` (`files.harness_provenance`) |
| 6 this file | — | — | this commit |

- **Code state.** The report records `files.harness_provenance`: commit `f4d4ef9a0b3b33116744488766f4e28d51aa51b3`,
  `dirty: false`, `untracked: 0`, `citable: true`, version `v1.6.26+f4d4ef9a`. The script has no `recorded_at`
  field; the time above is the file's. It embeds no `in_image` flag (the harness and the script ran from a checkout,
  not from a tools image).
- **PREREG.** `prereg.checked` true, `prereg.prereg_sha256`
  `31687ee41b036010a49975a1a4b215841e67aa5e5c6e5a979feb91c0160f6f82`, the frozen sha256.
- **Server.** One version across every row of every setting, three-arm and five-arm:
  `1.6.25+67e53fd4d5dd552eb3d22fa4c8554bff1748d738` (`server_version`, `settings.<s>.server_versions`), the version
  named in the freeze statement.
- **Reproduction from files.** For this write-up the frozen script was re-run from the same private inputs at
  `8d0dd703` (main after #1006; the script and its package are unchanged since `f4d4ef9a`). Its output equals the
  committed artifact in every field except `files.harness_provenance`, which names the other commit.
- **Private companion.** The run directory (receipts, stderr, store counts, task listings, cosines, ledgers,
  per-question files, the manifest); the pre-freeze records in the private experiments repository.

## The decision sentences (PREREG §4.5)

**The frozen form of the row that applied, row 3, *S (tie, Order B)*** (PREREG §4.5, verbatim; the "…" is the
counts clause of row 1, "in setting (a), hit@5 was S% under S against I% under ft512o64s (n = 2,035); S − ft512o64s
= D [lo, hi], discordance π;", followed by the G1 clause and the other-order clause):

```text
"Deploy S: … S is non-inferior at 2.0 points but not shown superior, and ft512o64s is not shown superior to S (ft512o64s − S = E [loE, hiE]); the tie goes to the cheaper arm under cost order B. Under …"
```

**The decision sentence, as the script printed it** (`sentences[0]`, `settings.a.sequence.sentence`, verbatim):

> Deploy S: in setting (a), hit@5 was 82.85% under S against 82.51% under ft512o64s (n = 2,035); S − ft512o64s = +0.34 [-0.64, +1.33], discordance 0.053; S is non-inferior at 2.0 points but not shown superior, and ft512o64s is not shown superior to S (ft512o64s − S = -0.34 [-1.38, +0.64]); the tie goes to the cheaper arm under cost order B. Gate G1: ft512o64s − ft512o64 = +3.49 [+2.11, +4.86]. Under cost order A the decision would be ft512o64s.

**The frozen H3 form** (PREREG §4.5, verbatim):

```text
"Segmenter: S-regex − S = D3 [lo3, hi3]; S may be deployed on any worker (on this corpus; chunk-id overlap X %) / only where Punkt is asserted (reason: margin / stratum / not evaluable: <each gate with its count>; chunk-id overlap X %)."
```

**The H3 sentence, as the script printed it** (`sentences[1]`, `settings.a.H3.sentence`, verbatim):

> Segmenter: S-regex − S = -0.15 [-0.49, +0.20]; S may be deployed on any worker (on this corpus; chunk-id overlap 99.2 %).

Both printed sentences equal their frozen forms character for character outside the filled values. Neither has
been edited here. The other-order clause is computed (`other_order`: order A, row 2, *ft512o64s (tie, Order A)*,
decision `ft512o64s`), not fixed text.

**What one point is here.** The denominator is the decision set, **2,035 questions** (`settings.a.n_decision`), one
question per article. One hit@5 point is 1 % of 2,035, about **20 questions**. hit@5 held on 1,686 questions under
S (82.85 %, `settings.a.hit5.S`) and on 1,679 under ft512o64s (82.51 %)‡. The arms differ on 107 questions
(discordance 0.05258, `settings.a.discordance_S_vs_ft512o64s`): S reaches the quote on **57** where ft512o64s does
not, ft512o64s on **50** where S does not‡, a net 7 questions, D = +0.344 points (`settings.a.D.point`). The
interval [−0.639, +1.327] (`settings.a.D.lo`, `.hi`) spans about −13 to +27 questions.

## Setting (a): the decision cell (hybrid + reranker; `settings.a`)

**The sequence (`settings.a.sequence`).** G: no gate (`gates` {}, `result.gates` []). H1: `true` (pooled lower bound
−0.639 ≥ −2.0, `result.h1_threshold` −2.0; `ctx_raised` false, ratio 0.8704; stratum guard `stratum_ok` true, no
stratum failed). H2: `false` (lower bound ≤ 0). Outcome `tie`, row 3, decision `S` under the frozen order B; under
order A row 2, `ft512o64s`.

| contrast | pair | point | 95 % interval | *x* only / *y* only‡ | discordance | field |
|---|---|---|---|---|---|---|
| **D** (H1, H2) | S − ft512o64s | **+0.344** | [−0.639, +1.327] | 57 / 50 | 0.05258 | `settings.a.D`, `.discordance_S_vs_ft512o64s` |
| **E** (Order B tie step) | ft512o64s − S | −0.344 | [−1.376, +0.639] | 50 / 57 | 0.05258 | `settings.a.E` |
| **D1** (gate G1) | ft512o64s − ft512o64 | **+3.489** | [+2.113, +4.865] | 140 / 69 | 0.10270 | `settings.a.D1`, `.G1` true |
| **D3** (H3) | S-regex − S | −0.147 | [−0.491, +0.197] | 5 / 8 | 0.006388 | `settings.a.D3`, `.H3` |

n = 2,035 in every row; paired bootstrap over questions, 10,000 resamples, seed 0 (`settings.a.bootstrap`); 95 %
two-sided. G1 recomputed Addendum C's +3.49 [+2.11, +4.86] to the printed precision.

**hit@5 per arm, decision set** (`settings.a.hit5`, `settings.a.descriptive.hit5`; questions‡):

| arm | hit@5 | questions‡ | role |
|---|---|---|---|
| S | 82.85 % | 1,686 | challenger (decision S) |
| ft512o64s | 82.51 % | 1,679 | incumbent |
| ft512o64 | 79.02 % | 1,608 | baseline, gate G1 |
| S-regex | 82.70 % | 1,683 | H3 only |
| sent512s | 82.06 % | 1,670 | secondary (C1) |

**Stratum guard on D** (`settings.a.stratum_guard`): k = 11 (8 journal families, 3 length bands), Bonferroni level
0.995455; every stratum has n ≥ 132, all 11 tested, **none failed**. Per-stratum points range from −1.44 (*other*,
n 139) to +2.28 (*molbio-immunology*, n 263); no stratum has point < −2.0, so the guard's second condition was
never reached. **Stratum guard on D3** (`settings.a.stratum_guard_H3`): none failed; points between −1.14 and +0.70.

**ctx@5** (chars/4, estimated; `settings.a.ctx5`, `settings.a.descriptive.ctx5`, `tokens_estimated` true): S 2,015.7,
ft512o64s 1,999.5, ft512o64 2,315.8, S-regex 2,014.3, sent512s 1,906.3; S / ft512o64 = 0.8704
(`ctx_ratio_S_over_ft512o64`), under the 1.5 that would raise H1's threshold.

**Secondary contrasts, Holm over {C1, C2} in (a)** (`settings.a.contrasts`; C3 is H3 and C4 is G1 here, `in_family`
false):

| contrast | pair | point | 95 % interval | p (two-sided) | Holm p | significant |
|---|---|---|---|---|---|---|
| C1 | S − sent512s | +0.786 | [+0.098, +1.474] | 0.0272 | 0.0272 | yes |
| C2 | S − ft512o64 | +3.833 | [+2.457, +5.258] | 0.0002 | 0.0004 | yes |
| C3 | S-regex − S | −0.147 | [−0.491, +0.197] | 0.496 | — | (H3) |
| C4 | ft512o64s − ft512o64 | +3.489 | [+2.113, +4.865] | 0.0002 | — | (G1) |

**Descriptive beside H3** (`settings.a.descriptive.S_regex_minus_ft512o64s`): S-regex − ft512o64s = +0.197
[−0.835, +1.179]. Discordance at k = 5 (`settings.a.descriptive.discordance5`): S|ft512o64s 0.0526, S-regex|S
0.0064, S|sent512s 0.0246, ft512o64s|ft512o64 0.1027, S|ft512o64 0.1081.

**Other depths and strict containment** (descriptive; `settings.a.descriptive.hit1`, `.hit10`, `.strict5`):

| arm | hit@1 | hit@5 | hit@10 | strict single-chunk @5 |
|---|---|---|---|---|
| S | 60.49 | 82.85 | 85.70 | 82.65 |
| ft512o64s | 60.88 | 82.51 | 85.45 | 82.46 |
| ft512o64 | 56.56 | 79.02 | 82.65 | 78.77 |
| S-regex | 60.20 | 82.70 | 85.65 | 82.51 |
| sent512s | 59.26 | 82.06 | 84.82 | 81.87 |

**Sensitivity set, n = 2,335 (noise subset included; not the decision; `settings.a.sensitivity`):** D = +0.514
[−0.428, +1.413]; D1 = +3.041 [+1.756, +4.326].

**Cross-check against the harness's `decision.json`** (`settings.a.cross_check`): `d1_match` true
(`arms.ft512o64s` +3.4889 [+2.113, +4.8649]); both ladder steps present were recomputed and match to 4 decimals:
S − ft512o64 +3.8329 [+2.457, +5.258] (switched), ft512o64s − S −0.344 [−1.3759, +0.6388] (not switched);
harness winner `S`; harness class test passed for S and ft512o64s. The harness's choice is reported, not compared.

## The secondary settings (`settings.d`, `settings.b`, `settings.c`; not the decision)

In every setting the script's sequence decides S (`sequence.decision`), so `secondary_settings_disagreeing_with_a`
is empty. No gate fired in any setting; `missing_by_arm` is 0 for every arm, three-arm and five-arm; n = 2,035 in
every cell; bootstrap 10,000 / seed 0.

| setting | S | ft512o64s | ft512o64 | S-regex | sent512s | D = S − ft512o64s | D1 = ft512o64s − ft512o64 | D3 = S-regex − S | H1 / H2 → row |
|---|---|---|---|---|---|---|---|---|---|
| (a) hybrid + reranker, **decision** | 82.85 | 82.51 | 79.02 | 82.70 | 82.06 | +0.34 [−0.64, +1.33] | +3.49 [+2.11, +4.86] | −0.15 [−0.49, +0.20] | T / F → 3, tie, S (A: ft512o64s) |
| (d) hybrid, no reranker | 67.08 | 65.41 | 61.28 | 67.08 | 66.49 | +1.67 [+0.44, +2.95] | +4.13 [+2.36, +5.95] | 0.00 [−0.29, +0.29] | T / T → 1, S superior (A: S) |
| (b) vector only | 59.16 | 57.10 | 49.14 | 59.12 | 58.97 | +2.06 [+0.59, +3.49] | +7.96 [+5.95, +9.98] | −0.05 [−0.49, +0.39] | T / T → 1, S superior (A: S) |
| (c) BM25 only | 56.31 | 55.38 | 55.68 | 56.27 | 55.04 | +0.93 [−0.10, +1.97] | −0.29 [−1.72, +1.13] | −0.05 [−0.15, 0.00] | T / F → 3, tie, S (A: ft512o64s) |

hit@5 in %, `settings.<s>.descriptive.hit5`; intervals `settings.<s>.D`, `.D1`, `.D3`; `G1` is `null` outside (a)
(the gate is defined in (a) only). Discordance S|ft512o64s (`discordance_S_vs_ft512o64s`): (d) 0.0865 (105 / 71‡),
(b) 0.1130 (136 / 94‡), (c) 0.0555 (66 / 47‡). S-regex|S: (d) 0.0049 (5 / 5‡), (b) 0.0093 (9 / 10‡), (c) 0.0005
(0 / 1‡).

**Secondary contrasts, Holm over {C1, C2, C3, C4} per setting** (`settings.<s>.contrasts`; point [interval], Holm p):

| setting | C1 S − sent512s | C2 S − ft512o64 | C3 S-regex − S | C4 ft512o64s − ft512o64 |
|---|---|---|---|---|
| (d) | +0.59 [−0.25, +1.43], 0.357 | +5.80 [+4.03, +7.57], 0.0008 * | 0.00 [−0.29, +0.29], 1.0 | +4.13 [+2.36, +5.95], 0.0008 * |
| (b) | +0.20 [−0.79, +1.18], 1.0 | +10.02 [+8.06, +11.99], 0.0008 * | −0.05 [−0.49, +0.39], 1.0 | +7.96 [+5.95, +9.98], 0.0008 * |
| (c) | +1.28 [+0.54, +2.06], 0.0056 * | +0.64 [−0.84, +2.11], 1.0 | −0.05 [−0.15, 0.00], 1.0 | −0.29 [−1.72, +1.13], 1.0 |

\* `holm_sig` true.

**Sensitivity set per setting** (`settings.<s>.sensitivity`, n = 2,335): (d) D +1.50 [+0.30, +2.70], D1 +4.28
[+2.61, +5.95]; (b) D +2.14 [+0.77, +3.47], D1 +8.39 [+6.51, +10.24]; (c) D +0.94 [0.00, +1.88], D1 −0.13
[−1.46, +1.20].

**ctx@5 per setting** (chars/4, estimated; `settings.<s>.descriptive.ctx5`; S / ft512o64 in
`ctx_ratio_S_over_ft512o64`): (d) S 1,801.3, ft512o64s 1,745.2, ft512o64 2,103.7, S-regex 1,799.8, sent512s 1,685.7
(0.856); (b) 1,669.8 / 1,548.6 / 1,920.0 / 1,670.2 / 1,528.2 (0.870); (c) 1,922.4 / 1,915.5 / 2,269.2 / 1,922.1 /
1,823.8 (0.847).

**Stratum guards** (`settings.<s>.stratum_guard`, `.stratum_guard_H3`): none failed in any setting.

**Cross-checks** (`settings.<s>.cross_check`): `d1_match` true and every ladder step present matches in all three.
Harness winner S in (d) and (b); in (c) the harness's own ladder kept the baseline ft512o64 (`harness_winner`
`ft512o64`; its steps S − ft512o64 +0.64 [−0.84, +2.11] and ft512o64s − ft512o64 −0.29 [−1.72, +1.13], neither
switched). That is the harness's choice on its own ladder (challenger against the baseline), reported and not
compared; the frozen §4.1 sequence on S − ft512o64s decides S in (c) as in (a).

## Gates and checks (PREREG §6)

**None fired.** `settings.<s>.gates` is `{}` and `settings.<s>.arm_gates` `{}` in all four settings;
`settings.<s>.arms_missing` is empty. Each check, as the report read it:

| check (gate code) | result | field |
|---|---|---|
| G1 (drift): lower(ft512o64s − ft512o64) > 0 in (a) | +2.113 > 0, `G1` true | `settings.a.D1`, `.G1` |
| receipts: 17 per arm (16 main + sentinel), status complete, 0 failed documents, 241,930 main documents, 40 sentinel documents (`receipts`, `docs`, `docs_failed`, `receipt_status`, `receipt_error`) | pass, both arms | `files.arms.<arm>.census` |
| sentinel chunk count = 168, the dry run's (`sentinel_chunks`) | 168 on both arms | `files.arms.<arm>.census.sentinel_n_chunks` |
| `chunking` object complete on every receipt: `v` 1, `sentence`, 512, 0.25, 128, ceiling 640, `sections/1` (`chunking`) | pass, 34 receipts | (no failure entry; `ingest-summary: sentinel.<arm>.chunking` shows the object) |
| segmenter: every S receipt `punkt-abbrev-v1`, every S-regex receipt `regex-abbrev-v1` (`segmenter`) | pass | `files.arms.<arm>.census.segmenter_versions` |
| image: one `image_commit` across both arms, equal to the pinned tools commit `5d65bead…`; `image_build` 1 on S, 2 on S-regex; `image_version` v1.6.24 (`image_commit`, `image_build`, `image_version`) | pass | `files.arms.<arm>.census.image_commit`, `.image_build` |
| chunk ids carried on every receipt; `n_chunks` = distinct ids per receipt (`chunk_ids`) | pass; `chunk_ids_carried` true | `files.arms.<arm>.census` |
| store counts: registry-derived expected count = Σ main `n_chunks` + sentinel = distinct ids = Qdrant = Elasticsearch (`counts`) | S **767,984** = 767,816 + 168; S-regex **767,956** = 767,788 + 168; equal in all three places on both arms | `files.arms.<arm>.{census,qdrant,es}` |
| worker stderr, staged file per shard (`stderr_missing`, `stderr_tail`, `stderr_lines`): S 0 warning lines in all 17; S-regex ≥ 1 in all 17 | S 0 / 0 / … (17 × 0; 700 B per main shard, 1,413 B sentinel); S-regex 17 × 1 (1,051 B; 1,850 B) | `files.arms.<arm>.stderr` |
| sentinel stderr: staged file vs logs-endpoint tail agree (`stderr_agree`) | S 0 = 0; S-regex 1 = 1; tails far under 256 KB | `files.arms.<arm>.sentinel_logs_endpoint` |
| reused arms: live census = frozen census, Qdrant = Elasticsearch (`census`) | ft512o64 705,204; ft512o64s 859,280; sent512s 850,312, all equal | `files.reused_census` |
| shard files: sha256 of all 17 equal the §9.1 pins (`shards`) | 17 hashed, pass | `files.shards_hashed` |
| re-embed: 200 cosines, ids sha256 = the pinned sample, every cosine ≥ 0.999 (`reembed`) | n 200, min **0.9999995**, 0 below | `files.reembed` |
| model ids at the run = at the freeze (`model_ids`) | pass (`Salesforce/SFR-Embedding-Mistral`, `BAAI/bge-reranker-v2-m3`) | (no failure entry) |
| provenance: clean tree, no untracked files, a commit (`provenance`) | pass | `files.harness_provenance` |
| config pins: decision and reporting config sha256 per setting = `frozen-inputs.json` (`config_pin`) | pass, 8 configs | (no failure entry) |
| request fields per setting = the frozen request (`request`); no row ignored for a request mismatch | pass; harness `request_mismatch_by_arm` 0 | `settings.<s>` (no entry) |
| retrieval rows: 2,437 questions × arms, no missing or duplicate ok row (`rows`); harness refusals none (`score_refusal`) | `missing_by_arm` 0 everywhere; `n_five_arm` 2,035 | `settings.<s>.missing_by_arm`, `.n_five_arm` |
| one server version across all rows and all four settings (`version`) | one label | `server_version`, `settings.<s>.server_versions` |
| decision set in the three-arm score: n = 2,035 and n = 2,335 exactly, sorted-id sha256 = the frozen pins (`sets`) | pass | `settings.<s>.n_decision` 2035, `.n_sensitivity` 2335 |
| `decision.json` integrity: nothing `forced_unchecked`, `paired_ladder`, `bonferroni`, margins 2.0 matching the noise file, decided on `clean_excl_noise` n 2,035, ladder order starts at the baseline and follows cost order B (`decision_json`) | pass | `settings.<s>.cross_check` |
| cross-check: D1 = `arms.ft512o64s` and every ladder step present recomputed to 4 decimals | match, all four settings | `settings.<s>.cross_check.d1_match`, `.ladder_steps[].match` |
| hit quality (harness): unlocated share 0, offsets unverified 0, no question without record text | pass† (private `score.json`: `unlocated_share` 0.0 for every arm, `questions_without_record_text` 0) | — |
| the optional top-5 drift figure | **absent** (D-3) | `files.top5_drift` null |

**Chunk-id overlap between the segmenters** (descriptive, carries the H3 finding; `files.chunk_id_overlap`): of
767,984 S chunk ids and 767,956 S-regex ids, **761,915** are shared: 99.21 % of S (`share_of_S` 0.992097), 99.21 %
of S-regex (0.992134). On the 40 sentinel documents the overlap was 168 of 168 on both sides
(`ingest-summary: sentinel.<arm>.chunk_id_overlap_with_other_arm`), and the sentinel ids equal those of the
pre-freeze dry run and study-text pass (checked by the write-up against the private dry-run receipts).

**Stored chunks per arm** (descriptive, the cost-order quantity of §4.2; `files.arms.<arm>.census.distinct_chunk_ids`,
`files.reused_census`): S 767,984; S-regex 767,956; sent512s 850,312; ft512o64s 859,280; ft512o64 705,204 (all with
the 40 sentinel documents). S stores 10.6 % fewer chunks than ft512o64s and 9.7 % fewer than sent512s.

## Ingest record (PREREG §6, *Segmenter actually used*; §8, *Mixed worker fleet*)

From `ingest-summary` unless marked otherwise.

- **Submissions.** Per arm: a sentinel submission (1 glossary + 1 ingest + 1 merge task), then a main submission of
  33 tasks (16 glossary, 16 `ingest_shard`, 1 merge); every task `SUCCESS`. GoWe wall time: S 3,569.8 s, S-regex
  4,469.6 s (the two ran concurrently on the dev worker group; the S-regex ingest tasks queued behind S's).
- **Attempts per ingest task** (GoWe's task listing, the RETRIES column): 30 of the 32 main-shard ingest tasks ran
  once; **S shard 14 shows 1 retry and S-regex shard 10 shows 2**. Both ended `SUCCESS`; the receipt and the staged
  stderr file are the final attempt's (PREREG §6), and the distinct-id = Qdrant = Elasticsearch gate, which would
  catch chunks an earlier attempt left behind, held on both arms. The cause of the retries is not in the records
  read.
- **Worker version per task.** The task listing the operator captured is the endpoint's first page: 20 of 33 records
  per arm (`listing_capture`: all 16 glossary tasks and ingest shards 00–03; `has_more` true, the rest was not
  fetched). For those, the worker's version from the fleet listing: S shards 00–03 ran on 0.26.1 / 0.28.1 / 0.26.1 /
  0.28.1; S-regex shards 00–03 on 0.26.1 / 0.26.1 / 0.28.1 / 0.28.1 (`ingest_tasks_with_worker_version`). Both worker
  versions ran ingest tasks of both arms; the version of the other 12 ingest tasks per arm is **absent**.
- **GoWe versions.** Server `0.28.1+e805888` at the freeze (the freeze record; the sentinel passes before the
  freeze ran on 0.26.1). The dev worker group at 22:31:31Z: 2 workers at `0.28.1+e805888`, 8 at `0.26.1+be35a2f`,
  all online (`gowe.dev_worker_group_by_version`); the tee of worker stderr into the staged file held on this fleet
  (the sentinel file = endpoint check above).
- **Sentinel at the run** (`sentinel.<arm>`): 40 documents, 0 failed, 168 chunks on each arm; build 1 / 2; segmenter
  `punkt-abbrev-v1` / `regex-abbrev-v1`; the full `chunking` object (`window_budget` 4080, reported, not gated);
  stderr file 1,413 B / 0 lines and 1,850 B / 1 line, the logs endpoint the same; ingest exit code 0; registry
  entries with `spec_hash` `29236085`, identical `chunk_params`, embedding `Salesforce/SFR-Embedding-Mistral`
  4096-d `openai` resolved from the server default (no `embedding` key in the create body); 168 of 168 chunk ids
  shared across the arms.

## Power, as planned against what was observed (PREREG §5; `power.json`)

- **H1 / H2.** Planned at π = 0.05 (the sent512s-vs-ft512o64s proxy, 0.047): non-inferiority power 0.98 if S were
  truly equal, 0.52 if truly 1 point worse; the smallest superiority detectable with power 0.80 is 1.39 points
  (`minimal_detectable_superiority`). Observed: discordance 0.0526, D = +0.34 points. The interval's half-width is
  0.98, as planned (0.97 at π 0.05). A true advantage of 1.4 points or more would have been seen with power ≥ 0.80;
  the observed +0.34 is below that, and the test does not resolve it. The non-inferiority conclusion is a powered
  one: at this discordance a true loss of 2.0 points would have failed H1 with probability 0.975.
- **G1.** Planned power 0.99 at +3.0 (π 0.10); observed +3.49 at π 0.103.
- **H3.** Planned at π = 0.02 (power 1.00 at a true 0). Observed discordance **0.0064**, so the two segmenters can
  differ by at most 0.64 points on this set (`note_h3`): H3 could not fail its 2.0-point margin, and the sentence's
  *any worker* rests on the chunk-id overlap (99.2 %) and on the 13 questions‡ whose hit status differs (5 for
  S-regex, 8 for S), not on the margin. This is the collapse PREREG §2 and §5 anticipated.

## Procedural notes

1. **The report ran once** and wrote once (00:33:47Z†). Its inputs: the private manifest (filled 00:33:41Z†; a
   partial manifest without the live reused-arm census was written a minute earlier), `frozen-inputs.json`,
   `PREREG.md` with `--prereg-sha256`. The one warning the script can print (an `--out` inside the checkout) did
   not apply; `--out` was in the run directory.
2. **Exposure log** (PREREG §6, *Operator exposure*). Before the report wrote its output, no S or S-regex
   per-question file was read. The harness's own `score` step prints the pooled hit@5 per arm on the decision set to
   its log at the end of each setting's run (00:29–00:31Z†; the four `score-*.log` files hold those lines), and
   `decide` prints its choice; those lines existed four minutes before the report. The operator's account of the
   interval, from the session transcript (not machine-verifiable): the only reads of `score-*.log` and
   `decide-*.log` before the report were (a) the exit codes and (b) one grep for `refus|error|warn|Traceback`
   piped through `grep -v -i -E 'hit@|hit5'`, which returned only the four `decide` warnings about
   `noise.json selected_ids_sha256` (note 3); no pooled hit@5 line was displayed. Of the retrieval ledgers, the
   operator read only row counts, `status` and `server_version` before the report: no hits, no scores. Nothing was
   changed between `score` and `report`: the rule, the configs (sha256 gated), the margins and the order were
   frozen, and the script's output is the decision.
3. **`decide`'s warning**, printed in all four settings: *cannot check that score excluded the noise run's subset
   (missing noise.json selected_ids_sha256)*. The frozen `noise.json` (sha256 pinned per setting) carries no
   `selected_ids_sha256`, so `decide`'s own preflight could not confirm the exclusion. The exclusion is established
   elsewhere: `score.json` records `noise_exclusion` enabled, 300 excluded, `n_primary` 2,035†, and the report's
   `[sets]` gate checks the three-arm decision set independently, n = 2,035 and the sorted-id sha256 equal to the
   frozen pin, plus n = 2,335 for the sensitivity set; it did not fire (D-5).
4. **Harness invocation.** `retrieve`, `score`, `decide` and `report` ran from one checkout of the frozen tag with
   `PYTHONPATH=python`, as the write-up skill prescribes (D-5).
5. **The first retrieval attempt made 0 calls** (D-2): the new `out_dir`s held only the frozen `noise.json`; the
   harness found no question file and exited 0 with "0 calls this run" in all four settings. The frozen inputs were
   then staged as symlinks (verified identical to the Addendum C `out_dir`s' files) and retrieval restarted.
6. **The §9.2 freeze commit in `README.md`** was not made before the run (D-6); the freeze is recorded in the
   signed tag and the private `PREREG.sha256`, and this commit adds the record to `README.md`.
7. **Collections listing before the creates.** PREREG §7 step 2 has the operator record the tenant-wide collection
   count before creating the two arms. That count is not among the run records read for this write-up (absent).
