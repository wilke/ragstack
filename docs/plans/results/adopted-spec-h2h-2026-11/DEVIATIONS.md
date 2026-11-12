# Deviations from the frozen pre-registration (PREREG, sha256 `31687ee4…6f82`, tag `prereg-adopted-spec-h2h-2026-11`)

Append-only. Each entry follows PREREG §9.3: what changed, why, when, what had been seen at that time, and
which result it touches. None of the elements §6 lists as deviations (the margin, the sequence, the cost order and
tie-break, the arms and their specs, the images, the settings and request fields, the decision set, the bootstrap
iterations and seed, the stratum guard parameters, the gate thresholds, the §4.5 wording) changed. The entries
below are tooling, staging and bookkeeping; each says what it touches.

## D-1 (2026-11-11): the re-embed `fetch` step's id lookup was fixed after the freeze (PR #1006)

- **What changed.** `h2h_report.py reembed fetch`, the one live step of the §2 embedding-drift gate, looked chunks
  up by their bare chunk id. The text index stores a chunk under `_id` `<tenant>:<chunk_id>` and the vector store
  under the store's own derived point id, so the first live attempt stopped with *chunk … not in the text index*
  and produced no cosine. The fix (`d9051640`, merged as `8d0dd703`, PR #1006) adds `--tenant` (default `public`),
  maps both lookups using the store's own id function, and keys the output rows by the original chunk id. The
  sample (the same 200 ids, pinned by `frozen-inputs.json` `reembed.ids_sha256`), the threshold (0.999), the
  `verify` subcommand and the report's `[reembed]` gate are unchanged; the fix is in the fetching tool only. Three
  tests on fake store and embedding responses were added with it. `PREREG.md` and `frozen-inputs.json` were not
  touched.
- **Why.** The pre-freeze dry run and the §9.4 tests exercised `select` and `verify` and the gate itself, not a live
  `fetch` against the stores; the id scoping was found on the first live call.
- **When.** Fix commit 22:37:58Z, cosines fetched 22:38:32Z† (from the fix branch, while the main ingest ran),
  merge 22:51:40Z. Freeze: 22:30:22Z.
- **What had been seen.** The failed attempt's error and nothing else: no S or S-regex retrieval existed (ingest
  was running), and the cosines are a property of the reused ft512o64s arm.
- **Which result it touches.** None. The report ran at the frozen commit `f4d4ef9a` and read only the cosines
  file: n = 200, ids sha256 equal to the pin, minimum cosine 0.9999995 ≥ 0.999, 0 below
  (`artifacts/h2h-report.json` `files.reembed`). Had the fix not been made, the gate would have read *absent*
  and the decision GATE-NOT-EVALUABLE; the fix made the frozen check runnable, it did not change what it checks.

## D-2 (2026-11-11/12): the frozen inputs were staged into the new `out_dir`s as symlinks after a first retrieval attempt made 0 calls

- **What changed.** PREREG §7 step 3 retrieves with the five-arm reporting config into its own `out_dir`, and the
  decision config has its own. The harness reads its question set, passages, sample, audit and extracted text from
  files inside the `out_dir`; the two new `out_dir`s per setting held only the frozen `noise.json` (PREREG §4.1). The
  first `retrieve` attempt (23:52:08Z†) found no questions and exited 0 in all four settings with *0 calls this
  run*; no retrieval row was written. The operator then staged the 17 input entries per `out_dir` (16 files and the
  `extracted` directory) as symlinks to the OA eval's files (23:53:02Z†) and restarted retrieval (23:53:12Z†).
- **Why.** The configs derived by `h2h_report.py configs` copy every path key of the Addendum C configs but set a new
  `out_dir` (`config-diff.json`), and the harness takes those inputs from the `out_dir` rather than from path keys;
  the pre-freeze procedure did not say how the inputs reach the new directories.
- **What had been seen.** The harness's *0 calls* lines only.
- **Verification.** For this write-up, every staged file in all eight `out_dir`s was compared by sha256 with the same
  file in Addendum C's `out_dir` of the same setting: all 16 files identical in every directory, and the
  `extracted` link resolves to the same directory. Addendum C's `out_dir` entries are themselves symlinks to the one
  shared OA-eval `out/` directory, so the comparison is identity by construction. The staged `passages.summary.json` has the sha256 pinned in
  `frozen-inputs.json` (`passages_summary_sha256`; `segmenter` `punkt-abbrev-v1`). The report's `[sets]` gate
  independently checked the three-arm decision and sensitivity sets by count and sorted-id sha256 against the
  frozen pins, and `[config_pin]` the eight configs; neither fired.
- **Which result it touches.** None: the inputs are the frozen ones, the attempt that made no calls wrote nothing,
  and every retrieval row comes from the restarted run (one server version, 0 missing rows).

## D-3 (2026-11-12): the optional top-5 drift figure is absent

- **What changed.** PREREG §2 names, beside the one-`git_sha` gate, a descriptive figure per reused arm: the share of
  questions whose top-5 set equals the old (Addendum C) run's. The §9.4 script computes it from an optional `drift`
  block in the manifest (old and new per-question rows per arm). Addendum C's retrieval ledgers do not store the
  per-question hit lists in the form the frozen `top5_drift` function reads, and they contain control rows; on the
  first attempt the function failed with an exception and produced no figure. The block was dropped from the
  manifest and the report records `files.top5_drift: null`.
