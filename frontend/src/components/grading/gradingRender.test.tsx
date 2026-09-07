import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { GradingBatch, GradingTask, GradingVerdict } from "../../api/client";
import { saveErrorMessage } from "../../lib/grading";
import { AdjudicationView } from "./AdjudicationView";
import { PairView } from "./PairView";
import { TaskRail } from "./TaskRail";
import { VerdictBar } from "./VerdictBar";

// Render smoke tests, same no-DOM/no-fetch approach as render.test.tsx: mount
// the screen and assert what it puts on the page — and, more to the point here,
// what it must NEVER put on the page.

function render(node: ReactElement): string {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return renderToStaticMarkup(createElement(QueryClientProvider, { client: qc }, node));
}

const batch: GradingBatch = {
  id: "b1",
  name: "R-dev pilot r3",
  kind: "evidence-read",
  status: "open",
  rubric_sha256: "f".repeat(64),
  order_seed: 20260915,
  readers: ["conf-a", "conf-b"],
  task_count: 4,
  progress: [
    { reader: "conf-a", label: "A", saved: 1 },
    { reader: "conf-b", label: "B", saved: 3 },
  ],
  created_at: "2026-09-06T09:00:00Z",
  created_by: "conf-admin",
  adjudicating_at: "",
};

function row(over: Partial<GradingVerdict> = {}): GradingVerdict {
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
    stratum: "model_positive",
    question: {
      id: "2016_1",
      type: "diagnosis",
      summary: "A 78 year old male presents with frequent stools and melena.",
      description: "78 M transferred to nursing home for rehab after CABG.",
    },
    document: {
      doc_id: "PMC3797929",
      title: "Lower Gastrointestinal Bleeding",
      units: [
        {
          index: 0,
          title: "Abstract",
          sentences: [
            { i: 0, text: "LGIB is defined as acute or chronic abnormal blood loss." },
            { i: 1, text: "The incidence of LGIB is one fifth of the upper tract." },
            { i: 2, text: "Bleeding usually stops spontaneously." },
          ],
        },
      ],
    },
    claims: [
      {
        set_index: 1,
        sources: ["scout", "qwen"],
        spans: [
          {
            unit: 0,
            first_sentence: 0,
            last_sentence: 1,
            text: "LGIB is defined as acute or chronic abnormal blood loss.",
          },
        ],
      },
    ],
    extra_questions: [],
    readers: ["conf-a", "conf-b"],
    created_at: "2026-09-06T09:00:00Z",
    created_by: "conf-admin",
    verdict: null,
    ...over,
  };
}

describe("the reader's rail", () => {
  // THE RULE. `GET …/batches/{id}/tasks` returns the caller's OWN seeded
  // permutation — the one s0_rdev.py built the paper readsheets with. If the UI
  // sorted (by pair_id, by unread, by anything) a read begun on those sheets
  // would silently resume at the wrong pair, and nothing on screen would say so.
  it("renders the tasks in the API's order and does not sort them", () => {
    // Deliberately NOT in pair_id order, and with a graded task in the middle —
    // the two orderings a well-meaning sort would impose.
    const order = ["z_pair", "a_pair", "m_pair", "b_pair"];
    const tasks = order.map((pair_id, i) =>
      task({
        id: `t${i}`,
        pair_id,
        verdict: i === 1 ? row({ task_id: `t${i}` }) : null,
      }),
    );

    const html = render(
      createElement(TaskRail, {
        batch,
        tasks,
        current: 0,
        onSelect: () => {},
        label: "A",
      }),
    );

    const positions = order.map((p) => html.indexOf(p));
    expect(positions.every((p) => p >= 0)).toBe(true);
    expect(positions).toEqual([...positions].sort((a, b) => a - b));
    // And the rail numbers them 1..n in that same order, so "pair 3 of 10"
    // means the third pair the SERVER handed over.
    expect(html.indexOf(">1<")).toBeLessThan(html.indexOf(">4<"));
  });

  it("shows the other reader's progress COUNT and nothing else about them", () => {
    const html = render(
      createElement(TaskRail, {
        batch,
        tasks: [task()],
        current: 0,
        onSelect: () => {},
        label: "A",
      }),
    );
    expect(html).toContain("3"); // reader B's saved count
    expect(html).toContain("You are reader");
    expect(html).not.toContain("wrong-location");
  });
});

