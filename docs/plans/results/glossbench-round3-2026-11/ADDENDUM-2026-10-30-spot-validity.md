# Addendum, 2026-10-30: layer-3 owner spot-check validity for sets O and A

**Tier: record.** This file corrects nothing in [`RESULTS.md`](RESULTS.md), which stands as written. When
`RESULTS.md` was committed the owner's layer-3 labels had not been imported, so it reports the spot-check
as **absent** ("What is not reported"; Limitations). This addendum reports the check now that the labels
exist. It is a new file because the record is not edited. No test, retrieval or analysis was re-run, and
no hypothesis label in `RESULTS.md` changes.

Source: [`artifacts/spot-validity-r3.json`](artifacts/spot-validity-r3.json) (counts only), built by
[`artifacts/make_spot_validity_r3.py`](artifacts/make_spot_validity_r3.py) at `b1d0371` from a clean tree
(`dirty false`, `untracked 0`, `citable true`, `in_image false`, warning kept). The builder reads the
private sheets, keys, labels and harness imports, recomputes every figure below, checks the as-typed
figures against the harness's own `spot-import`, and records the sha256 of every input. The partner that
holds the papers-OC collection is "the papers-OC owner".

## The rule, as pre-registered

The round-3 prereg (§3.2) carries round 2's acceptance unchanged and names the check as an "owner
spot-check of 40 per set with the 0.85 validity rule, seed `<spot.seed_base>|<set>` with `spot.seed_base`
set to **`261103|spot`** in the private config". The rule itself is round 2's prereg §3.3, layer 3,
quoted exactly:

> a seeded random sample of **40** accepted records per set (`261101|spot|<set>`), shown blind to stratum
> and arm; the owner marks valid / invalid. Reported: validity with a Wilson 95% CI. **Rule:** validity
> below **0.85** at the point estimate makes every result from that set carry *validity below threshold*
> in the sentence; the set is not rebuilt (that would be a forking path).

So the comparison is the **point estimate** k / n against 0.85, not an interval bound. The Wilson interval
is reported beside it and decides nothing. The interval is computed as the harness computes it
(`glossbench_r2_draft.wilson`, z = 1.959963984540054, no continuity correction). The round-3 seed base
replaces round 2's `261101|spot`, as the round-3 text states.

## What was sampled, and how blind it was

| | set O (OA-1000) | set A (ASM-1000) |
|---|---|---|
| population: accepted records | 206 (V 57, M_num 149) | 63 (V 6, M_num 57) |
| sample | 40 (V 8, M_num 32) | 40 (V 4, M_num 36) |
| seed | `261103\|spot\|O` | `261103\|spot\|A` |
| sheet | `spot-a0ac920796d0` | `spot-cd61817eb7d5` |
| accepted file sha256 | `e67378b5…` | `dff0d37d…` |

The samples were drawn by the harness's `spot-export` (`random.Random(seed).sample` over the sorted record
ids). The labeller was the owner.

**Blind to arm and condition.** Each sheet item carries only `item` (a sheet-local id, `s000`–`s039`), the
question, the quantity asked, the drafted answer, the quote, the passage and an empty label. No field names
an arm, a condition, a stratum, a record or a paper (`blind.unblinding_fields_present` is empty for both
sets). The passage is the source chunk's raw content (`spot_export`), with no glossary field rendered. The
key that maps items to records is a separate file, never shown to the labeller.

**Timing.** Both sheets were exported before the first retrieval call (22:33:47Z), going by the private
files' modification times: set O's at about 19:18Z and set A's at about 22:27Z. The labels were written
and imported **after** `RESULTS.md`'s figures existed (imports 00:26Z and 01:08Z the next UTC day). The
sheets carry no arm, so an item's label cannot be tied to an arm outcome from the sheet. But this is not a
labelling that predates the results, and the set-O decision below was also made after them.

## The labels, as counts

| | set O | set A |
|---|---|---|
| labelled `valid` / `invalid` | 40 / 0 | 40 / 0 |
| `denotes_quantity`: yes / unsure | 39 / 1 | 40 / 0 |
| items carrying an owner note | 1 | 2 |

`denotes_quantity` is a field the owner added to each label. The harness reads only `valid` / `invalid`.

## Set O: one item counted as not valid, by owner decision

One set-O item (sheet item `s010`) was typed `valid`, but its `denotes_quantity` is `unsure`, and the
owner's note on it says the question is ambiguous or wrong. The same record is one of the **12** records
the papers-OC owner's census flagged (D-5), in the class *not a quantity*. On 2026-10-30, after the as-typed
import, the owner decided to **count it as not valid**. The artifact lists the decision explicitly
(`sets.O.decisions`, reason code `OWNER_NOTE_QUESTION_AMBIGUOUS_OR_WRONG__DENOTES_QUANTITY_UNSURE`). It
names the record by `sha256(record_id)[:12]` = `54b3ce7c4394`, never by id. The builder asserts the typed
label, the `unsure` and the note before applying the decision.

Both readings, side by side:

