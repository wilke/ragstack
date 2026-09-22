# `year` str → int on `lucid_sfr_tok256` — canary report

**Status:** canary COMPLETE and CLEAN (20,000 / 1,363,425 points). **The full run did not
start** — writes to `:6343` were blocked by the harness permission classifier partway through
the disk-measurement slice (see §8). Nothing is half-written; the collection is green,
consistent, and resumable from a checkpoint.

Date 2026-09-16. Target Qdrant `http://localhost:6343`, collection `lucid_sfr_tok256`
(1,554,790 points, 8 segments). ES `http://localhost:24003`, index `lucid_sfr_tok256`.
Owning tenant `lucid` (config `/rag/data/tenants/lucid/config/tenant.env`), API live on `:24000`.

---

## 1. Headline — before / after the int filter

The defect, reproduced exactly as briefed, and the canary's effect on it:

| filter on Qdrant `:6343` | before | after canary |
|---|---:|---:|
| `{"year": 2021}` (int — the only form the API accepts) | **0** | **1,890** |
| `{"year": "2021"}` (str) | 129,248 | 127,358 |
| `{"year": 2023}` (int) | **0** | **4,810** |
| `{"year": "2023"}` (str) | 329,150 | 324,340 |
| ES `metadata.year` term `2021`, numeric **or** string | 129,248 | 129,248 (untouched) |

Across the whole collection after the canary: **20,000 int, 1,345,811 str, 188,979 absent,
total 1,554,790** — no point gained or lost. For *every one* of the 81 year values,
`int_count + str_count == the original string count`. That identity is the proof that the
conversion moved points between the two forms and did not drop any.

Before the canary the int filter returned **zero for every year in the collection**. That is
the live defect: a correct year filter matched nothing on the vector leg while BM25 matched
129,248, and hybrid fusion hid the discrepancy.

---

## 2. Field paths — proven against real sampled documents in each store

Done first, because the brief notes a wrong field path has produced false readings three times
in this work.

- **Qdrant payload is FLAT.** A scrolled point carries, at the top level:
  `chunk_id, doc_id, start_char, end_char, tenant_id, chunk_index, title, authors, year, doi,
  doi_url, citation`. The key is `year`, *not* `metadata.year`.
- **ES nests.** Mapping top level is `chunk_id, content, doc_id, end_char, metadata,
  start_char`; the field is `metadata.year`, mapped **`keyword`**.
- ES `_id` = `"lucid:" + chunk_id`. Verified by direct `_doc` fetch.

Both stores independently report the same totals (1,554,790 docs; 188,979 without a year;
identical per-year counts), so the two are in agreement about the data — they disagree only
about the *type*.

---

## 3. The unparseable population — reported before anything was converted

83 distinct `year` string forms exist. Contrary to the §4.4 examples (`'2019-2020'`,
`'2023 Mar'`, `'n.d.'`, `'In press'`), **this corpus has no partial-date or free-text forms at
all**. Every value is either four digits or one of two junk forms:

| form | points | verdict |
|---|---:|---|
| `""` (empty string) | **2,382** | **unparseable — left as the empty string, untouched** |
| `"????"` | **4** | **unparseable — left as `"????"`, untouched** |
| `"1400"` … `"2106"` (81 four-digit forms) | 1,363,425 | parseable → int |
| *(key absent entirely)* | 188,979 | nothing to convert; not touched, not invented |

**Unparseable total: 2,386 points (0.15% of the collection, 0.17% of those carrying a year).**

Policy applied exactly as instructed: the field was **not dropped** (a dropped field leaves
every year filter silently, with no error) and **no sentinel was written** (`0`/`9999` actively
match range filters and return wrong documents). These 2,386 stay visibly broken the way they
already were. Verified after the canary: `""` still 2,382 and `"????"` still 4 — the exact
original counts.

### Two parseable-but-implausible values, converted deliberately