describe("the pair view", () => {
  const html = render(
    createElement(PairView, {
      task: task(),
      index: 2,
      total: 10,
      judgements: {},
      onJudgement: () => {},
      extraAnswers: {},
      onExtraAnswer: () => {},
    }),
  );

  it("carries the reading guide with each verdict's meaning and 'counts as'", () => {
    expect(html).toContain("How to read a pair, and what the six verdicts mean");
    expect(html).toContain("counts as");
    expect(html).toContain("label OK");
    expect(html).toContain("omission");
    expect(html).toContain("correctly-none");
  });

  it("labels the three sections, and explains the need type as a decision", () => {
    expect(html).toContain("The question — a patient case, and what the clinician needs");
    expect(html).toContain("The claimed answer");
    expect(html).toContain("The document");
    expect(html).toContain("what is the most likely diagnosis for this patient?");
    // Summary and description are named as two forms of ONE question, never as
    // a question and a reason.
    expect(html).toContain("Summary — the case in short");
    expect(html).toContain("Description — the same case in full");
  });

  it("tags each span with its labelers and links into the document", () => {
    expect(html).toContain("scout + qwen");
    expect(html).toContain("unit 0, sentences 0–1");
    expect(html).toContain('href="#u0s0"');
    expect(html).toContain('id="u0s0"');
  });

  it("offers the three per-span judgements as radios", () => {
    expect(html).toContain("located correctly");
    expect(html).toContain("wrong location");
    expect(html).toContain("not minimal");
    expect(html).toContain('type="radio"');
  });

  it("numbers the sentences and renders them as text, never as markup", () => {
    expect(html).toContain("Bleeding usually stops spontaneously.");
    expect(html).toContain("UNIT 0");
  });

  it("says so plainly when no labeler supplied evidence", () => {
    const negative = render(
      createElement(PairView, {
        task: task({ claims: [] }),
        index: 0,
        total: 10,
        judgements: {},
        onJudgement: () => {},
        extraAnswers: {},
        onExtraAnswer: () => {},
      }),
    );
    expect(negative).toContain("No labeler supplied evidence for this pair.");
    expect(negative).toContain("correctly-none");
  });

  it("renders extra questions only when the task has them", () => {
    expect(html).not.toContain("One more question about this pair");
    const pointed = render(
      createElement(PairView, {
        task: task({
          extra_questions: [
            {
              id: "other_passage",
              text: "Does another delivered passage answer the question?",
              answer_type: "yes-no",
            },
          ],
        }),
        index: 0,
        total: 10,
        judgements: {},
        onJudgement: () => {},
        extraAnswers: {},
        onExtraAnswer: () => {},
      }),
    );
    expect(pointed).toContain("One more question about this pair");
    expect(pointed).toContain("Does another delivered passage answer the question?");
  });
});

