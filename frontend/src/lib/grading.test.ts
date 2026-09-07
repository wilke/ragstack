import { describe, expect, it } from "vitest";
import {
  adjudicationErrorMessage,
  buildVerdictBody,
  disagreementCount,
  exportFiles,
  extraAnswerList,
  firstUnreadIndex,
  highlightedSentences,
  judgementList,
  judgementMap,
  needGloss,
  nextUnreadIndex,
  readerLabel,
  rubricShort,
  saveErrorMessage,
  SPAN_JUDGEMENTS,
  VERDICTS,
} from "./grading";
import type {
  GradingBatch,
  GradingExportResponse,
  GradingTask,
  GradingVerdict,
} from "../api/client";

// Fixtures small enough to read: one two-set task, one saved row.

function verdict(over: Partial<GradingVerdict> = {}): GradingVerdict {
  return {
    task_id: "t1",
    reader: "conf-a",
    verdict: "correct",
    span_judgements: [],
    extra_answers: [],
    notes: "",
    version: 1,
    saved_at: "2026-09-06T10:00:00Z",
    ...over,
  };
}

function task(over: Partial<GradingTask> = {}): GradingTask {
  return {
    id: "t1",
    batch_id: "b1",
    kind: "evidence-read",
    pair_id: "2016_1__3797929",
    question: { type: "diagnosis", summary: "s", description: "d" },
    document: { doc_id: "PMC1", title: "T", units: [] },
    claims: [],
    extra_questions: [],
    readers: ["conf-a", "conf-b"],
    created_at: "2026-09-06T09:00:00Z",
    created_by: "conf-admin",
    verdict: null,
    ...over,
  };
}

describe("the verdict vocabulary", () => {
  it("is the rubric's six, in the pilot sheet's order", () => {
    expect(VERDICTS.map((v) => v.value)).toEqual([
      "correct",
      "wrong-location",
      "non-minimal",
      "missed-evidence",
      "correctly-none",
      "ambiguous",
    ]);
    // Every one carries the three things the sheet shows: meaning, example and
    // how it scores. A blank here is a chip with no tooltip and no guide row.
    for (const v of VERDICTS) {
      expect(v.meaning.length).toBeGreaterThan(20);
      expect(v.example.length).toBeGreaterThan(20);
      expect(v.countsAs).toBeTruthy();
    }
    // `correctly-none` is a CORRECT outcome, not an error — the distinction the
    // rubric spends a paragraph on.
    expect(VERDICTS.find((v) => v.value === "correctly-none")?.countsAs).toBe("label OK");
    expect(VERDICTS.find((v) => v.value === "missed-evidence")?.countsAs).toBe("omission");
    expect(VERDICTS.find((v) => v.value === "ambiguous")?.countsAs).toBe("neutral");
  });

  it("has the three per-span judgements the contract accepts", () => {
    expect(SPAN_JUDGEMENTS.map((j) => j.value)).toEqual(["located", "wrong", "non-minimal"]);
  });

  it("explains a need type as a decision, and says nothing for an unknown one", () => {
    expect(needGloss("diagnosis")).toContain("diagnosis for this patient");
    expect(needGloss("treatment")).toContain("treatment");
    expect(needGloss("test")).toContain("test or investigation");
    expect(needGloss("something-the-study-added-later")).toBe("");
  });
});

