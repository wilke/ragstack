# Handoff — chunking releases v1.6.19 and v1.6.21 on dev; fleet roll and soak pending

**From:** the release session · **To:** the next release / management session (svcbvbrc / ragstack-ctl) · **Date:** 2026-10-29

Follows [handoff-2026-10-24-chunking-release-dev.md](handoff-2026-10-24-chunking-release-dev.md) (v1.6.17 on dev).
Procedure: [cut-a-release.md](cut-a-release.md), [server-image.md](server-image.md), [tenant-upgrade.md](tenant-upgrade.md).

## What shipped

| Artifact | Name | Commit | Where |
|---|---|---|---|
| Tools tag | `v1.6.18` | `b1a727976fe2f83db9791ff4020068eca216b760` (PR #841, version bump) | GitHub |
| Tools image | `ragstack-tools-v1.6.18-b1.sif` | from v1.6.18 | sha256 `9633ff7581f02474b4b304fea2e317ff896c1888ce8ef6abd2559b0f2d74f15a` |
| Server tag | `v1.6.19` | `9394a183c84d75f4cb06815d86f51fd6f2531975` (restamp PR #842) | GitHub; CWL stamped with tools v1.6.18-b1 |
| Server image | `ragstack-server-v1.6.19-b1.sif` | from v1.6.19 | sha256 `c9fd52d9c1195b265c4e4198771b14b7d456f35db4eb1fbe94553ba8c68bafa5` |
| Tools tag | `v1.6.20` | `4a0571b1383739393c6623385b8d7477910cd252` (merge of #853) | GitHub |
| Tools image | `ragstack-tools-v1.6.20-b1.sif` | from v1.6.20 | sha256 `1dbb8539ad166dcc4a1161c737db03575dbd12a09d8850c103e9b71b40ec95e6` |
| Server tag | `v1.6.21` | `0e22c55586e7fb3269a330263e77fe93f31130e4` (G2b, #854) | GitHub; CWL stamped with tools v1.6.20-b1 |
| Server image | `ragstack-server-v1.6.21-b1.sif` | from v1.6.21 | sha256 `19571ef4c06e5820b5c23899aff3c22a847cc08f650cf3d6003d5df58efe62ef` |
| UI artifact | v1.6.21 | from v1.6.21 | prepared for the fleet |

There is no server image for v1.6.18 or v1.6.20 (tools tags), and no tools image for v1.6.19 or v1.6.21 (server tags).

**v1.6.18 tools, post-build checks:** no new CLI flag, the default cap, a chunking v2 receipt, `.md` extraction byte-identical, goldens v2 0 diffs, regression 9/9.

**v1.6.19 content (chunking):**
- #829 semantic token cap: new `semantic` collections record `size_cap: tokens`, `cap_split: greedy`, `breakpoint_cap: size`; `size` defaults to 512.
- #830 Markdown is extracted correctly on the GoWe path. Dev verified 1,113 chunks against 1,023 on v1.6.17.
- #836 `size_tolerance` / `min_piece` under `chunk.params`, receipt chunking v2. Refused on non-semantic methods; `overlap >= size` is a 400.
- #839 and #840 landed on `main` after the tag and are **not** in v1.6.19.

**v1.6.20 tools content:** the glossary step flags, `--glossary-file`, `--max-chunks` (default 0), `--workflow-id`, and the #852 receipt guard.

**v1.6.21 content (G2b, #854):** renames the receipts and stamps the CWL with `ragstack-tools-v1.6.20-b1.sif`.

## What is live where

| Tenant | Server tag | Notes |
|---|---|---|
| `dev` | **v1.6.21** | v1.6.19 from ≈04:25Z 2026-10-28 (`update-code dev` job 01M60C2V7E2WVJ6510FH7Z3W9A); v1.6.21 on the owner's "Upgrade dev" (2026-10-28/29) |
| `asm-next`, `demo`, `elrond`, `clark`, `lucid-next`, `hackathon` | v1.6.14 | unchanged; wait for the owner's **"Roll fleet"** |

**Dev-only settings** (no other tenant has them):
- `MAX_COLLECTIONS_PER_OWNER=100`; admins are exempt; there is no per-user override.
- `MAX_COLLECTIONS=150` tenant-wide.
- `MAX_CHUNKS_PER_COLLECTION=0` (cap off). The per-collection override is the registry's `collections.max_chunks`.

## How to verify dev (read-only)

Same checks as the 2026-10-24 handoff and tenant-upgrade.md Part 1 § Verify, with the new names:

```bash
ragstack-ctl --json tenant show dev | jq '.registry | {server_image, code, last_ops: .last_ops["update-code"]}'
                                                                               # server_image ragstack-server-v1.6.21-b1.sif
curl -s -H "X-API-Key: $DEV_ADMIN_KEY" http://127.0.0.1:24040/v1/version      # git_sha == 0e22c55586e7…
grep -E "tool image check" /rag/data/tenants/dev/logs/api-dev.log | tail -3    # "sha256 ok" for ragstack-tools-v1.6.20-b1.sif
ragstack-ctl gowe render dev                                                   # CWL names v1.6.20-b1
ragstack-ctl fleet image list                                                  # v1.6.19 and v1.6.21 listed, commits == the tags'
ragstack-ctl fleet artifact list                                               # the v1.6.21 artifact (id = <tag>-<sha[:12]>)
```

Chunk counts: tenant-level GoWe jobs report `chunk_ids=[]`, so `GET /v1/ingest/{job_id}` does not tell you how many chunks landed. Count the points in the collection's Qdrant collection instead.

Uploads on dev need a person's BV-BRC token: API-key principals cannot upload while `INGEST_BACKEND=gowe`.

## Fleet roll (on the owner's "Roll fleet" only)

Not started. Per tenant, as in earlier rolls (tenant-upgrade.md):

```bash
ragstack-ctl tenant update-code <t> --image ragstack-server-v1.6.21-b1.sif --artifact <v1.6.21 artifact id> --dry-run
ragstack-ctl tenant update-code <t> --image ragstack-server-v1.6.21-b1.sif --artifact <v1.6.21 artifact id> --yes-destructive <t> --wait
ragstack-ctl gowe render <t>                                                   # CWL names v1.6.20-b1
```

Rollback per tenant: `update-code` unwinds itself on failure; otherwise update-code back to `ragstack-server-v1.6.14-b1.sif` with the v1.6.14 artifact (`v1.6.14-baf7174a805d`).

## Soak legs still to run

The post-roll soak legs on v1.6.21 have **not** been run on dev. Run them before the fleet roll, and record the results here under a "Result" section, as the 2026-10-24 handoff did.

## Open items

- **Fleet roll** to v1.6.21 waits for the owner's "Roll fleet". Six tenants are still on v1.6.14.
- **#849 GoWe worker cap:** it is open whether tools v1.6.20 fixes it for bulk loads.
- **GoWe job reporting:** tenant-level GoWe jobs report `chunk_ids=[]`; verify counts in Qdrant.
- **Dev uploads:** API-key principals cannot upload on dev; GoWe needs a person's BV-BRC token.
