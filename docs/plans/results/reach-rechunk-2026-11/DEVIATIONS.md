# Deviations from the frozen pre-registration (PREREG, sha256 `1e3791ed…4458`, tag `prereg-reach-rechunk-2026-11`)

Append-only. Each entry follows PREREG §9.3: what changed, why, when, what had been seen at that time, and
which result it touches.

## D-1 (2026-11-10): the gateway share of J's judging ran on the second gateway, not the gateway the coordinator audits (§6 step 4, §12)

None of the figures in this entry is in a committed artifact. They come from the second gateway's audit, as
read by that gateway's operator session batch by batch and reported to the operator; the operator's private run
log gives the batch times.

- **What changed.** PREREG §12 sends one gateway notice to the gateway coordinator before the first gateway
  call, and §6 step 4 makes no gateway call before the coordinator's acknowledgement. Every gateway call of
  this study (the Argon tiebreak and the Opus drift reference, for the S registry and the drift registry) ran
  on the **second gateway** instead of the gateway the coordinator audits, and the owner's go in chat stood in
  for the coordinator's acknowledgement. The judge configuration files were unchanged but for `out_dir` and
  `chunk_exports`, as §6 step 4 allows; the judge models, prompts, caps, budget stop and hard-stop rules were
  unchanged. The Qwen judges (C and E) run locally and did not use a gateway.
- **Why.** The gateway coordinator was unreachable: it was not among the reachable sessions. The second
  gateway's operator session confirmed which gateway it was: up since 03:02Z, audited, with no access control.
  The owner chose in chat to run on the second gateway and record a deviation. The declined alternative was to
  wait for the coordinator and its gateway.
- **When.** 2026-11-10. The owner's go for judging ("Go: Qwen now, gateway after notice") came first; the choice of
  the second gateway followed when the coordinator could not be reached. The gateway calls ran
  20:18:40–23:36:10Z (the audit's first and last logged call times; the operator's batch windows, which start at
  the runner's start line and end at its end line, are 20:18:29–23:36:11Z), one batch at a time: the harness finalizes each batch (Qwen, then the Argon tiebreak and
  the Opus drift reference) before it starts the next.
- **What had been seen.** The per-registry totals of `j-seeds.json` (S seeds 7,196, S orphans 1,549; drift
  sample 616, seeds 555, orphans 61), read to size the notice, as §6 step 3 declares. During judging, the audit's
  call counts per batch and model below. No S quote@10 or found@10, no label file, `final.jsonl`,
  `judge-summary.json` or per-registry `LOG` count, and no `j-seeds-detail.json`. The report did not exist.
