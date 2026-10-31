# Alias study: the layer-2 verifier (alias-verify/3)

Status: the prompt in `python/scripts/eval/glossbench_alias_checks.py` (`VERIFY_SYSTEM`,
`verify_prompt`, `VERIFY_VERSION = "alias-verify/3"`). Every verify row and l2 log row records
`verify_version`, and every accepted record records `verifier_prompt_version`. Rows that lack
the field were written by alias-verify/1, the prompt both pilots ran with. This note gives
counts only. Record text stays in the private output directory.

## What changed and why

The owner's design is authoritative. A later judge compares answers from **other** papers
against each question, and a correct answer from another paper counts as correct.
alias-verify/1 departed from that design on two criteria, and it lacked one input.

| key | alias-verify/1 | alias-verify/2 |
|---|---|---|
| `standalone` | "would accept a correct answer from any source". In pilot 2 this led to rejects such as "the percentage only holds for this study's isolates", "presumes this paper's specific culture preparation" and "this paper's own convention". | The question must not refer to its source document: no paper, study or authors, no "we" or "our", no figure, table, section, passage or list of abbreviations, and no unnamed presupposition of the paper's own experiment ("the samples", "Buffer A"). The question **may** ask about a study-specific fact (a cohort, isolates, an experiment, a value or a convention) as long as it names the distinguishing context in its own words. The prompt explicitly forbids a reject on the ground that a fact holds only for that study. |
| `single_reading` | "can be read in exactly one way". In practice the verifier rejected when another valid answer existed. | The question's **meaning** is unambiguous. Several correct answers, or another paper's different answer, are no ground for a reject. Only wording that allows two interpretations of *what is asked* fails. |
| `term_denoted` | For G records, the definition chunk only, which symbols never have (an extracted symbol has no `def_chunk_id`). The verifier rejected "the passage never defines ωL". | G records now carry the **glossary entry** (`in this paper the abbreviation "X" stands for "Y"`, given as true). They also carry the **definition sentence**: the sentence of the entry's definition chunk that holds the alias, preferring one that also holds the primary. The whole chunk is the fallback when no such sentence is found. The verifier is told that by construction the passage contains only the alias. |
| `supports` | strict | Still strict. Stated more fully: the answer must not be inferred, must not be reversed, and must not need text outside the quote (a table value whose column the quote does not name). |

Self-check: `test_glossbench_alias.py::VERIFY_CASES` holds six synthetic records with no real
text:

1. a study-specific fact with its context named (pass)
2. a question that refers to "this study" (standalone fails)
3. an open "what is notable about" question (single_reading fails)
4. a question with many valid answers but a clear meaning (pass)
5. a symbol defined only by the glossary entry (pass)
6. an unsupported answer (supports fails)

Offline tests cover how the input is assembled. With `RAGSTACK_ALIAS_VERIFY_LIVE=1` the same
cases go to the Claude CLI. Before the re-run, all 6 got the expected verdict.

## v1 vs v2 on the pilot drafts

The re-run used the clean commit of this change, with Claude CLI `claude-opus-5-5` only:
no gateway calls and no new drafting. The drafts and the layer-1 verdicts were copied to
`/rag/tmp/alias-study-2026-11/out/verify-v2/{pilot1,pilot2}/`, without the l2 rows. The
originals were left untouched. For pilot 2, `check` first judged the wave-3 drafts, which had
never been checked (13 new layer-1 rows). The verifier then ran on every draft that passed
layer 1: 36 in pilot 1 and 38 in pilot 2. That took 74 CLI calls at $1.20, with 0 refusals and
0 Codex fallbacks.

Accepted share is accepted jobs divided by drafted jobs. S+SR is the number of verified
records rejected on `standalone` or `single_reading`.

| set | verifier | G jobs | G L1-pass | G accepted (share) | C jobs | C L1-pass | C accepted (share) | S+SR rejects / verified |
|---|---|---|---|---|---|---|---|---|
| pilot 1 | v1 | 28 | 25 | 10 (0.36) | 16 | 11 | 6 (0.38) | 19/36 (53%) |
| pilot 1 | v2 | 28 | 25 | 18 (0.64) | 16 | 11 | 7 (0.44) | 8/36 (22%) |
| pilot 2, waves 1-2 | v1 | 16 | 11 | 2 (0.13) | 27 | 18 | 13 (0.48) | 12/29 (41%) |
| pilot 2, waves 1-2 | v2 | 16 | 11 | 9 (0.56) | 27 | 18 | 14 (0.52) | 3/29 (10%) |
| pilot 2, all waves | v2 | 29 | 20 | 17 (0.59) | 27 | 18 | 14 (0.52) | 4/38 (11%) |

Reject reasons under v2:

