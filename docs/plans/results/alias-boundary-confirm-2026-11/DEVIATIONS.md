# Deviations from the frozen pre-registration (PREREG v3.2, sha256 `b3a2d9e6…`, tag `prereg-alias-boundary-confirm-2026-11`)

Append-only. Each entry follows PREREG §9.3: what changed, why, when, what had been seen at that time, and
which result it touches.

## D-1 (2026-11-06): the owner's go replaced the gateway coordinator's acknowledgement (§5 step 4)

- **What changed.** PREREG §5 step 4 (and §12) says the gateway size notice goes to the gateway coordinator
  and that no gateway call is made before its acknowledgement in chat. The first gateway batch (both arms:
  Argon 26 + 23 calls, Opus 18 + 18) ran on the owner's go instead.
- **Why.** The coordinator session was unreachable: it was not among the reachable sessions, and the notice
  bounced undelivered. The operator held and asked the owner, who answered in chat: "Continue but test if
  the gateway is reachable first." The gateway's model list answered, with both models served; that check
  made no model call.
- **When.** Asked after the Qwen labelling finished (05:26Z). The gateway batch ran 14:29:30–14:32:59Z.
- **What had been seen.** Both arms' Qwen labelling was complete. The gateway size notices had been read; by
  their nature they give the per-arm escalation counts (26 control, 23 retest), and §5 step 4 requires the
  notice to name them. No label, `final.jsonl`, `judge-summary.json` or per-arm `LOG` count had been read.
- **Which result it touches.** None of the computation. The judge settings, the arm order (Argon
  control → retest, Opus retest → control), the caps and the hard-stop rules were unchanged. All 85 calls
  returned `ok`, with no provider notice, access-denied reply or served-model mismatch, and the Opus cap
  (24 per registry) was not reached. Only the notice safeguard was replaced: had the coordinator been able
  to say "pause", it could not have. The step-6 report (`artifacts/confirm-report.json`) and its sentence in
  `RESULTS.md` do not depend on it.
