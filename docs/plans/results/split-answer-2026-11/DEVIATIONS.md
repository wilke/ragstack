# Deviations from the split-answer plan (PLAN.md)

Append-only. The plan is exploratory and not pre-registered; these entries record where the run departed from
what PLAN.md says, why, when, and what had been seen at that time.

## D-1 (2026-11-11): step 1's gateway share ran on the second gateway, on the owner's go, without the coordinator's acknowledgement (PLAN §6)

None of the figures in this entry is in a committed artifact. They come from the second gateway's audit, as read
by that gateway's operator session and reported to the operator, and from the operator's private run log.

- **What changed.** PLAN §6 requires a size notice to the gateway coordinator and its acknowledgement before any
  Argon or Opus call, and names the owner's explicit go, recorded as a deviation, as the only substitute. Step 1's
  gateway calls (the Argon tiebreak and the Opus drift reference) ran on the **second gateway**, and the owner's go
  in chat stood in for the acknowledgement. The judge configuration was the main study's but for `out_dir` and the
  control chunk export; models, prompts, caps and hard-stop rules were unchanged. The Qwen judges ran locally and
  used no gateway.
- **Why.** The gateway coordinator was unreachable, as for the reach comparison (its D-1). The second gateway's
  operator session confirmed the gateway: the same process as during the reach comparison, flags unchanged, audited,
  with no access control. The owner chose "Go: second gateway, record a deviation".
- **When.** Qwen ran 02:24:41–02:44:43Z. The preflight size notices were written at 02:44:51Z and 02:45:00Z, after
  Qwen and before any gateway call. The gateway calls ran 02:46:01–02:47:11Z (runner start and end lines), one batch.
- **What had been seen.** The preflight notices (call counts and token estimates per model). No label file,
  `final.jsonl`, `judge-summary.json` or report; the report was run after the batch was finalized.
- **The calls** (token counts are the provider's, as the audit logs them; reasoning is a subset of out):

  | model | audit ids | calls | preflight estimate in | tokens in | out | of which reasoning |
  |---|---|---|---|---|---|---|
  | Gemini 4 Argon | 1897–1924 | 28 | 41,646 | 40,870 (−1.9 %) | 2,787 | 1,612 |
  | Claude Opus 5.5 | 1925–1946 | 22 | 52,737 | 53,110 (+0.7 %) | 3,552 | 2,071 |
  | **total** | 1897–1946, contiguous | **50** | 94,383 | **93,980** | 6,339 | 3,683 |

  All 50 answered; no error, provider notice, refusal, duplicate, empty answer or interleaved call from another
  caller inside the window; served model as requested, no fallback. Attribution is by window, model and contiguity,
  because the gateway has no access control. The gateway total, 50, is just below PLAN §6's ≈ 53–62: Argon 28
  against ≈ 31–40 (28 of 446 items escalated, 6.3 %, against the 7–9 % assumed), and Opus 22 as computed.
- **What it touches.** Who audited the calls, not what the judge saw: the same models and prompts would have run on
  the coordinator's gateway.