- pilot 1: G had standalone 5, supports 3, single_reading 1; C had standalone 2, supports 1, single_reading 1.
- pilot 2, all waves: G had supports 2, standalone 1; C had standalone 2, single_reading 2, supports 2.

One record that v1 passed now fails `supports`, because its table value had no column named
in the quote.

The pre-set gate for pilot 2 was G ≥ 0.25, C ≥ 0.25, and S+SR ≤ 25% of verified records.
Under v2 it **passes**: G 0.59, C 0.52, S+SR 11% (waves 1-2 alone: 0.56, 0.52, 10%).

### Spot-read of the newly passing G records (judgement only, no text)

The author read all 24 G records that v1 rejected (or never checked, in wave 3) and that v2
passes:

- **Pilot 1: 3 of 9 look wrong.** One leans on an unnamed "a simulation". One is an open
  "general relationship" question. One narrates "was examined" over a figure caption and has a
  one-word answer. Pilot 1 was drafted under alias-draft/1, and its layer 1 predates #889. The
  current layer 1 rejects all three phrasings before they reach the verifier.
- **Pilot 2: 4 of 15 look doubtful.**
  - A near-vacuous "purpose → analysis" pair from a figure caption.
  - "different groups of children", which presupposes the paper's unnamed cohort.
  - An answer that names the culture but not the purpose asked for.
  - A qualifier ("recovered from the oral cavity") that the quote does not state, so
    `supports` was applied loosely.

Without those 4, pilot 2's G share is 13/29 = 0.45, and the gate still passes. If loose
`supports` judgements keep recurring in the main run, the next tightening should go there.

## alias-verify/3 (tightened after the v2 spot-read)

The v2 spot-read showed two leaks, and v3 closes both.

- **`standalone` must also NAME the distinguishing context.** A generic referent to the
  paper's own experiment, cohort, sample or model fails, with no identifying detail:
  "a simulation", "an experiment", "the model", "the samples", "the isolates", "the patients",
  "different groups of children". A named context such as "Klebsiella pneumoniae oral
  isolates" passes.
- **`supports` is strict about exactly what is asked.** It fails in three cases:
  - the question asks for a purpose, reason, cause or function, and the quote does not
    state one;
  - the question or the answer carries a qualifier that the quote does not state;
  - the answer is near-empty (it restates the question, or is a generic word such as
    "analysis").

Five synthetic cases were added (11 in all): a generic "different groups of children", a
generic "in a simulation", an unstated purpose, an unstated qualifier, and a near-empty
answer. The live self-check got 11/11.

The re-verification ran into `out/verify-v3/{pilot1,pilot2}/` from the clean v3 commit. The
v1 and v2 files were left as they were. It took 74 CLI calls at $1.28, with 0 refusals and 0
Codex fallbacks.

| set | verifier | G accepted / jobs (share) | C accepted / jobs (share) | S+SR rejects / verified |
|---|---|---|---|---|
| pilot 1 | v2 | 18/28 (0.64) | 7/16 (0.44) | 8/36 (22%) |
| pilot 1 | v3 | 16/28 (0.57) | 7/16 (0.44) | 9/36 (25%) |
| pilot 2, waves 1-2 | v2 | 9/16 (0.56) | 14/27 (0.52) | 3/29 (10%) |
| pilot 2, waves 1-2 | v3 | 6/16 (0.38) | 12/27 (0.44) | 4/29 (14%) |
| pilot 2, all waves | v2 | 17/29 (0.59) | 14/27 (0.52) | 4/38 (11%) |
| pilot 2, all waves | v3 | 12/29 (0.41) | 12/27 (0.44) | 5/38 (13%) |

Reject reasons under v3:

- pilot 1: G had standalone 6, supports 4; C had standalone 2, supports 1, single_reading 1.
- pilot 2, all waves: G had supports 6, standalone 2; C had supports 4, standalone 2, single_reading 1.

The pilot-2 gate **passes under v3**: G 0.41 ≥ 0.25, C 0.44 ≥ 0.25, and S+SR 13% ≤ 25%.

**Spot-read of every G record that v3 passes** (28: 16 in pilot 1, 12 in pilot 2;
judgement only):

- **3 doubtful (11%):**
  - pilot 1: an unnamed "in a simulation", even though the device is named;
  - pilot 1: a one-word "correlation" answer drawn from a figure caption;
  - pilot 2: a question qualifier ("enteric rod isolates") that the quote does not state.
- **1 borderline:** the "oral" qualifier on the Klebsiella pneumoniae isolates is not in the
  quote. It is counted as right because it is the owner's own pass example.
- **Under the current layer 1:** both doubtful pilot-1 records came from alias-draft/1, and
  the current layer 1 rejects their phrasing before the verifier sees them. On drafts made
  with the current pipeline, the doubtful rate is 1/12 in pilot 2.
