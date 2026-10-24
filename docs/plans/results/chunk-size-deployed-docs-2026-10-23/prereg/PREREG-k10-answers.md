# Pre-registration: k = 10 chunks passed to a model (redacted public copy)

> **Redacted copy.** The private original is kept with the outputs, off GitHub. Its sha256 is
> `dda8af1efbda810cf0bd436793244fc85056c71fee3b391ea4160619231a76e2` (recorded 16:29:19Z on 2026-10-23).
> Endpoint hosts and the gateway owner's name are replaced by neutral labels; the product name in the
> prompts is `<PRODUCT>`. Design, prompts (as templates), rules and arms are unchanged.

Written 2026-10-23. EXPLORATORY.

Owner: "can we make a practical test of k=10 chunks passed to the model? This is informative because most
systems return n chunks."

What had been seen before writing:
- the retrieval results of part 2, part 3 and Addendum S, including found@10 per arm;
- no model answers.

This test asks what deployed systems actually do: pass the top 10 reranked chunks, whatever their size, and
see whether the model answers correctly. It is deliberately **not** budget-matched. Context size is
reported per arm, beside the accuracy.

## Questions
| set | n | gold |
|---|---|---|
| part 3 M | 105 | quote_a + quote_b |
| part 3 D | 65 | answer_quote |
| part 2 | 116 | answer_quote |

- Total: 286, all already generated and validated.
- Group W (no gold) is excluded.
- Results are reported per set, never pooled.

## Arms (reranked top 10, the existing retrieval lists, rules glossary)
- **L:** live semantic, uncapped.
- **SEM512, SEM256:** semantic with a token cap.
- **T128o0, T256o0, T512o0, T1024o0:** fixed token.
- **X512o0:** fixed token within sections.

That is 8 arms.

## Readers
- **Llama-4-Scout** on a local endpoint (max_model_len 60,000).
- **Qwen3.8-27B** on a local endpoint, thinking off (max_model_len 131,072).
- Both run at temperature 0 with max_tokens 400, at most 4 in flight per endpoint. Never the deployed
  assistant's endpoint.
- If a prompt would exceed the window minus 512, chunks are dropped from the tail, and the drops are
  logged. None are expected, since the worst case is about 34k tokens.

**Prompt (template, system)**
```
You answer questions from <PRODUCT> users using only the documentation excerpts provided. If the excerpts do not contain the answer, say "The documentation provided does not answer this." Be concise.
```

**Prompt (template, user)**
```
Documentation excerpts:

[1] {chunk 1}

[2] {chunk 2}
...
[10] {chunk 10}

Question: {question}
```

- Chunks are given in reranked order, each as its text with the glossary field if present.
- Reader calls: 286 × 8 × 2 = 4,576.

## Grader
**Primary: Claude Opus 5 via the Claude gateway.**
- Settings: temperature 0, max_tokens 64, `fallback_model ""`.
- Empty content counts as a failure; the run stops at 5 in a row or more than 20 in total.
- It runs only after the gateway owner's go. The batch size goes to the gateway owner first.
- Calls are deduplicated by (question, normalised answer text), so there are at most 4,576.

**Secondary: Qwen3.8-27B**, thinking off, max_tokens 16, on every answer.
- κ against Opus is reported.
- Qwen grading its own answers is a self-preference risk. It is reported, not corrected.

**Grader prompt (template, user)**
```
A user asked a question about <PRODUCT>. The reference answer is quoted from the documentation.

Question: {question}

Reference (from the documentation):
{gold quote(s), one per line}

Answer given:
{answer}

Grade the answer against the reference:
- correct: it states the information in the reference (wording may differ); extra correct detail is fine.
- partial: it states some but not all of the reference information, or for two-part references only one part.
- incorrect: it contradicts the reference or gives different information.
- abstained: it says the documentation does not answer the question.
Answer with one word: correct, partial, incorrect, or abstained.
```

**Parsing.** The first word, lower-cased and stripped of punctuation. Anything else is unparseable,
counted as incorrect, and listed.

## Endpoints
**Primary:** the number of questions graded *correct* (Opus), per arm, per reader and per set.

**Also reported:**
- correct + partial;
- abstention count;
- incorrect count. Among the incorrect answers, those where the gold was present in the top 10, which is a
  reading failure;
- found@10 from the retrieval run, beside the accuracy, so retrieval failures and reading failures are
  separable;
- context tokens per arm (SFR-Embedding-Mistral tokenizer, chunk text): mean, median and max;
- the prompt and completion tokens actually used.

## Contrasts
Gained/lost/net on *correct*, exact McNemar, and the split by stratum, per reader and per set:
1. Each arm − L.
2. Each T arm − T512o0 (size, at k = 10).
3. SEM512 − L (the cap).

**Rule:**
- net ≥ +5;
- the same sign under the other reader;
- net gains in at least 2 strata.

Contrasts with fewer than 10 discordant pairs are reported as *unresolved*, not as null.

**Reading:**
- Top 10 favours large chunks by construction: 10 × 1,024 tokens is about 8× the text of 10 × 128. Any win
  for a large arm is stated together with its context size.
- Part 2 is near ceiling at retrieval, so its contrasts are expected to be small.

## Load
| step | calls | endpoint |
|---|---|---|
| readers | 2,288 | local Scout |
| readers | 2,288 | local Qwen |
| secondary grader | ≤ 4,576 | local Qwen |
| Opus grader | ≤ 4,576 | Claude gateway, after a go; about 3M input and 50k output tokens (estimate) |

No retrieval reruns (cached lists), no store writes, not the deployed assistant's endpoint.
