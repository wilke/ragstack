# Deviations: rechunk found@10, stage 1 (exploratory)

Departures from the stage-1 plan ([PLAN.md](PLAN.md)) and from the private live-ops plan (v2.1) that built
the four collections. They are dated 2026-11-06/07 and listed in run order. "Owner" means the owner's word in
chat; "management" means the dev-tenant coordinator session.

## D-1: one collection end to end before the next (live-ops plan §5)
- **Plan.** Create all four collections, embed all four, then load them one at a time, with a stop after base.
- **Run.** Base went create → embed → load and stopped. The owner and management agreed this narrower order;
  the other three then ran the same way, one at a time, on a second owner go.
- **Effect.** None on the result. Each create still passed the eviction gate, with its remaining-creates count.

## D-2: preflight method for the dev port owners (P-3)
The listening ports belong to a service account, so `ss -p` cannot show their processes. Ownership was shown
instead by each listener's Apptainer instance (`api-dev`, `elasticsearch-dev`), and for Qdrant by inference
from the API's own configuration. The alias study's executed plan used the same method.

## D-3: GoWe worker count (P-9)
- **Plan.** Expected 2 workers in the dev group.
- **Found.** 10.
- **Resolution.** Management confirmed 10 is expected and acceptable, and recorded it as the baseline.
  Conditions: watch both embedding endpoints, and stop on any error, timeout or retry. None occurred.

## D-4: offline render of the embed command line (B1 gate check 3)
- **Problem.** The plan's `cwltool --no-container` render cannot run: `embed-bulk.cwl` declares its container
  under `requirements`.
- **Run.** The command line was built through the cwltool library instead, from the real workflow and the
  real base inputs, routed through the step's `in:` mapping.
- **Result.** It shows `--collection-id bench-rechunk-base --chunk-size-unit tokens`; without the input, no
  `--chunk-size-unit` appears. The binding was also proven live: every embedding header records
  `size_unit: tokens`.

## D-5: exports for the three expanded corpora derived from their embeddings (owner decision)
- **What happened.** The offline exports were made with committed code in the host environment. The
  embeddings were made inside the tools image `v1.6.24-b1`. The two differ in library versions:

  | library | host | image |
  |---|---|---|
  | tokenizers | 0.22.2 | 0.23.3 |
  | transformers | 5.12.1 | 5.22.1 |
  | nltk | 3.9.4 | 3.10.4 |
  | regex | 2026.6.28 | 2026.9.29 |

  Base chunked identically on all 12,560 documents. The expanded corpora did not:
  - neighbour: 3 of 30,408 added documents (48 image chunks against 49 host chunks);
  - random-1: 8 of 32,671 (108 against 109);
  - random-2: 8 of 33,409 (105 against 106).

  In each affected document, one chunk ends later in the image from a point mid-document onwards. The export
  parity gate (G-E5) stopped neighbour before anything was loaded.
- **Decision.** Owner: "Image is truth, continue". Management gave the go and asked for the library versions
  to be recorded.
- **What changed.**
  - Each expanded corpus's export was rebuilt from its own embedding files (the production chunking), in the
    export's row shape.
  - Each harness `expected_chunks` became the image count: 141,592, 136,402 and 136,187, which is 1 fewer per
    expanded collection.
  - Base kept its host export.
- **Checks.** None of the 19 differing documents holds a confirmatory pair, and no chunk of the label table
  lies in one. The build script checked this per corpus and would have stopped otherwise. So the label reuse,
  and every stage-1 measure, is unaffected.
- **Consequence for the plan's sizes.** PLAN.md §2's expected collection sizes (141,593 / 136,403 / 136,188)
  are the host counts. The collections as built hold 1 chunk fewer each, as `artifacts/build-summary.json`
  records.

## D-6: two operational adjustments in the load procedure
- **Load submit guard.** The guard that the heap monitor is alive and predates its pid file needed a 2-second
  tolerance, because `etimes` and the file mtime are whole seconds, and it accepts the monitor script's own
  process name. Its first check refused on a 1-second rounding race, and nothing was submitted.
- **Stage-out register.** The extraction that registers every output file also listed the tasks' input files.
  Those are classified as inputs and kept out of the teardown register.

## D-7: three mistakes in the operator's wrapper script, none with a data effect
All three stopped the neighbour build before any store write, were fixed, and were re-run:
1. a relative `--out` path for the in-image load rehearsal, which the container could not resolve;
2. a checksum glob that included the previous manifest;
3. reading document counts from the wrong field of the rehearsal summary.

