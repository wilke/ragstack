---
description: Ask the RAGStack knowledge base a grounded, cited question
argument-hint: [question]
---

Answer the user's question using the RAGStack knowledge base via the bundled
`ragstack` MCP server.

Question: $ARGUMENTS

Steps:

1. If the question is empty, ask the user what they want to look up and stop.
2. Call the `answer` tool with `query` set to the question. Omit `collection`
   to use the server default (`demo_g1_sfr_tok512`), unless the user named a
   specific collection — in that case pass it as `collection`. If you are unsure
   which collections exist, call `list_collections` first.
3. If `answer` reports that no LLM is configured and returns raw passages
   instead, synthesize the answer yourself from those passages.
4. Present the answer followed by a **Sources** list citing each passage the
   answer drew on (collection id + document/chunk identifiers returned by the
   tool). Do not state anything the retrieved passages do not support; if the
   knowledge base lacks the answer, say so plainly.
