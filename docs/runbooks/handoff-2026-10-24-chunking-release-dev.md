# Handoff — deploy the chunking release to dev (steps 8–9 of cut-a-release.md)

**From:** the #318/#647 session (Fable) · **To:** the management session (svcbvbrc / ragstack-ctl) · **Date:** 2026-10-24

## What is being released

| Artifact | Name | Commit | Where |
|---|---|---|---|
| Tools tag | `v1.6.16` | `eef856f0041260b1c116f92279a4a138840f6983` | GitHub |
| Tools image | `ragstack-tools-v1.6.16-b1.sif` (+ `.receipt.json`) | from v1.6.16 | `/scout/containers/ragstack/` (store) + symlink pair in `/scout/containers/ragstack-dev/` |
| Server tag | `v1.6.17` | `a86f00b45d449763b3a2ec91ae8e65d95850d4d7` | GitHub; CWL stamped with the tools image above (PR #818) |
| Server image | `ragstack-server-v1.6.17-b1.sif` (+ receipt) | from v1.6.17 | `/home/wilke/ragstack-tools-builds/` — **not yet prepared** (step 8 is yours) |

`v1.6.15` is a tools tag with **no image** (its image had no `nltk`, #815 — found by the runbook's post-build import check). Do not use it.

Content: #803 JATS spans · #802 `chunk_size` honoured / `max_tokens` a cap at sentence boundaries / semantic never cuts mid-sentence / `size_unit` · #805 one chunker factory, in-process path capped, 503 instead of a guessed window · #804 Punkt abbreviations + `segmenter_version` in receipts, goldens v2 · #816 nltk in both images, `segmenter_version()` honest about the backend · #813 keyed conformance third boot. Details: STATUS.md "Unreleased on main (2026-10-23)", issues #318 #647 #815.

**Behaviour changes an operator will notice on dev**
- Every worker receipt now carries `provenance.segmenter_version = "punkt-abbrev-v1"` and `tool_image = ragstack-tools-v1.6.16-b1.sif`.
- The API's in-process ingest (not used on dev: `INGEST_BACKEND=gowe`) answers **503** when neither `CHUNK_MAX_TOKENS` nor the embedding endpoint's `/v1/models` yields a window. Dev's vLLM at :9001/:9002 serves `/v1/models`, so this does not fire.
- Sentence-based collections chunk differently (Punkt + abbreviations, and GoWe workers previously ran the regex fallback). Existing `sem-e2e`, `sem-e2e-legacy` are unaffected until re-ingested (#811).

## Step 8 — prepare (ctl account)

```bash
ragstack-ctl selftest --supervisor instance                       # green before the first prepare of the day
ragstack-ctl --direct fleet image prepare --sif <build dir>/ragstack-server-v1.6.17-b1.sif
ragstack-ctl fleet image list                                     # v1.6.17 listed, commit == the tag's
ragstack-ctl doctor dev --op update-code                          # not red
```

## Step 9 — upgrade dev (ctl account)

Dev's UI is the Vite dev server, so this is an API-only move (`--no-rebuild-ui`):

```bash
ragstack-ctl tenant update-code dev --image ragstack-server-v1.6.17-b1.sif --no-rebuild-ui --dry-run
ragstack-ctl tenant update-code dev --image ragstack-server-v1.6.17-b1.sif --no-rebuild-ui --yes-destructive dev --wait
```

Verify (tenant-upgrade.md Part 1 § Verify):
```bash
ragstack-ctl --json tenant show dev | jq '.registry | {server_image, code, last_ops: .last_ops["update-code"]}'
curl -s -H "X-API-Key: $DEV_ADMIN_KEY" http://127.0.0.1:24040/v1/version      # git_sha == v1.6.17's commit
grep -E "tool image check" /rag/data/tenants/dev/logs/api-dev.log | tail -3    # "sha256 ok, labels ok" for ragstack-tools-v1.6.16-b1.sif
ragstack-ctl gowe render dev                                                   # worktree at the image's commit; CWL names v1.6.16-b1
```

Rollback: `update-code` unwinds itself on failure; otherwise `ragstack-ctl tenant update-code dev --image ragstack-server-v1.6.14-b1.sif --no-rebuild-ui --yes-destructive dev --wait`.

**Only dev.** No other tenant moves until the dev soak and the #811 re-ingest rehearsal are done.

## After step 9 — tell the #318 session; it runs the test plan

Scripts are ready in this session's scratchpad (`devtest/`):
- `legs_bcd_dev_gowe.sh <v1.6.17 sha> ragstack-tools-v1.6.16-b1.sif` — version, deep health, boot log, four `relchk-*` collections (fixed_token / sentence tokens / sentence chars / semantic) uploaded through GoWe, receipts, chunk token/char maxima and sentence-edge boundaries; deletes its collections.
- `leg_e_inprocess.sh ~/Development/worktrees/rel-v1.6.17` — the in-process path dev does not exercise: scratch in-memory API, `fixed` (tokenizer load), `sentence`+`tokens`, `semantic` with a >3,000-sentence document, and the 503 refusal against a dead endpoint.
- #811 rehearsal on `sem-e2e` / `sem-e2e-legacy` — planned separately after legs B–E pass.

## Already verified on the release code (before any deploy)

- **Server image** `ragstack-server-v1.6.17-b1.sif`: receipt/labels/RELEASE/sha256 agree (`1e6f1302ecb4…`), role `server`, nltk 3.10.4, `segmenter_version()=='punkt-abbrev-v1'` inside, stamped CWL inside, boot smoke `/v1/version` → `git_sha a86f00b4…`.
- **Tools image** `ragstack-tools-v1.6.16-b1.sif` (sha `1fd4584d4368…`): same battery, plus `sentence_spans` inside the image byte-identical to the v2 goldens on all 50 docs + canonical (nltk 3.10.4 vs 3.9.4 makes no difference), `ingest_shard.py` has no `--chunk-size-unit` (it reads the registry entry).
- **Leg F** keyed conformance on the v1.6.17 checkout: all three boots pass (251/19, 2, 2).
- **Leg E** in-process path (scratch in-memory API from the v1.6.17 checkout, dev's vLLM :9001 for embeddings): `sentence size=128 size_unit=tokens` → 220 chunks, max **128** tokens; `sentence 1500 chars` → max 1498 chars / 748 tokens; `semantic` → max 2324 tokens ≤ 4080, 35/36 chunks end at sentence punctuation; >3,000-sentence document → the `fixed_token` 512 fallback (#809, unchanged); `size_unit` on a `fixed` collection → 400; dead endpoint + no `CHUNK_MAX_TOKENS` → **503** "cannot size chunks … did not report the model window"; with `CHUNK_MAX_TOKENS=512` sizing proceeds.

What dev adds: the GoWe path with the new tools image (legs B–D) and the #811 rehearsal on `sem-e2e` / `sem-e2e-legacy`.