`"1400"` (26 points) and `"2106"` (80 points) parse cleanly but fall outside
metadata-consistency.md §1.2's `1800 ≤ year ≤ current+1`. §1.2 says drop-and-count; this task
says never drop. **I converted them** (`1400` → `1400`, `2106` → `2106`) because that is
faithful to the source and keeps them visible and correctable. Flagging rather than deciding
silently: if the schema rule should win, they are 106 points and trivially re-addressed.

### `date` derivation

**Every year string in this corpus is year-only** — no month, no day, anywhere. So per
date-filtering.md, `date = year * 10000` with `year = date // 10000`, and `date` carries no
information beyond `year` here. Written anyway (`"2021"` → `year: 2021, date: 20210000`) so a
future `date` range filter works on this collection without a second backfill pass. Verified:
`date` present on exactly the 20,000 converted points, **0 rows where `date != year * 10000`**,
0 rows with a `date` but no int `year`. No point carried a `date` key beforehand.

---

## 4. Out of scope, as instructed — reported and left alone

- **`authors`**: present on **1,503,418** points as a `", "`-joined string
  (e.g. `"Charlotte Xue Dong, Cassandra Malecki, …"`). **Not touched.** Splitting on `", "`
  would turn `"Cotter, Joshua A."` into `["Cotter", "Joshua A."]` — worse than leaving it.
- **`keywords`**: **does not exist on this collection at all** — 0 documents, and the field is
  absent from the ES mapping entirely. Nothing to leave alone.
- Untouched, as instructed: `ragstack_sfr_semantic` (118 GB staged build) and the empty
  `ragstack_lib_open_access_…` shell on `:6343`; `:6333`, `:9200`, `:24081`, `:24041`.

---

## 5. Does ES need anything? **No.**

Measured directly, not inferred:

| query against `metadata.year` (mapped `keyword`) | count |
|---|---:|
| `term: 2021` (numeric literal) | 129,248 |
| `term: "2021"` (string literal) | 129,248 |
| `range: gte 2020` (numeric) | 726,628 |
| `range: gte "2020"` (string) | 726,628 |

ES coerces the query value to the field's type, so **both forms already match** — the
divergence is Qdrant-only, and **no ES write was performed at all**. This also means no new
divergence was introduced between `:24003` and the frozen copy of this index on `:9200` that
lucid production `:8010` still reads (per the comment in `tenant.env`).

**Finding, not attempted:** `metadata.year` is `keyword`, and an ES mapped type cannot be
changed in place. Making it `long` would require a **full reindex** of 1,554,790 docs / 3.5 GB,
and would additionally have to decide what happens to the 2,386 unparseable values (a `long`
field rejects `""` and `"????"` unless `ignore_malformed` is set, which silently drops them
from the index — the exact invisible-breakage failure mode §4.4 warns about). Not done. Not
recommended without that decision made first.

**One real ES defect surfaced in passing:** `range` on a `keyword` field is *lexicographic*.
`gte 2020` returns 726,628, which includes the 80 `"2106"` and the 4 `"????"` rows — `'?'`
(0x3F) sorts above `'9'`. Converting Qdrant does not fix this; only a reindex to `long` would.

---

## 6. Canary results — 20,000 points, every check passed

Stratified across **all 81** parseable year forms (≥1 point from each, remainder proportional),
so the canary exercised every value in the corpus, including `1400`, `2106` and `2026`.

| check | result |
|---|---|
| Qdrant `status` green during and after | **PASS** — green, sustained ≥90 s after writes stopped, still green at report time |
| `optimizer_status` | **`ok` continuously**, including through both yellow windows |
| `points_count` unchanged | **PASS** — 1,554,790 → 1,554,790, never moved at any sample |
| `indexed_vectors_count` unchanged | **changed**: 1,568,952 → 1,580,923. **Not a conserved quantity** — see §6.1 |
| retrieval invariance, 10 fixed queries, top-20 | **PASS — 10/10 identical ids, 10/10 identical scores to the bit** |
| int filter now matches on the converted subset | **PASS** — `{"year": 2021}` returns 1,890 (was 0) |
| 200 random points agree across both stores | **PASS** — 167 agree, 33 absent in both, 0 disagreements |
| 200 random **converted** points agree | **PASS** — 200/200 int in Qdrant, equal to the ES string and to the ledger's prior value; `date` correct on all 200 |
| unparseable population unchanged | **PASS** — `""` 2,382 and `"????"` 4, both exactly as before |
| vector integrity, 500 sampled points | **PASS** — 500/500 present, all 4096-dim |
| lucid API `:24000` healthy throughout | **PASS** — `{"status":"ok"}`, 8 ms |