| set O | valid / n | validity | Wilson 95% | below 0.85? |
|---|---|---|---|---|
| **reported (owner's decision applied)** | **39 / 40** | **0.975** | **[0.871, 0.996]** | **no** |
| as typed (harness `spot-import`) | 40 / 40 | 1.000 | [0.912, 1.000] | no |

The harness import (`spot-results-O.json`, sha256 `1acbef8b…`, from labels sha256 `3d5ab616…`) reported
40 / 40 valid at `ad2adf1`, from a clean tree (`citable true`), 2026-10-31T01:08:06Z. The builder
reproduces it from the labels exactly. The decision changes the count, not the label: under either
reading, set O's validity is above 0.85.

## Set A

| set A | valid / n | validity | Wilson 95% | below 0.85? |
|---|---|---|---|---|
| reported = as typed | 40 / 40 | 1.000 | [0.912, 1.000] | no |

Harness import `spot-results-A.json` (sha256 `eefe23de…`, labels sha256 `a2e7b528…`): 40 / 40 valid,
at `ad2adf1`, clean, 2026-10-31T00:26:00Z. `denotes_quantity` is `yes` on all 40.

## Set A: two items kept as typed, by owner decision

Two set-A items (sheet items `s005` and `s013`) were typed `valid`, with `denotes_quantity` `yes`, but the
owner's note on each says the number comes from a bibliographic (journal) citation. Neither record is in
set O's census, which covers set O only. On 2026-10-30, after the as-typed import and after this
addendum's first draft, the owner decided to **count both as typed, valid**. The artifact lists the
decisions explicitly (`sets.A.decisions`, reason code `OWNER_KEEP_AS_TYPED`). It names the records by
`sha256(record_id)[:12]` = `69ff2277b1d4` and `90baf7d0d273`, never by id. The builder asserts the typed
label, the `yes` and the note before recording each decision.

Both readings, side by side:

| set A | valid / n | validity | Wilson 95% | below 0.85? |
|---|---|---|---|---|
| **reported (owner's decision applied) = as typed** | **40 / 40** | **1.000** | **[0.912, 1.000]** | **no** |
| what-if: the two kept items counted not valid | 38 / 40 | 0.950 | [0.835, 0.986] | no |

The what-if is not an owner decision and not the reported figure
(`sets.A.what_if_kept_counted_invalid`). Under either reading, set A's validity is above 0.85 at the point
estimate, which is what the rule compares; the what-if's Wilson lower bound (0.835) falls below 0.85, and
under the rule that decides nothing.

D-6's caveat applies to how set A was **drafted**, after the drafter model's upstream change. It does not
apply to this check, which the owner made on the accepted records as they stand. Set A remains descriptive
only, so no hypothesis depends on this figure.

## What the rule gives

- **Set O: validity 39 / 40 = 0.975, Wilson 95% [0.871, 0.996]**, not below 0.85. No result from set O
  carries *validity below threshold*. Set O's status label becomes the pre-registered *our three-layer
  check*, owner-spot-checked (prereg §3, set table), in place of `RESULTS.md`'s interim *our three-layer
  check (layers 1–2)*.
- **Set A: validity 40 / 40 = 1.000, Wilson 95% [0.912, 1.000]**, not below 0.85. Set A's pre-registered
  label is *our three-layer check*, with the floor quoted beside every number. It is unchanged.

## Relation to the papers-OC owner's census (D-5)

The census and the spot-check cover **different populations**, judged by different people on different
questions. The census was a full count of set O's **53 discriminating records** (V 17, M_num 36), judged
by the papers-OC owner on whether the quoted value denotes the quantity asked. The spot-check is a random
**40 of all 206** accepted set-O records, judged by the owner as valid or invalid.

- **8 of the 40** sampled records are among the 53 (V 2, M_num 6). The census accepted all 8.
- **1 of the 40** is among the census's 12 flagged records: the overridden item above, in the class
  *not a quantity*. The census accepted it, but its note marks it as not a quantity, which is why D-5
  flags it.

The overlap is small, so neither check substitutes for the other. The census bounds nothing outside the
53. The spot-check says nothing specific about the discriminating records, beyond the 8 it happens to
include.

## What this does not show

- **A spot-check of 40 bounds the invalid rate. It does not prove it zero.** At 39 / 40 the Wilson upper
  bound on the invalid share is 0.129. At 40 / 40, as typed, it is 0.088. Set O's 206 records and set A's
  63 could each hold invalid records that the sample missed.
- **Validity is the owner's judgement on one question per record.** It is not the census's question, and
  it does not re-check the classes the census found (equation numbers, cross-references, accessions,
  definitional constants, names). The single *not a quantity* record in the sample was labelled `valid`
  until the owner's decision. Set A's two citation-number items were labelled `valid` and the owner kept
  them so. So the
  spot-check alone does not catch that class reliably. D-5 already assigns it to later rounds' layer-1
  rules.
- **`RESULTS.md` stands.** Its hypothesis labels, figures and D-5 sensitivities are untouched. The only
  change is the status of an item `RESULTS.md` reported as absent, which is now measured.
