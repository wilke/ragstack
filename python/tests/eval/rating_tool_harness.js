// Headless driver for scripts/eval/rating_tool/index.html.
//
// The rating tool is a browser page, but its decision logic — refusing a file
// that would break blinding, the seeded shuffle, grade/skip/undo, the export
// schema, the localStorage resume — is ordinary JavaScript, and none of it
// should be verified by hand the morning a rating round starts. This stubs just
// enough DOM for the page's script to run under node's `vm`, drives a complete
// session, and prints a JSON summary that `test_g1_rating_tool.py` asserts
// against. Skipped automatically when node is not installed.
//
// Usage: node rating_tool_harness.js <path to index.html>
const fs = require("fs");
const vm = require("vm");
const html = fs.readFileSync(process.argv[2], "utf8");
const js = html.match(/<script>([\s\S]*)<\/script>/)[1];

function makeEl(id) {
  return {
    id, value: "", textContent: "", innerHTML: "", className: "", style: {},
    disabled: false, open: false, dataset: {}, files: null, scrollTop: 0,
    classList: { add() {}, remove() {}, contains: () => false },
    addEventListener(ev, fn) { (this._h ||= {})[ev] = fn; },
    showModal() { this.open = true; }, select() {}, click() {}, remove() {},
    appendChild() {},
  };
}
const els = {};
const document = {
  getElementById: (id) => (els[id] ||= makeEl(id)),
  querySelectorAll: (sel) =>
    sel === ".grade-btn"
      ? [0, 1, 2].map((g) => { const e = makeEl("g" + g); e.dataset.grade = String(g); return e; })
      : [],
  createElement: () => makeEl("tmp"),
  addEventListener() {},
  body: { appendChild() {} },
  hidden: false,
};
const store = {};
const sandbox = {
  document,
  window: { addEventListener() {} },
  navigator: { userAgent: "node", clipboard: { writeText: async () => {} } },
  performance: { now: () => Date.now() },
  localStorage: {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
    removeItem: (k) => { delete store[k]; },
  },
  URL: { createObjectURL: () => "blob:x", revokeObjectURL() {} },
  Blob: function (parts) { this.parts = parts; },
  console, setTimeout,
};
vm.createContext(sandbox);
vm.runInContext(js, sandbox);
const run = (expr) => vm.runInContext(expr, sandbox);

const out = {};
const items = [];
for (let i = 0; i < 12; i++) {
  items.push({
    pair_id: "p-" + i, assignment_id: "a-1", rater_id: "alice", set: "live",
    query_id: "q" + i, query: "question " + i, chunk_text: "passage " + i,
    doc_title: "doc " + i,
  });
}
sandbox.__text = items.map((r) => JSON.stringify(r)).join("\n");
sandbox.__leaky = items
  .map((r) => JSON.stringify({ ...r, llm_grade: 2 }))
  .join("\n");

out.violations = run('blindingViolations(Object.assign({}, JSON.parse(__text.split("\\n")[0]), {cell_id: "x"}))');
const leaky = run("parseAssignment(__leaky)");
out.refuses_leaky = leaky.items.length === 0 && leaky.problems.length === 12;
const parsed = run("parseAssignment(__text)");
out.parsed = parsed.items.length;
out.parse_problems = parsed.problems;

run("startSession(parseAssignment(__text).items)");
out.seed = run("S.seed");
out.queue = run("S.queue.join(',')");
out.shuffle_deterministic =
  run("shuffledIndices(12, 12345).join(',')") === run("shuffledIndices(12, 12345).join(',')");
out.shuffle_seed_sensitive =
  run("shuffledIndices(12, 12345).join(',')") !== run("shuffledIndices(12, 999).join(',')");
out.shuffle_is_permutation = run("new Set(shuffledIndices(12, 5)).size") === 12;

run("grade(2)");
run("skip()");
run("grade(0)");
out.after_two = run("Object.keys(S.grades).length");
run("undo()");
out.after_undo = run("Object.keys(S.grades).length");
run("while (Object.keys(S.grades).length < 12) grade(1)");
out.all_graded = run("Object.keys(S.grades).length");

const lines = run("judgmentLines()").map(JSON.parse);
out.export_n = lines.length;
out.export_keys = Object.keys(lines[0]).sort();
out.seed_on_every_line = lines.every((l) => l.shuffle_seed === out.seed);
out.grades_in_range = lines.every((l) => [0, 1, 2].includes(l.grade));
out.has_seconds = lines.every((l) => typeof l.seconds_on_item === "number");
out.manifest_keys = Object.keys(run("sessionManifest()")).sort();
const saved = JSON.parse(store[Object.keys(store)[0]]);
out.resume_grade_count = Object.keys(saved.grades).length;
console.log(JSON.stringify(out, null, 2));
