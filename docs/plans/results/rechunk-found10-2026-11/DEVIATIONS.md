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