describe("reader independence, as the screen enforces it", () => {
  // The server guarantees a reader is never SENT another reader's row. This is
  // the client half: even handed a task that carries `reader_verdicts` (which
  // only ever happens for an admin on a frozen batch), the READER'S pair view
  // renders none of it.
  const contaminated = task({
    verdict: row({ verdict: "correct", notes: "MY OWN NOTE" }),
    reader_verdicts: [
      row({ verdict: "correct", notes: "MY OWN NOTE" }),
      row({ reader: "conf-b", verdict: "missed-evidence", notes: "THE OTHER READER'S NOTE" }),
    ],
  });

  it("shows only the caller's own row in the pair view and the verdict bar", () => {
    const pair = render(
      createElement(PairView, {
        task: contaminated,
        index: 0,
        total: 10,
        judgements: {},
        onJudgement: () => {},
        extraAnswers: {},
        onExtraAnswer: () => {},
      }),
    );
    const bar = render(
      createElement(VerdictBar, {
        taskId: contaminated.id,
        value: "correct" as const,
        onChange: () => {},
        notes: "MY OWN NOTE",
        onNotes: () => {},
        onSave: () => {},
        saving: false,
        status: "",
        error: null,
        savedAt: "2026-09-06T10:00:00Z",
        version: 1,
        isLast: false,
      }),
    );
    expect(bar).toContain("MY OWN NOTE");
    expect(pair + bar).not.toContain("THE OTHER READER'S NOTE");
    expect(pair + bar).not.toContain("conf-b");
  });

  it("never asks the server for another reader's row", () => {
    // `GET /v1/grading/tasks/{id}/verdicts/{reader}` answers 404 for anyone
    // else's row — but the UI must not ASK. There is no client helper for that
    // path and no call site anywhere in src/.
    // Comments are stripped first — the rule is worth WRITING DOWN in the code
    // it constrains, and a test that forbade naming the endpoint would push the
    // explanation out of the files that need it.
    const code = (text: string) =>
      text
        .split("\n")
        .filter((l) => !/^\s*(\/\/|\*|\/\*)/.test(l))
        .join("\n");
    const hits: string[] = [];
    const walk = (dir: string) => {
      for (const entry of readdirSync(dir, { withFileTypes: true })) {
        const p = join(dir, entry.name);
        if (entry.isDirectory()) walk(p);
        else if (/\.tsx?$/.test(entry.name) && !entry.name.endsWith(".d.ts")) {
          if (p.endsWith("gradingRender.test.tsx")) continue;
          if (/\/verdicts\//.test(code(readFileSync(p, "utf8")))) hits.push(p);
        }
      }
    };
    walk(join(__dirname, "..", ".."));
    expect(hits).toEqual([]);
  });
});

describe("the verdict bar", () => {
  const base = {
    taskId: "t1",
    value: null,
    onChange: () => {},
    notes: "",
    onNotes: () => {},
    onSave: () => {},
    saving: false,
    status: "",
    error: null,
    savedAt: null,
    version: null,
    isLast: false,
  };

  it("is a radio group of the six verdicts, each naming how it counts", () => {
    const html = render(createElement(VerdictBar, base));
    expect(html).toContain('role="radiogroup"');
    for (const v of [
      "correct",
      "wrong-location",
      "non-minimal",
      "missed-evidence",
      "correctly-none",
      "ambiguous",
    ]) {
      expect(html).toContain(v);
    }
    expect(html).toContain("label error");
    // The hover meaning is a title, and the same sentence is in visible text
    // for keyboard and touch — a title alone is unreachable for both.
    expect(html).toContain("counts as label OK");
  });

  it("speaks the save confirmation through an aria-live region", () => {
    const html = render(
      createElement(VerdictBar, { ...base, status: 'Verdict "correct" recorded for X (version 1).' }),
    );
    expect(html).toContain('aria-live="polite"');
    expect(html).toContain("recorded for X (version 1)");
  });

  it("shows the 409 as a closed read, not as a rejected credential", () => {
    const html = render(
      createElement(VerdictBar, { ...base, error: saveErrorMessage(409), disabled: true }),
    );
    expect(html).toContain("This read is closed for adjudication");
    expect(html).not.toContain("API key");
    // Frozen means the inputs say so, rather than accepting a click that cannot
    // land.
    expect(html).toContain("disabled");
  });
});

describe("the adjudication view", () => {
  const frozen: GradingBatch = { ...batch, status: "adjudicating", adjudicating_at: "2026-09-06T11:00:00Z" };
  const tasks = [
    task({
      id: "t1",
      pair_id: "pair-1",
      reader_verdicts: [
        row({ verdict: "correct", notes: "A's note" }),
        row({ reader: "conf-b", verdict: "non-minimal", notes: "B's note" }),
      ],
      adjudication: null,
    }),
    task({
      id: "t2",
      pair_id: "pair-2",
      reader_verdicts: [
        row({ task_id: "t2", verdict: "correct" }),
        row({ task_id: "t2", reader: "conf-b", verdict: "correct" }),
      ],
      adjudication: null,
    }),
  ];

  const html = render(
    createElement(AdjudicationView, {
      batch: frozen,
      tasks,
      apiKey: "",
      onExport: () => {},
      exporting: false,
      exportStatus: "",
    }),
  );

  it("puts both readers' rows side by side and counts the disagreements", () => {
    expect(html).toContain("Reader A");
    expect(html).toContain("Reader B");
    expect(html).toContain("A&#x27;s note");
    expect(html).toContain("B&#x27;s note");
    expect(html).toContain("1 of 2 pairs where the readers disagree");
    expect(html).toContain("readers disagree");
  });

  it("offers the joint verdict as its own radio group per task", () => {
    expect(html).toContain("Joint verdict for pair-1");
    expect(html).toContain("Save joint verdict");
  });

  it("offers the export", () => {
    expect(html).toContain("Export CSVs");
  });
});