- **The calls** (chat endpoint; token counts are the provider's, as the audit logs them; `reasoning` is a
  subset of `out`, so visible = out − reasoning; cache tokens 0):

  | batch | window (UTC) | audit ids | model | calls | tokens in | out | of which reasoning | visible |
  |---|---|---|---|---|---|---|---|---|
  | S1 | 20:18:29–20:21:53 | 774–867 | Gemini 4 Argon | 94 | 81,199 | 4,831 | 887 | 3,944 |
  | | | 868–967 | Claude Opus 5.5 | 100 | 120,693 | 9,453 | 2,918 | 6,535 |
  | S2 | 21:31:27–21:36:01 | 968–1093 | Gemini 4 Argon | 126 | 106,834 | 5,960 | 715 | 5,245 |
  | | | 1094–1193 | Claude Opus 5.5 | 100 | 123,008 | 10,757 | 4,066 | 6,691 |
  | S3 | 22:45:42–22:50:39 | 1194–1298 | Gemini 4 Argon | 105 | 89,350 | 7,531 | 3,129 | 4,402 |
  | | | 1299–1399 | Claude Opus 5.5 | 101 | 124,119 | 11,967 | 5,368 | 6,599 |
  | S4 | 23:32:00–23:34:30 | 1400–1473 | Gemini 4 Argon | 74 | 64,883 | 4,175 | 1,087 | 3,088 |
  | | | 1474–1533 | Claude Opus 5.5 | 60 | 75,679 | 5,284 | 1,291 | 3,993 |
  | drift | 23:34:51–23:36:11 | 1534–1566 | Gemini 4 Argon | 33 | 30,618 | 1,876 | 485 | 1,391 |
  | | | 1567–1594 | Claude Opus 5.5 | 28 | 37,142 | 3,074 | 1,270 | 1,804 |
  | **total** | 20:18:40–23:36:10 (calls logged) | 774–1594 | Argon / Opus | **432 / 389** | 372,884 / 480,641 | 24,373 / 40,535 | 6,303 / 14,913 | 18,070 / 25,622 |

  - **821 calls**, all HTTP 200, with 0 errors, 0 provider notices and 0 refusals. The model served equals the
    model requested on every call ("Gemini 4 Argon", "Claude Opus 5.5"), with no fallback. Tokens in: 853,525
    in all; out 64,908, of which reasoning 21,216.
  - **Planned 819** (431 Argon, 388 Opus), plus **2 harness re-issues in S3**: one Opus drift-reference item
    returned an empty completion (0 completion tokens, finish `stop`) on both attempts (audit ids 1311 and
    1334), so that pair has no Opus reference and the S3 drift reference holds 99 pairs; one Argon reply held no
    parseable verdict (id 1235) and its re-issue returned a verdict (id 1240). Opus sets no label, so the empty
    item changed no label. Its cause is not known: an empty completion and a filtered stop cannot be told apart,
    and the audit holds no body.
  - **Contiguity.** The ids 774–1594 are contiguous, and each batch follows the previous one with no other call
    between; the call before S1 (id 773) was at 18:58:52Z. Nothing else is interleaved.
  - **Preflight estimate.** Tokens in were 0.4 % above the preflight estimate for Argon (371,385) and 0.7 % below
    it for Opus (484,267); no batch was off by more than 1.9 %.
  - **Latency outliers** (the audit's list): Argon ids 1262 (107 s), 1432 (30 s), 1216 (29 s), 1437 (22 s),
    1546 (20 s); Opus id 1330 (66 s).
  - **Cost.** Not computed.
- **What could be established, and what could not.**
  - The 821 calls are **attributed to this study by window, model and contiguity; no access control.** They fall
    inside the batch windows, carry the study's two models, and form one contiguous id range with nothing
    interleaved. The second gateway has no access control, so the attribution cannot rest on a credential.
  - The gateway coordinator was unreachable and has no access to the second gateway's audit, so it could not
    reconcile these calls against an audit of its own, as it had for earlier studies' batches on its gateway. The call record
    is therefore the second gateway's alone.
- **Which result it touches.**
  - **Q: none.** quote@10 needs no judge, and the report computed Q from the retrieval runs and the exports
    alone.
  - **J (descriptive): its S labels, through the Argon tiebreak.** Every new S label that needed a tiebreak was
    decided on the second gateway, while W's labels (2026-11-04/06) were judged on the first gateway. That adds a
    gateway difference to the S-only drift §7.2 already declares for J ("any judge drift lands on S only").
  - **The drift check bears on it.** The drift registry re-judged a seeded sample of W pairs with the same frozen
    judge, its tiebreaks on the second gateway. Agreement with the original labels was 0.9712 overall and 0.9327
    on W's *answers* labels, at or above the 0.90 threshold, so J carries no *judge drift* caveat
    (`artifacts/reach-report.json` `drift_check.agreement`, `drift_check.agreement_on_answers`,
    `drift_check.caveat`). The check measures every difference between the two judgings at once (date, gateway,
    any other); it does not isolate the gateway.
  - **The Opus drift reference** is report-only and sets no label; the report holds no Opus figure.

## D-2 (2026-11-10): procedural, the first `report` attempt stopped before computing (§6 step 5)

- **What happened.** §6 step 5 runs `reach_report.py report` once. Attempt 1, shortly before 23:38:55Z, ran under
  a Python environment without `transformers`.
  - **What ran:** the input checks. It verified the PREREG sha (`--prereg`, `--prereg-sha256`), the `j-seeds.json`
    input shas and the frozen-input pins (`frozen-inputs.json`), and passed all three.
  - **What did not run:** `build_report`. The run stopped while importing the tokenizer (`hf_token_counter`),
    before `build_report` computed any figure.
  - **Nothing was written or seen:** no `--out` file and no printed count, rate, label or statistic.
- **Then.** Attempt 2 ran under the project environment (with `transformers`), from the same clean checkout of
  the frozen tag with the same inputs, and completed (`artifacts/reach-report.json` `provenance.recorded_at`
  2026-11-10T23:38:55Z). It is the only run that computed the report.
- **Why recorded here.** No element listed in §9.3 changed: the inputs, code, rules and step order are the frozen
  ones, and the report was computed once. This entry is procedural, kept so the record shows both invocations.
- **Which result it touches.** None.