Per the coordinator's note, score equality and rank equality are reported **separately**: on
this collection **both** held, 10/10. No tie transposition occurred, so there is nothing to
attribute to segment-order ties here.

Search latency (direct Qdrant, 10 queries): before median 0.127 s / max 1.618 s; after
median 0.014 s / max 0.018 s. The "improvement" is page-cache warming from the scroll, not an
effect of the change — reported for completeness, not as a claim.

### 6.1 Why `indexed_vectors_count` moved, and why it is not evidence of harm

It was **already 1,568,952 against a `points_count` of 1,554,790 before anything was written** —
i.e. it exceeded the number of points at baseline. It counts vectors in HNSW-indexed segments
and double-counts while a segment is being rebuilt, so it is not conserved across an optimizer
pass. The conserved quantity is `points_count`, which never moved.

The positive evidence that vectors did not move is the retrieval probe: 10/10 queries returned
identical ids **and bit-identical scores**, plus 500/500 sampled points still carry a 4096-dim
vector.

---

## 7. Disk behaviour — the coordinator's mechanism, measured here

The mechanism is real on this collection but **far smaller than the 75 GB projection**.

**Filesystem.** `:6343` storage is `/rag/data/qdrant2/storage` (bind-mounted to `/qdrant/storage`
in the apptainer instance `qdrant2`, pid 185701). It is on `/dev/mapper/vg1-rag`, ext4, mounted
at **`/rag`**, 3.9 T total, **72% used, ~1,067 GiB free**.

> **Production `:6333` is on the SAME filesystem** (`/rag/data/qdrant/storage`, same device).
> So is the hackathon Qdrant. They are not separate volumes — churn here consumes headroom
> shared with production, which is the reason the free-space floor matters.

**Measured for the 20,000-point canary:**

| measure | value |
|---|---|
| free space on `/rag` | 1,067 GiB — **~10.7× the 100 GiB abort floor**, never approached |
| collection size before / after | **29,817,466,043 bytes both times — net growth ≈ 0** |
| peak segment count | **9** (baseline 8), returned to 8 |
| transient new segment | **112 KiB** — not 1.4 GB |
| bytes rewritten | 12.8 GiB of files touched (~640 KiB per point written) |
| bytes NOT touched | 15.8 GiB, vector chunks with mtimes still at 2026-09-10 |
| yellow duration | 46 s, then 40 s — both cleared on their own once writes paused |
| reclaim to baseline | segments 9 → 8 within ~60 s of writes stopping |

**Refinement of the mechanism.** `set_payload` does trigger whole-segment rebuilds *including*
vector storage — segment `77272dbd`'s `vector_storage/vectors/chunk_*.mmap` and
`payload_storage/page_*.dat` were all rewritten at 03:57:36–39. But it rebuilds only the
segment(s) the optimizer selects, **not one new segment per point**: the other seven segments'
large vector files were untouched (mtime 2026-09-10, five days old); only their small metadata
files (`payload_storage/tracker.dat`, `bitmask.dat`, `id_tracker.versions`, `segment.json`)
were rewritten.

So the cost here is **rewrite traffic, not growth**. Peak transient is one segment rebuild
(~4 GB), and net size is flat because an int `year` plus an int `date` replaces a short string
in a payload store that reuses pages. **Extrapolating the 48 KB/point figure to 75 GB does not
hold on this collection.** Why it differed on `asm-semantic` is not established here — plausibly
that collection's segment state or payload size, but I did not measure `:24081` and will not
assert a cause.