## D-8: teardown deferred
The plan deletes the four collections after the analysis. The owner asked that nothing be deleted, so the
collections and the stage-out register stay until the owner gives the teardown go.

## D-9 (2026-11-10): the stage-1b gateway calls ran on the second gateway, not the Mac gateway
Stage 1b is described in [ADDENDUM-2026-11-10-stage1b.md](ADDENDUM-2026-11-10-stage1b.md). None of the
figures below is in a committed artifact. They come from the second gateway's audit, as read by that gateway's operator session at
03:15Z, and from the gateway coordinator's (OpenChia's) review, both reported to the operator.

- **What changed.** Every earlier stage's gateway calls ran on the Mac gateway, and its audit covered them.
  The 114 stage-1b gateway calls ran on the second gateway instead. That gateway has its own Argo key
  and its own audit.
- **Why.** The Mac gateway was unreachable because the Mac's VPN was down. The owner chose the second -mgrast
  gateway in chat on 2026-11-10. The declined alternative was to wait for a tunnel to the Mac gateway.
- **The gateway.**
  - Started 03:02Z, with `--messages-native`, `--fallback ""` (no model fallback), and no access control.
  - It runs the Mac gateway's code plus a one-line header patch in `post_native`. That patch affects only
    `/v1/messages`, and the study's calls all went to the chat endpoint.
- **The calls** (2026-11-10, chat endpoint): all non-streaming and `ok`/200, with 0 empty, 0 error and 0
  provider notices.

  | model | calls | window (UTC) | audit ids | tokens in | tokens out | of which reasoning | visible (out − reasoning) |
  |---|---|---|---|---|---|---|---|
  | Gemini 4 Argon (tiebreak) | 80 | 03:11:12–03:13:12 | within 8–99 | 71,005 | 4,439 | 937 | 3,502 |
  | Claude Opus 5.5 (drift check) | 34 | 03:13:23–03:13:44 | 103–136, contiguous | 43,847 | 4,076 | 1,842 | 2,234 |

  - Token counts are the provider's, as the audit logs them. `reasoning_tokens` is a subset of `tokens_out`,
    as on the Mac gateway (same code, and consistent with the data). Cache tokens were 0 / 0.
  - The audit holds ids 1–141, contiguous; it has no `events.jsonl`.
- **Other traffic in the window.**
  - Exactly 12 streamed Opus calls to `/v1/messages` sit inside the Argon id range (ids 12, 21, 25, 33, 36,
    39, 48, 52, 60, 62, 95, 96), and 3 more at ids 100–102. They are attributed to the operator's own
    argo-agent review run, which started at 03:10:10Z.
  - Ids 1–2 are 2 Haiku calls, the second gateway operator's own tests. Ids 3–7 and 137–141 are the remaining
    10 of the operator's 25 review-run calls, before and after the window.
  - Every chat-endpoint call in the window is one of the study's 114.
- **What the coordinator established, and what it could not.**
  - OpenChia *reviewed* the reported totals and found them consistent:
    - labels 323 + 348 = 671;
    - tiebreak labels 28 + 52 = 80, so the escalation rate is 11.92 %;
    - 591 non-escalated labels = 295 + 296;
    - drift sample 34 = round(33.55);
    - timing within the range observed on the Mac gateway.
  - It has no access to the second-gateway audit, so it could not reconcile the calls against an audit of its own, as
    it had for the earlier batches. The stage-1b gateway traffic is therefore **reviewed, not verified**.
- **Identity evidence.**
  - Per-call input tokens match the Mac runs: Argon 887.6 against 892.4 in the exploratory null (0.5 %), and
    Opus 1,289.6 against 1,287.4 in the confirmatory run (0.2 %). Read as analysis, these per-call rates are
    consistent with the same prompts reaching the same models.
  - Timing lies within the Mac-observed range.
  - Argon's reasoning tokens were 11.7 per call, against 29.9 (null) and 26.3 (confirmatory). The
    coordinator noted this and did not chase it.
- **Preflight estimate.** The preflight token estimate was off by −16.9 % (Argon) and +21.1 % (Opus).
- **Cost.** About $0.55 for stage 1b. The study's gateway total comes to about $9.8 across both gateways
  (the coordinator's figures).
- **Consequence.** The study's gateway record now spans two gateways and two credentials. The Mac audit
  covers every gateway call before stage 1b, and the second-gateway audit covers the 114 stage-1b calls alone.
- **Effect on the result.** The calls decided 80 tiebreak labels and the 34-call drift check. They touch only
  stage 1b's A, which saturates. They do not touch A_own, which is unchanged
  ([addendum](ADDENDUM-2026-11-10-stage1b.md)).