- **Why.** The function was written and tested against synthetic rows, not against the Addendum C ledgers'
  layout, and the frozen script could not be changed to read them (§9.3).
- **What had been seen.** Nothing of S or S-regex: the figure reads the reused arms only, and the attempt failed
  before output.
- **Which result it touches.** None. The figure was descriptive, with no gate and no test. The drift controls the
  decision relies on both held: every arm was re-retrieved on one server version (gate `[version]`, one label
  across all rows), and the re-embed gate held (minimum cosine 0.9999995). G1 recomputed Addendum C's
  ft512o64s − ft512o64 at +3.49 [+2.11, +4.86], the same to the printed precision, which is the drift check that
  matters for the comparison. *Absent* is recorded as absent, not as passed.

## D-4 (2026-11-11): GoWe was upgraded after the sentinel passes; the dev worker fleet was mixed at the run

- **What changed.** Nothing in the pre-registration: PREREG §2 and §8 declare that the GoWe server was upgraded to
  0.28.1 after both pre-freeze sentinel passes (which ran on 0.26.1), that the dev worker group runs two worker
  versions, and that no gate is needed because the worker only launches the per-arm tools image, chunk identity is
  gated on the receipts and on distinct ids = Qdrant = Elasticsearch, and the stderr tee is proved per run by the
  sentinel file-vs-endpoint check. This entry records what the run saw, as §8 asks.
- **What the run saw.** Server `0.28.1+e805888` (freeze record). Dev worker group at 22:31:31Z: 2 workers at
  `0.28.1+e805888`, 8 at `0.26.1+be35a2f`. The captured ingest tasks (shards 00–03 of each arm; the task listing's
  first page, D-5) ran on both versions: S on 0.26.1 / 0.28.1 / 0.26.1 / 0.28.1, S-regex on 0.26.1 / 0.26.1 /
  0.28.1 / 0.28.1. The sentinel tee held: staged file and logs-endpoint tail agree on both arms (0 = 0, 1 = 1 warning
  lines). Every receipt of each arm carries one `image_commit`, the arm's `image_build` and the arm's
  `segmenter_version`; distinct ids = Qdrant = Elasticsearch on both arms.
- **Which result it touches.** None (no gate fired; the per-task worker version is recorded, not gated).

## D-5 (2026-11-12): procedural

Three items that changed no input, rule or output, kept so the record shows them.

- **`PYTHONPATH=python`.** The harness (`oa_chunk_eval`) and `h2h_report.py` import `ragstack`; the operator ran
  them with `PYTHONPATH=python` from the frozen checkout, as the write-up skill prescribes. Operator setup, no code
  change, no effect.
- **`decide`'s warning.** In all four settings `decide` printed *cannot check that score excluded the noise run's
  subset (missing noise.json selected_ids_sha256)*. The frozen `noise.json` per setting (sha256 pinned in
  `frozen-inputs.json`) carries no `selected_ids_sha256` key, so `decide`'s own preflight could not confirm that
  `score` excluded the 300-question noise subset. The exclusion is established independently: `score.json` records
  the exclusion enabled, 300 excluded, `n_primary` 2,035†, and the frozen report's `[sets]` gate checks the
  three-arm decision set by count (2,035) and by sorted-id sha256 against the frozen pin, and the sensitivity set
  (2,335) likewise (`artifacts/h2h-report.json` `settings.<s>.n_decision`, `.n_sensitivity`; `settings.<s>.gates`
  empty). `decide`'s choice is reported, not compared (PREREG §4.1).
- **The GoWe task listing was captured one page deep.** The listing endpoint paginates (20 records per page, 33 per
  submission); the operator saved the first page per arm, which holds the 16 glossary tasks and ingest shards
  00–03. The per-task worker version PREREG §8 asks for is therefore known for 4 of 16 ingest tasks per arm and
  absent for the rest; the attempt count per task was taken from GoWe's status listing, which covers all 33 tasks
  per submission (`RESULTS.md` § *Ingest record*).

## D-6 (2026-11-11): the §9.2 freeze commit in `README.md` was not made before the run

- **What changed.** PREREG §9.2 has one commit record the freeze in this directory's `README.md` after the signed
  tag. The tag was signed (22:31:08Z) and the private `PREREG.sha256` written (22:30:22Z), and the run started
  (22:31:31Z) without that commit: `README.md` at the freeze commit `f4d4ef9a` (sha256 `bf417cb3…`, listed in the
  tag message) still reads *DRAFT, not frozen*, and its *What remains for the freeze* table was left as drafted.
- **Why.** The owner's go came as *freeze …* then *run* in one exchange, and the operator went to the run after the
  tag.
- **What had been seen.** Nothing: no S or S-regex data existed.
- **Which result it touches.** None. The freeze itself is complete and verifiable: the signed tag at `f4d4ef9a`
  carries the sha256 of every §9.1 public file and both images, the private `PREREG.sha256` carries the owner's go
  verbatim with the UTC time, and the report refused to run on any other PREREG text (`--prereg-sha256`). The
  commit that adds this file also writes the freeze record into `README.md` (status line, the tag's figures, and
  the two bookkeeping rows: the harness changes since `d2a8b9b` — one commit, `e6323f4c`, the PR #972 review fixes
  — and `power.json`, whose committed sha256 equals the tag's and which `test_power_reproduces_prereg_section_5`
  regenerates byte-identically).