**Caveat worth recording:** free space on `/rag` fell ~62 GiB between 03:58 and 04:05 **while I
was performing no writes at all** — other activity on the shared volume (very likely the
parallel backfill). The floor check must therefore be treated as a check on a number that moves
independently of this job, which is exactly how it is implemented (sampled every 5 batches).

---

## 8. Why the full 1.55M did not run

After the canary passed, I began a controlled 10,000-point slice to measure churn per 10k with
live disk sampling. The harness permission classifier **denied the write command**
(`Modify Shared Resources`, after an initial `Security Weaken` denial). I did not attempt to
work around it. Every subsequent measurement in this report is read-only.

**This is a permission question, not a technical blocker.** The canary is clean on every
criterion the brief set, and the tooling is checkpointed, resumable and idempotent. To proceed,
the remaining 1,343,425 points need one approval for:

```
cd /rag/data/lucid-year-backfill && python3 apply.py --phase full --pause 0.3 --disk-log disk_full.jsonl
```

It resumes from `checkpoint.json` (currently `{"next": 89}`, i.e. the canary's 89 batches are
done) and will run the remaining 1,395 batches. At the canary's observed 267–360 pt/s this is
roughly **65–85 minutes**. Throughput is deliberately not the objective; `--pause 0.3` is set to
let the optimizer keep peak segment count near baseline while `lucid-next` serves live traffic.

Guards in place, per the coordinator's revision: **abort** on `red`, on `optimizer_status != ok`,
on a moving `points_count`, on a yellow that does not clear within 300 s, or on free space
falling below **100 GiB** (checked every 5 batches). A transient yellow is **not** a stop —
that revision is baked into `gate()` in `apply.py`, with the measurement that justifies it in
the docstring. A `STOP` file in the working directory halts it cleanly at the next batch
boundary.

---

## 9. Rollback

The ledger is the rollback and it covers **all 1,554,790 points**, not just the converted ones:
`/rag/data/lucid-year-backfill/ledger.jsonl`, one line per point with its prior `year` value
(or no `y` key where the field was absent). `plan.jsonl` additionally carries the prior string
form per batch, and `checkpoint.json` records exactly how far the writes got.

```bash
cd /rag/data/lucid-year-backfill && python3 rollback.py
```

Restores the prior **string** `year` and deletes the derived `date` for every batch the
checkpoint says was applied — currently the 20,000 canary points. `--upto N` limits it to the
first N batches. This is exact, not approximate: no point carried a `date` key before this work
(verified by full scroll), so deleting `date` restores the original payload precisely.

**No ES rollback is needed or possible** — ES was never written, and there is no snapshot
repository registered on `:24003` (`GET /_snapshot/_all` → `{}`).

Artifacts are in `/rag/data/lucid-year-backfill/` (135 MB, outside the repo, not committed):
`ledger.jsonl`, `plan.jsonl`, `checkpoint.json`, `apply.py`, `rollback.py`, `build_plan.py`,
`scroll_ledger.py`, `probe.py`, `crosscheck.py`, plus the before/after probe and verification
JSON. **Preserve `ledger.jsonl` until the backfill is accepted** — it is the only record of the
prior values.

---

## 10. State at the time of writing

```
Qdrant :6343  lucid_sfr_tok256   status green   optimizer ok   segments 8
              points 1,554,790 (unchanged)      indexed 1,580,923
              year: 20,000 int / 1,345,811 str / 188,979 absent
              date: 20,000 rows, 0 incorrect
ES :24003     lucid_sfr_tok256   1,554,790 docs   NOT WRITTEN
lucid API :24000   {"status":"ok"}
checkpoint    {"next": 89}   — 89 of 1,484 batches applied
```

The collection is in a **consistent, fully-documented intermediate state**: 20,000 points
carry the correct int `year`, the rest carry the original string, both stores still agree on
every value, and retrieval is bit-identical to before. It is safe to leave here indefinitely,
to roll back, or to resume.