describe("per-span toggles round-trip into the PUT body", () => {
  it("carries every toggle, ordered by (set, span)", () => {
    const map = judgementMap(
      verdict({
        span_judgements: [
          { set: 2, span: 1, judgement: "wrong" },
          { set: 1, span: 2, judgement: "non-minimal" },
          { set: 1, span: 1, judgement: "located" },
        ],
      }),
    );
    // The screen holds them by the sheet's `<set>.<span>` key…
    expect(map).toEqual({ "1.1": "located", "1.2": "non-minimal", "2.1": "wrong" });

    // …and they go back out as the contract's array, in a stable order, so a
    // re-save of an unchanged read produces an identical body.
    const body = buildVerdictBody({
      verdict: "non-minimal",
      judgements: map,
      extraAnswers: {},
      notes: "  second sentence is background  ",
    });
    expect(body).toEqual({
      verdict: "non-minimal",
      span_judgements: [
        { set: 1, span: 1, judgement: "located" },
        { set: 1, span: 2, judgement: "non-minimal" },
        { set: 2, span: 1, judgement: "wrong" },
      ],
      extra_answers: [],
      notes: "second sentence is background",
    });
  });

  it("sends the lists explicitly, because an omitted one CLEARS the stored row", () => {
    const body = buildVerdictBody({
      verdict: "correct",
      judgements: {},
      extraAnswers: {},
      notes: "",
    });
    expect(body.span_judgements).toEqual([]);
    expect(body.extra_answers).toEqual([]);
    expect(body.notes).toBe("");
  });

  it("drops a malformed key rather than sending a span the task cannot have", () => {
    // The server 422s on a (set, span) the task does not have. A key that is not
    // `<int>.<int>` is a UI bug, and shipping it would fail the whole save.
    expect(judgementList({ "1.1": "located", nonsense: "wrong", "0.1": "wrong" })).toEqual([
      { set: 1, span: 1, judgement: "located" },
    ]);
  });

  it("keeps only answered extra questions", () => {
    expect(extraAnswerList({ q1: "yes", q2: "" })).toEqual([{ id: "q1", answer: "yes" }]);
  });
});

describe("the document highlight", () => {
  it("marks every sentence a claimed span covers, inclusive of both ends", () => {
    const keys = highlightedSentences([
      {
        set_index: 1,
        sources: ["scout"],
        spans: [{ unit: 0, first_sentence: 2, last_sentence: 4, text: "" }],
      },
      {
        set_index: 2,
        sources: ["qwen"],
        spans: [{ unit: 3, first_sentence: 0, last_sentence: 0, text: "" }],
      },
    ]);
    expect([...keys].sort()).toEqual(["0:2", "0:3", "0:4", "3:0"]);
  });
});

describe("resume and advance walk the server's order", () => {
  const tasks = [
    task({ id: "a", verdict: verdict({ task_id: "a" }) }),
    task({ id: "b" }),
    task({ id: "c", verdict: verdict({ task_id: "c" }) }),
    task({ id: "d" }),
  ];

  it("resumes at the first pair with no row of the caller's own", () => {
    expect(firstUnreadIndex(tasks)).toBe(1);
  });

  it("resumes at the start of the read when everything is graded", () => {
    expect(firstUnreadIndex([tasks[0], tasks[2]])).toBe(0);
  });

  it("advances to the next unread, then wraps once, then stops", () => {
    expect(nextUnreadIndex(tasks, 1)).toBe(3);
    // From the last pair it wraps to pick up one skipped earlier — it does not
    // re-order anything, it only scans the order the server gave.
    expect(nextUnreadIndex(tasks, 3)).toBe(1);
    expect(nextUnreadIndex([tasks[0], tasks[2]], 0)).toBeNull();
  });
});

describe("save failures", () => {
  it("words a 409 as the read being closed for adjudication", () => {
    // The one status that is neither a permission problem nor a bad body: the
    // batch left `open` while the pair was on screen.
    expect(saveErrorMessage(409)).toBe(
      "This read is closed for adjudication — verdicts can no longer change.",
    );
    expect(saveErrorMessage(409)).not.toMatch(/key|permission|invalid/i);
  });

  it("distinguishes a refusal from an unreachable API", () => {
    expect(saveErrorMessage(403)).toContain("not one of this read's readers");
    expect(saveErrorMessage(null)).toContain("Could not reach the API");
    expect(saveErrorMessage(500)).toContain("error 500");
  });

  it("words the adjudicator's 409 the other way round — the batch is still open", () => {
    expect(adjudicationErrorMessage(409)).toContain("still open");
    expect(adjudicationErrorMessage(403)).toContain("Only an admin");
  });
});

