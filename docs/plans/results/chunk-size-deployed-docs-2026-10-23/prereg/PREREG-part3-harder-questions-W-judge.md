# Pre-registration, part 3: harder questions and a new W judge (redacted public copy)

> **Redacted copy.** The private original is kept with the outputs, off GitHub. Its sha256 is
> `2ddf5407bd1781af61e9d8cd2005dc17fde0e85d6c8dd17e7958f986d9f58c9e` (recorded 16:03:27Z on 2026-10-23).
> The collection's name, endpoint hosts and the gateway owner's name are replaced by neutral labels; the
> product name in the prompts is `<PRODUCT>`. Design, prompts (as templates), rules and arms are unchanged.

Written 2026-10-23, after parts 1, 2 and Addendum S were run and read. Owner go: use Qwen or Claude Opus
through the gateway. EXPLORATORY.

What was seen, and why this exists:
- Part 2 is at its ceiling. Live finds 113 of 116 at b5120, and no arm is more than 5 away, so a
  chunk-size effect couldn't show.
- Part 1's Scout judge answered "yes" on 0 of 3,731 pairs (2,905 partial, 825 no, 1 unparseable). The
  group-W results are therefore void; they are recorded in DEVIATIONS.md and not reported as results.
- Addendum S: SEMlive reproduces live on 336/336 pages, and the missing cap is confirmed (235 chunks over
  512; SEM512 has 0).
- The judges below were chosen **after** seeing Scout's verdict distribution and the per-arm W tables,
  which were all "partial". So W remains descriptive only.

Everything is read in place from the collection's store, read-only. Outputs are written outside the repo
only. The page text, strata, served pipeline, budgets and gold normalisation are those of part 2.

## A. Harder questions (part 3)

### Eligible pages
- Pages whose reassembled text is at least 2,048 SFR-Embedding-Mistral tokens. The count is logged before
  generation.
- 110 pages are a single live chunk, and the median page is 868 tokens. Below about 2k tokens, chunking
  can't separate the arms at a 5,120 budget.

### Type M: multi-span (2 per eligible page)
**Passages**
- Two 1,000-character passages, A and B, from the same page. Each start is snapped to a sentence start, as
  in part 2.
- A's start is uniform over the page. B's start is uniform over the positions at least 1,500 characters
  from A's start in either direction, without overlap. If there is no room, the page gets fewer M
  questions.
- Seed 261024.

**Prompt (template, user; the system prompt is part 2's)**
```
Here are two passages from the same <PRODUCT> documentation page.

<passage_a>
{a}
</passage_a>
<passage_b>
{b}
</passage_b>

Write ONE question a <PRODUCT> user might ask whose full answer needs information from BOTH passages. Rules:
- Use your own words. Do not copy any phrase of four or more words from either passage, except names of tools, commands, file formats or organisms.
- Do not mention "the passage", "this page" or "the documentation".
Then copy, exactly and character for character, the shortest span of passage A (at most 300 characters) and the shortest span of passage B (at most 300 characters) that together answer it.

Answer with JSON only: {"question": "...", "quote_a": "...", "quote_b": "..."}
```

**Validation**
- Each quote must be found in the page under the gold normalisation and be at most 300 characters.
- The two quotes must start at least 1,500 characters apart in the page.
- Failures are dropped and counted. There is no redraw.

**Gold.** A question is *found* at a budget when both quotes are found (quote rule: full, or a ≥ 60%
prefix or suffix) among the delivered chunks. *Both-in-one* (both quotes in a single chunk) is reported as
a descriptive.

### Type D: detail (1 per eligible page)
- One 1,200-character passage, with its start uniform over the middle 80% of the page and snapped. Seed
  261024.
- The prompt is part 2's, with one rule added after the first rule:
  `- Ask about a specific detail (a parameter, option, value, file name or step), not about the page's main topic.`
- Validation is part 2's. In addition, a question with echo ≥ 5 is dropped and counted, so type D is
  low-echo by construction.

### Generator
- Qwen3.8-27B on a local endpoint, thinking off, temperature 0, max_tokens 600, at most 4 in flight. Never
  the deployed assistant's endpoint.
- One attempt; one retry only when the output can't be parsed.
- The call count is logged before the calls start, with a cap of 450 calls. If more than 450 would be
  needed, pages are sampled down, stratified as in part 2, with seed 261024.

### Arms
- L (= SEMlive), SEM512, SEM256, T128–T1024 and X128–X1024, at o0 and s/8.
- Rules glossary, f32 only. Glossary-off and storage variants are not run.

### Endpoints and contrasts
**Endpoints**
- Found at b2048 and b5120. The primary is b5120 for M and b2048 for D.
- Reranked@10.
- Reported per type, M and D, never pooled.

**Contrasts.** Each reports gained/lost/net, an exact McNemar test, and the split by stratum.
1. Size: each T and X size − 512, same family and overlap.
2. Against live: each arm − L.
3. Cap: SEM512 − L and SEM256 − L.
4. Overlap: o(s/8) − o0.

**Rule (unchanged from part 2).** Net ≥ +5 on the primary leg, no loss at b5120, and net gains in at least
2 strata.

**Power.** With n under about 100 per type, a net of +5 is roughly the smallest resolvable effect. Any
contrast whose discordant count is under 10 is reported as *unresolved*, not as null.

## B. A new judge for group W (part 1)

The pool is unchanged: 3,731 (question, chunk) pairs, already built and cached. Scout's labels are kept as
a record only.

**Judges**
- **Qwen3.8-27B** on a local endpoint: thinking off, temperature 0, max_tokens 8, at most 4 in flight.
- **Claude Opus** via a Claude gateway (loopback tunnel, operated by another session): temperature 0,
  max_tokens 8. The batch size goes to the gateway owner before the first call.
  - Estimate: 3,731 calls, about 2M input tokens, under 30k output tokens.
  - If the gateway is unreachable, the fallback is the Claude CLI judge, on a 400-pair sample stratified by
    question. That change is logged.
- **Primary: Opus.** Qwen is the secondary.

**Prompt (template, user, the same for both judges)**
```
Question from a <PRODUCT> user:
{question}

Text from the <PRODUCT> documentation:
<text>
{chunk}
</text>

Does this text tell the user how to do what they ask?
- yes: it gives the steps, or names the tool, service, page or command AND how to use it for this task, enough to get started.
- partial: it is about the right task or tool but leaves out what the user would need to actually do it.
- no: it does not address this task.
Answer with one word: yes, partial, or no.
```

**Parsing.** The first word, lower-cased, stripped of punctuation. Anything else counts as "no" and is
listed.

**Reported**
- For each judge: the verdict distribution.
- For each arm: W yes-hits at r10, b2048 and b5120.
- Between judges: Cohen's κ over the 3,731 pairs (three classes, and yes vs not-yes).
- Degeneracy check, fixed now: if either judge labels under 2% or over 90% of pairs "yes", its W results
  are reported as *degenerate*, not as results.

## Load
- About 450 generation calls and about 3,731 judge calls on the local Qwen endpoint.
- 3,731 Opus calls via the gateway, after the gateway owner has been told.
- Embedding fleet (at most 2 in flight per endpoint); reranker (at most 4 in flight).
- Not the deployed assistant's endpoint, no store writes.