describe("export", () => {
  const envelope: GradingExportResponse = {
    batch_id: "b1",
    name: "R-dev pilot r3",
    kind: "evidence-read",
    status: "adjudicating",
    rubric_sha256: "a".repeat(64),
    order_seed: 20260915,
    exported_at: "2026-09-06T12:00:00Z",
    readers: [
      { subject: "conf-a", label: "A" },
      { subject: "conf-b", label: "B" },
    ],
    csv: [
      { filename: "rdev_verdicts_A.csv", reader: "conf-a", label: "A", content: "a-csv" },
      { filename: "rdev_verdicts_B.csv", reader: "conf-b", label: "B", content: "b-csv" },
      { filename: "rdev_verdicts_ADJ.csv", reader: null, label: "ADJ", content: "adj-csv" },
    ],
    verdicts: [],
    adjudications: [],
  };

  it("builds the filenames the scorer reads, in the envelope's order", () => {
    const files = exportFiles(envelope);
    // s0_rdev_score.py --a/--b/--adjudicated reads these BY NAME. The client
    // must not invent or normalise them.
    expect(files.slice(0, 3).map((f) => f.filename)).toEqual([
      "rdev_verdicts_A.csv",
      "rdev_verdicts_B.csv",
      "rdev_verdicts_ADJ.csv",
    ]);
    expect(files.slice(0, 3).map((f) => f.content)).toEqual(["a-csv", "b-csv", "adj-csv"]);
    expect(files.slice(0, 3).every((f) => f.mime.startsWith("text/csv"))).toBe(true);
  });

  it("adds the JSON the CSV columns cannot carry", () => {
    const json = exportFiles(envelope).at(-1);
    expect(json?.filename).toBe("grading_b1_verdicts.json");
    expect(json?.mime).toContain("application/json");
    const parsed = JSON.parse(json?.content ?? "{}") as Record<string, unknown>;
    // Per-span judgements and extra answers are the reason this file exists.
    expect(parsed).toHaveProperty("verdicts");
    expect(parsed).toHaveProperty("adjudications");
    expect(parsed.rubric_sha256).toBe("a".repeat(64));
  });
});

describe("adjudication bookkeeping", () => {
  it("counts a disagreement only where two visible rows differ", () => {
    const rows = [
      task({
        id: "1",
        reader_verdicts: [verdict({ verdict: "correct" }), verdict({ verdict: "non-minimal" })],
      }),
      task({ id: "2", reader_verdicts: [verdict(), verdict()] }),
      // One reader only: an incomplete read, not a disagreement.
      task({ id: "3", reader_verdicts: [verdict({ verdict: "ambiguous" })] }),
      task({ id: "4" }),
    ];
    expect(disagreementCount(rows)).toBe(1);
  });
});

describe("batch presentation", () => {
  const batch: GradingBatch = {
    id: "b1",
    name: "R-dev pilot r3",
    kind: "evidence-read",
    status: "open",
    rubric_sha256: "0123456789abcdef".repeat(4),
    order_seed: 20260915,
    readers: ["conf-a", "conf-b"],
    task_count: 10,
    progress: [
      { reader: "conf-a", label: "A", saved: 3 },
      { reader: "conf-b", label: "B", saved: 0 },
    ],
    created_at: "2026-09-06T09:00:00Z",
    created_by: "conf-admin",
    adjudicating_at: "",
  };

  it("resolves the caller's reader letter, and null when they are not a reader", () => {
    expect(readerLabel(batch, "conf-b")).toBe("B");
    expect(readerLabel(batch, "conf-admin")).toBeNull();
    expect(readerLabel(batch, null)).toBeNull();
  });

  it("shows enough rubric hash to compare by eye", () => {
    expect(rubricShort(batch.rubric_sha256)).toBe("0123456789ab");
  });
});
