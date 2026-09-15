import { describe, expect, it } from "vitest";
import {
  buildFilters,
  buildPrompt,
  buildQuery,
  collectionDetail,
  collectionUnavailable,
  DATA_TYPES,
  modeById,
  modeUnusable,
  modesFrom,
  parseTable,
  sortedCollections,
  templateVars,
  toTsv,
  truncationNote,
  type QueryFields,
} from "./extraction";
import type { CollectionInfo, PromptTemplate, Source } from "./api";

const fields = (overrides: Partial<QueryFields> = {}): QueryFields => ({
  organism: "",
  genes: "",
  otherTerms: "",
  dataTypeId: "none",
  ...overrides,
});

describe("buildQuery", () => {
  it("joins non-empty trimmed fields with spaces", () => {
    expect(buildQuery(fields({ organism: " E. coli ", genes: "recA", otherTerms: "biofilm" }))).toBe(
      "E. coli recA biofilm",
    );
  });

  it("includes the data-type label only when that type has columns", () => {
    expect(buildQuery(fields({ organism: "E. coli", dataTypeId: "ppi" }))).toBe(
      "E. coli Protein-Protein Interaction (PPI)",
    );
  });

  it("contributes nothing for data type 'none'", () => {
    expect(buildQuery(fields({ organism: "E. coli", dataTypeId: "none" }))).toBe("E. coli");
  });

  it("collapses whitespace-only fields", () => {
    expect(buildQuery(fields({ organism: "   ", genes: "\t", otherTerms: "recA" }))).toBe("recA");
  });

  it("returns an empty string when every field is empty", () => {
    expect(buildQuery(fields())).toBe("");
  });
});

describe("buildFilters", () => {
  it("converts a clean integer year to a NUMBER", () => {
    const f = buildFilters({ year: "2025" });
    expect(f.year).toBe(2025);
    expect(typeof f.year).toBe("number");
  });

  it.each(["20xx", "2025.5", "", "  "])("omits a non-integer year (%j) entirely", (bad) => {
    const f = buildFilters({ year: bad });
    expect(f).not.toHaveProperty("year");
  });

  it("passes docType through as a trimmed string", () => {
    expect(buildFilters({ docType: "  paper  " })).toEqual({ doc_type: "paper" });
  });

  it("returns {} rather than {year: undefined} when nothing is set", () => {
    expect(buildFilters({})).toEqual({});
    expect(Object.keys(buildFilters({}))).toHaveLength(0);
  });

  it("combines a valid year and docType", () => {
    expect(buildFilters({ year: "2020", docType: "review" })).toEqual({ year: 2020, doc_type: "review" });
  });
});

describe("buildPrompt", () => {
  const source = (overrides: Partial<Source> = {}): Source => ({
    doc_id: "d1",
    chunk_id: "c1",
    content: "some content",
    score: 1,
    ...overrides,
  });

  it("emits tab-joined columns and the TSV instruction for format 'table' with a columned data type", () => {
    const p = buildPrompt(fields({ organism: "E. coli", dataTypeId: "ppi" }), "table", []);
    expect(p).toContain("Pathogen\tProtein A\tProtein B\tInteraction Type\tMethod\tAssertion\tReference");
    expect(p).toContain("Return ONLY a TSV (tab-separated values) table with these columns:");
  });

  it("falls back to the prose instruction when format is 'table' but the data type has no columns", () => {
    const p = buildPrompt(fields({ organism: "E. coli", dataTypeId: "none" }), "table", []);
    expect(p).not.toContain("Return ONLY a TSV");
    expect(p).toContain("provide a comprehensive answer about relevant findings");
  });

  it("includes the genes clause only when genes is non-empty", () => {
    const withGenes = buildPrompt(fields({ organism: "E. coli", genes: "recA" }), "raw", []);
    const withoutGenes = buildPrompt(fields({ organism: "E. coli" }), "raw", []);
    expect(withGenes).toContain("involving genes/proteins: recA");
    expect(withoutGenes).not.toContain("involving genes/proteins:");
  });

  it("includes the otherTerms clause only when otherTerms is non-empty", () => {
    const withOther = buildPrompt(fields({ organism: "E. coli", otherTerms: "biofilm" }), "raw", []);
    const withoutOther = buildPrompt(fields({ organism: "E. coli" }), "raw", []);
    expect(withOther).toContain(", related to: biofilm");
    expect(withoutOther).not.toContain(", related to:");
  });

  it("formats each source as a numbered block, with (title) and [DOI: x] only when present", () => {
    const sources = [
      source({ metadata: { title: "Title A", doi: "10.1/a" }, content: "content A" }),
      source({ metadata: { title: "Title B" }, content: "content B" }),
      source({ content: "content C" }),
    ];
    const p = buildPrompt(fields({ organism: "E. coli" }), "raw", sources);
    expect(p).toContain("Source 1 (Title A) [DOI: 10.1/a]:\ncontent A");
    expect(p).toContain("Source 2 (Title B):\ncontent B");
    expect(p).not.toContain("Source 2 (Title B) [DOI:");
    expect(p).toContain("Source 3:\ncontent C");
    expect(p).not.toContain("Source 3 (");
  });
});

describe("parseTable", () => {
  it("parses plain TSV with a header and rows", () => {
    const t = parseTable("Name\tYear\nAda\t1815\nAlan\t1912");
    expect(t).toEqual({
      headers: ["Name", "Year"],
      rows: [
        ["Ada", "1815"],
        ["Alan", "1912"],
      ],
    });
  });

  it("strips ``` fences with no language tag", () => {
    const t = parseTable("```\nName\tYear\nAda\t1815\n```");
    expect(t).toEqual({ headers: ["Name", "Year"], rows: [["Ada", "1815"]] });
  });

  it("strips ```tsv fences", () => {
    const t = parseTable("```tsv\nName\tYear\nAda\t1815\n```");
    expect(t).toEqual({ headers: ["Name", "Year"], rows: [["Ada", "1815"]] });
  });

  it("parses a markdown table, dropping the |---|---| separator row", () => {
    const t = parseTable("| Name | Year |\n|------|------|\n| Ada | 1815 |");
    expect(t).toEqual({ headers: ["Name", "Year"], rows: [["Ada", "1815"]] });
  });

  it("returns null for fewer than 2 usable lines", () => {
    expect(parseTable("")).toBeNull();
    expect(parseTable("just a header row")).toBeNull();
  });

  it("returns null when a header is followed only by a separator row (no data)", () => {
    expect(parseTable("| Name | Year |\n|------|------|")).toBeNull();
  });

  it("preserves an empty cell as data in TSV mode", () => {
    const t = parseTable("Name\tGene\tYear\nAda\t\t1815");
    expect(t).toEqual({ headers: ["Name", "Gene", "Year"], rows: [["Ada", "", "1815"]] });
  });

  it("strips empty leading/trailing cells in pipe mode (contrast with TSV)", () => {
    const t = parseTable("| Name | Year |\n| Ada | 1815 |");
    // The `|`-split of "| Ada | 1815 |" yields empty leading/trailing cells
    // that pipe mode drops, unlike the TSV case above.
    expect(t).toEqual({ headers: ["Name", "Year"], rows: [["Ada", "1815"]] });
  });

  it("keeps an INTERIOR empty cell in pipe mode — only the split artifacts go", () => {
    // Regression: filtering EVERY empty cell (rather than just the leading and
    // trailing ones the `|`-split produces) collapsed this row to
    // ["Ada", "1815"], shifting Year into the Gene column and silently
    // mis-attributing the data. An interior empty is a real value here, exactly
    // as it is in TSV above. The original Dojo widget has the unfixed version.
    const t = parseTable("| Name | Gene | Year |\n| Ada |  | 1815 |");
    expect(t).toEqual({ headers: ["Name", "Gene", "Year"], rows: [["Ada", "", "1815"]] });
  });
});

describe("toTsv", () => {
  it("round-trips headers and rows as tab-joined, newline-separated text", () => {
    const table = {
      headers: ["Name", "Year"],
      rows: [
        ["Ada", "1815"],
        ["Alan", "1912"],
      ],
    };
    expect(toTsv(table)).toBe("Name\tYear\nAda\t1815\nAlan\t1912");
  });
});

const collection = (overrides: Partial<CollectionInfo> = {}): CollectionInfo => ({
  id: "oa-dev",
  label: "OA JATS prototype (dev) — mixed prose+table/figure units",
  model: "Salesforce/SFR-Embedding-Mistral",
  dim: 4096,
  chunk_method: "fixed_token",
  chunk_size: 512,
  state: "active",
  count: 24263,
  text_count: 24263,
  ...overrides,
});

const emptyCollection = (overrides: Partial<CollectionInfo> = {}): CollectionInfo => ({
  id: "ragstack_salesforce_...",
  label: "ragstack_salesforce_...",
  model: "Salesforce/SFR-Embedding-Mistral",
  chunk_method: "fixed",
  chunk_size: 512,
  state: null,
  count: 0,
  text_count: 0,
  ...overrides,
});

describe("collectionUnavailable", () => {
  it("reports 'empty' when count is exactly 0", () => {
    expect(collectionUnavailable(emptyCollection())).toBe("empty");
  });

  it("treats count: null as available (server could not compute it, not empty)", () => {
    expect(collectionUnavailable(collection({ count: null }))).toBeNull();
  });

  it("treats count: undefined as available (server could not compute it, not empty)", () => {
    expect(collectionUnavailable(collection({ count: undefined }))).toBeNull();
  });

  it("is available for a real positive count", () => {
    expect(collectionUnavailable(collection({ count: 24263 }))).toBeNull();
  });

  it("flags 'dormant' as unavailable, with a reason mentioning restoring", () => {
    const reason = collectionUnavailable(collection({ state: "dormant" }));
    expect(reason).not.toBeNull();
    expect(reason).toContain("restoring");
  });

  it("flags 'restoring' as unavailable, with a reason mentioning restoring", () => {
    const reason = collectionUnavailable(collection({ state: "restoring" }));
    expect(reason).not.toBeNull();
    expect(reason).toContain("restoring");
  });

  it("flags 'lost' as unavailable with a non-null reason", () => {
    expect(collectionUnavailable(collection({ state: "lost" }))).not.toBeNull();
  });

  it("treats state 'active' as available", () => {
    expect(collectionUnavailable(collection({ state: "active" }))).toBeNull();
  });

  it("treats state: null as available (untracked collection, not an error)", () => {
    expect(collectionUnavailable(collection({ state: null }))).toBeNull();
  });

  it("treats an absent state as available", () => {
    expect(collectionUnavailable(collection({ state: undefined }))).toBeNull();
  });

  it("reports 'empty' even when state is 'active', if count is 0 (count takes precedence)", () => {
    expect(collectionUnavailable(collection({ count: 0, state: "active" }))).toBe("empty");
  });
});

describe("sortedCollections", () => {
  it("puts queryable collections before unavailable ones", () => {
    const dormant = collection({ id: "dormant", state: "dormant" });
    const queryable = collection({ id: "queryable", state: "active", count: 5 });
    expect(sortedCollections([dormant, queryable]).map((c) => c.id)).toEqual(["queryable", "dormant"]);
  });

  it("within the queryable group, sorts by larger count first", () => {
    const small = collection({ id: "small", count: 10 });
    const big = collection({ id: "big", count: 1000 });
    const medium = collection({ id: "medium", count: 100 });
    expect(sortedCollections([small, big, medium]).map((c) => c.id)).toEqual(["big", "medium", "small"]);
  });

  it("sorts a null-count entry (queryable) after counted ones, using -1", () => {
    const counted = collection({ id: "counted", count: 1 });
    const uncounted = collection({ id: "uncounted", count: null, state: "active" });
    expect(sortedCollections([uncounted, counted]).map((c) => c.id)).toEqual(["counted", "uncounted"]);
  });

  it("does not mutate the input array", () => {
    const a = collection({ id: "a", count: 1 });
    const b = collection({ id: "b", count: 1000 });
    const input = [a, b];
    sortedCollections(input);
    expect(input).toEqual([a, b]);
  });

  it("returns an empty array for empty input", () => {
    expect(sortedCollections([])).toEqual([]);
  });
});

describe("collectionDetail", () => {
  it("includes a thousands-separated count, the model's last path segment, and chunk_method with chunk_size", () => {
    expect(collectionDetail(collection())).toBe("24,263 chunks · SFR-Embedding-Mistral · fixed_token 512");
  });

  it("omits the count part cleanly when count is absent", () => {
    expect(collectionDetail(collection({ count: undefined }))).toBe("SFR-Embedding-Mistral · fixed_token 512");
  });

  it("omits the model part cleanly when model is absent", () => {
    expect(collectionDetail(collection({ model: undefined }))).toBe("24,263 chunks · fixed_token 512");
  });

  it("omits the chunk part cleanly when chunk_method is absent", () => {
    expect(collectionDetail(collection({ chunk_method: undefined }))).toBe("24,263 chunks · SFR-Embedding-Mistral");
  });

  it("omits the chunk_size suffix (no stray space) when chunk_size is absent, keeping chunk_method", () => {
    expect(collectionDetail(collection({ chunk_size: undefined }))).toBe(
      "24,263 chunks · SFR-Embedding-Mistral · fixed_token",
    );
  });

  it("produces no stray separators and no 'undefined' text anywhere", () => {
    const detail = collectionDetail(collection({ count: undefined, model: undefined }));
    expect(detail).toBe("fixed_token 512");
    expect(detail).not.toContain("undefined");
    expect(detail).not.toMatch(/·\s*·/);
    expect(detail.startsWith("·")).toBe(false);
    expect(detail.endsWith("·")).toBe(false);
  });

  it("returns an empty string when none of count, model, or chunk_method are present", () => {
    expect(collectionDetail({ id: "bare", label: "Bare collection" })).toBe("");
  });
});

describe("parseTable — prose is not a table (rewrite)", () => {
  it("returns null for plain prose with no delimiter at all", () => {
    expect(parseTable("No relevant interactions were found.\nPlease refine the query.")).toBeNull();
  });

  it("treats two prose lines that each carry an incidental '|' as prose, not a table", () => {
    // Regression: the majority vote counted lines CONTAINING a pipe, so two
    // sentences using "|" as an or-separator became a two-cell table with a
    // working Download button — the same failure the tab branch was rewritten
    // to prevent. A pipe table must also look like one: pipe-bounded rows, or a
    // |---| separator. Prose is neither.
    expect(
      parseTable("The result is unclear | uncertain.\nWe could not find evidence | at all."),
    ).toBeNull();
  });

  it("drops a lead-in sentence that does not split into >= 2 cells, so the next line becomes the header", () => {
    const t = parseTable("Here is the table you asked for:\nA\tB\nx\ty");
    expect(t).toEqual({ headers: ["A", "B"], rows: [["x", "y"]] });
  });
});

describe("parseTable — an all-dash TSV row is data, not a separator", () => {
  it("keeps a '-\\t-' data row (models write '-' for unknown)", () => {
    const t = parseTable("A\tB\n-\t-\nx\ty");
    expect(t).toEqual({ headers: ["A", "B"], rows: [["-", "-"], ["x", "y"]] });
  });

  it("contrast: a real markdown separator (3+ dashes) is still dropped, while a single-dash data row in the same table survives", () => {
    const t = parseTable("| A | B |\n| --- | --- |\n| - | - |");
    expect(t).toEqual({ headers: ["A", "B"], rows: [["-", "-"]] });
  });
});

describe("parseTable — escaped pipes and inline fences", () => {
  it("unescapes a backslash-escaped pipe as a literal character within one cell, not a delimiter", () => {
    const t = parseTable("| A | B |\n| a \\| b | y |");
    expect(t).toEqual({ headers: ["A", "B"], rows: [["a | b", "y"]] });
  });

  it("preserves an inline triple-backtick occurrence inside a cell — only a fence-only LINE is stripped", () => {
    const t = parseTable("A\tB\nuse ```code``` here\ty");
    expect(t).toEqual({ headers: ["A", "B"], rows: [["use ```code``` here", "y"]] });
  });
});

describe("parseTable — rectangularity", () => {
  it("widens the header with empty column names when a row has more cells than the header, so every row and the header share one width", () => {
    const t = parseTable("A\tB\nx\ty\tz");
    expect(t).toEqual({ headers: ["A", "B", ""], rows: [["x", "y", "z"]] });
    // The screen-vs-download consistency fix: headers and every row agree on
    // length, and so does the TSV the download button produces from them.
    expect(t!.headers).toHaveLength(3);
    for (const row of t!.rows) expect(row).toHaveLength(t!.headers.length);
    const tsvLines = toTsv(t!).split("\n");
    expect(tsvLines[0].split("\t")).toHaveLength(t!.headers.length);
    for (const line of tsvLines.slice(1)) expect(line.split("\t")).toHaveLength(t!.headers.length);
  });
});

describe("buildFilters — tightened year validation", () => {
  it.each(["0x7E9", "1e3", "2.025e3", "+2025", "20255555555555555555", "999", "12345", "1499", "2101"])(
    "omits a year that is not four plain digits in [1500, 2100] (%j)",
    (bad) => {
      const f = buildFilters({ year: bad });
      expect(f).not.toHaveProperty("year");
    },
  );

  it.each(["2025", "1500", "2100"])("accepts %j as a number", (good) => {
    const f = buildFilters({ year: good });
    expect(f.year).toBe(Number(good));
    expect(typeof f.year).toBe("number");
  });

  it("passes journal through as a trimmed string", () => {
    expect(buildFilters({ journal: "  Nature  " })).toEqual({ journal: "Nature" });
  });

  it("omits journal when blank", () => {
    expect(buildFilters({ journal: "   " })).not.toHaveProperty("journal");
  });
});

// server-side templates (ADR-0008) -------------------------------------------

const template = (overrides: Partial<PromptTemplate> = {}): PromptTemplate => ({
  id: "ppi-extraction",
  version: 1,
  hash: "b883138c8cc58b36",
  label: "Protein-Protein Interaction (PPI)",
  output: "table",
  columns: ["Pathogen", "Protein A", "Protein B", "Interaction Type", "Method", "Assertion", "Reference"],
  slots: [
    { name: "organism", required: true, max_len: 120 },
    { name: "genes", required: false, max_len: 200 },
    { name: "other_terms", required: false, max_len: 200 },
  ],
  ...overrides,
});

const textTemplate = (overrides: Partial<PromptTemplate> = {}): PromptTemplate => ({
  id: "literature-summary",
  version: 1,
  hash: "a1c9e6f0d2b47318",
  label: "Literature Summary",
  output: "text",
  slots: [{ name: "organism", required: true, max_len: 120 }],
  ...overrides,
});

describe("modesFrom — no server templates (fallback)", () => {
  it("falls back to the built-in DATA_TYPES: same ids, labels, and columns", () => {
    const modes = modesFrom([]);
    expect(modes.map((m) => m.id)).toEqual(DATA_TYPES.map((d) => d.id));
    expect(modes.map((m) => m.label)).toEqual(DATA_TYPES.map((d) => d.label));
    expect(modes.map((m) => m.columns)).toEqual(DATA_TYPES.map((d) => d.columns));
  });

  it("gives every fallback mode a null templateId — that null routes the two-leg Copilot path", () => {
    const modes = modesFrom([]);
    expect(modes.length).toBeGreaterThan(0);
    for (const m of modes) expect(m.templateId).toBeNull();
  });
});

describe("modesFrom — server templates present", () => {
  it("takes id, label, and templateId from the template, not the built-ins", () => {
    const t = template();
    const modes = modesFrom([t]);
    expect(modes).toEqual([
      {
        id: "ppi-extraction",
        label: "Protein-Protein Interaction (PPI)",
        columns: t.columns,
        templateId: "ppi-extraction",
        // Null because every required slot of this template is one the form can
        // supply; see modeUnusable.
        unusable: null,
      },
    ]);
  });

  it("resolves a BUILT-IN mode, whose templateId is null", () => {
    // The gap a mutation probe found: every modeById test used server modes, so
    // matching on `templateId` instead of `id` passed the whole suite while
    // breaking the fallback path — the data-type dropdown would snap back to the
    // first entry on every change, with nothing to see it.
    const modes = modesFrom([]);
    expect(modeById(modes, "ppi")?.label).toBe("Protein-Protein Interaction (PPI)");
    expect(modeById(modes, "none")).toBeDefined();
  });

  it("uses a table template's declared columns", () => {
    const modes = modesFrom([template({ columns: ["A", "B"] })]);
    expect(modes[0].columns).toEqual(["A", "B"]);
  });

  it("gives a text template columns: null, not []", () => {
    const modes = modesFrom([textTemplate()]);
    expect(modes[0].columns).toBeNull();
  });

  it("yields [] rather than undefined for a table template with no columns declared", () => {
    const noColumns: PromptTemplate = {
      id: "bare-table",
      version: 1,
      hash: "deadbeefcafef00d",
      label: "Bare Table",
      output: "table",
      slots: [],
    };
    const modes = modesFrom([noColumns]);
    expect(modes[0].columns).toEqual([]);
    expect(modes[0].columns).not.toBeUndefined();
  });
});

describe("modeById", () => {
  it("finds a mode by id", () => {
    const modes = modesFrom([template(), textTemplate()]);
    expect(modeById(modes, "literature-summary")).toEqual(modes[1]);
  });

  it("returns undefined for an unknown id, so callers can fall back to modes[0]", () => {
    const modes = modesFrom([template()]);
    expect(modeById(modes, "no-such-id")).toBeUndefined();
  });
});

describe("templateVars", () => {
  it("maps organism/genes/otherTerms onto slot names organism/genes/other_terms", () => {
    const f = fields({ organism: "E. coli", genes: "recA", otherTerms: "biofilm" });
    expect(templateVars(template(), f)).toEqual({
      organism: "E. coli",
      genes: "recA",
      other_terms: "biofilm",
    });
  });

  it("sends only slots the template declares — a form field with no matching slot is absent", () => {
    const t = template({ slots: [{ name: "organism", required: true, max_len: 120 }] });
    const f = fields({ organism: "E. coli", genes: "recA", otherTerms: "biofilm" });
    expect(templateVars(t, f)).toEqual({ organism: "E. coli" });
  });

  it("drops empty and whitespace-only values rather than sending \"\"", () => {
    const f = fields({ organism: "E. coli", genes: "", otherTerms: "   " });
    const vars = templateVars(template(), f);
    expect(vars).toEqual({ organism: "E. coli" });
    expect(vars).not.toHaveProperty("genes");
    expect(vars).not.toHaveProperty("other_terms");
  });

  it("trims values", () => {
    const f = fields({ organism: "  E. coli  ", genes: "", otherTerms: "" });
    expect(templateVars(template(), f)).toEqual({ organism: "E. coli" });
  });

  it("omits a slot the template declares that the form has no matching field for, rather than sending undefined", () => {
    const t = template({
      slots: [
        { name: "organism", required: true, max_len: 120 },
        { name: "not_a_form_field", required: false, max_len: 50 },
      ],
    });
    const f = fields({ organism: "E. coli" });
    const vars = templateVars(t, f);
    expect(vars).toEqual({ organism: "E. coli" });
    expect(vars).not.toHaveProperty("not_a_form_field");
    expect(Object.values(vars).every((v) => v !== undefined)).toBe(true);
  });

  it("returns {} when nothing applies", () => {
    expect(templateVars(template(), fields())).toEqual({});
    expect(Object.keys(templateVars(template(), fields()))).toHaveLength(0);
  });
});

describe("buildQuery with a resolved mode — retrieval must not differ by path", () => {
  it("takes the assertion-type label from the MODE, not from a DATA_TYPES lookup", () => {
    // The HIGH finding this closes: buildQuery used to resolve the label through
    // dataType(f.dataTypeId), which only knows the built-in ids. With a server
    // template id the lookup missed, fell back to `none`, and silently dropped
    // the label from the string that gets EMBEDDED — changing retrieval quality
    // for three of four modes the moment a tenant switched to templates.
    const serverMode = {
      id: "ppi-extraction",
      label: "Protein-Protein Interaction (PPI)",
      columns: ["Pathogen"],
      templateId: "ppi-extraction",
    };
    const f = fields({ organism: "SARS-CoV-2", genes: "Spike", dataTypeId: "ppi-extraction" });
    expect(buildQuery(f, serverMode)).toBe("SARS-CoV-2 Spike Protein-Protein Interaction (PPI)");
    // Without the mode the server id is unknown to DATA_TYPES — the old bug.
    expect(buildQuery(f)).toBe("SARS-CoV-2 Spike");
  });

  it("produces the SAME query for a built-in mode and its server equivalent", () => {
    const f = fields({ organism: "SARS-CoV-2", dataTypeId: "ppi" });
    const builtIn = buildQuery(f);
    const viaTemplate = buildQuery(fields({ organism: "SARS-CoV-2", dataTypeId: "ppi-extraction" }), {
      id: "ppi-extraction",
      label: "Protein-Protein Interaction (PPI)",
      columns: ["Pathogen"],
      templateId: "ppi-extraction",
    });
    expect(viaTemplate).toBe(builtIn);
  });

  it("omits the label for a prose mode, on either path", () => {
    const textMode = { id: "summary", label: "Literature summary", columns: null, templateId: "summary" };
    expect(buildQuery(fields({ organism: "E. coli", dataTypeId: "summary" }), textMode)).toBe("E. coli");
  });
});

describe("templateVars honours the declared cap", () => {
  it("truncates a value to the slot's max_len rather than letting the server 422", () => {
    const t = template({ slots: [{ name: "organism", required: true, max_len: 10 }] });
    const vars = templateVars(t, fields({ organism: "A".repeat(500) }));
    expect(vars.organism).toHaveLength(10);
  });

  it("leaves a value within the cap untouched", () => {
    const t = template({ slots: [{ name: "organism", required: true, max_len: 120 }] });
    expect(templateVars(t, fields({ organism: "SARS-CoV-2" })).organism).toBe("SARS-CoV-2");
  });

  it("does not resolve a slot name off Object.prototype", () => {
    // `slots[].name` carries no pattern in the contract, so the server may
    // legitimately advertise one of these. A prototype lookup returned a
    // FUNCTION where a string was declared.
    const t = template({ slots: [{ name: "constructor", required: false, max_len: 50 }] });
    expect(templateVars(t, fields({ organism: "E. coli" }))).toEqual({});
  });
});

describe("modeUnusable", () => {
  it("flags a template whose REQUIRED slot this form cannot supply", () => {
    // Such a mode would 422 on every single search, so it must not be offered
    // as though it worked.
    const t = template({
      slots: [
        { name: "organism", required: true, max_len: 120 },
        { name: "focus", required: true, max_len: 80 },
      ],
    });
    expect(modeUnusable(t)).toContain("focus");
    expect(modesFrom([t])[0].unusable).toContain("focus");
  });

  it("does not flag an unsuppliable slot that is OPTIONAL", () => {
    const t = template({
      slots: [
        { name: "organism", required: true, max_len: 120 },
        { name: "focus", required: false, max_len: 80 },
      ],
    });
    expect(modeUnusable(t)).toBeNull();
  });

  it("does not flag a template whose required slots the form supplies", () => {
    expect(modeUnusable(template())).toBeNull();
    expect(modesFrom([template()])[0].unusable).toBeNull();
  });
});

describe("truncationNote", () => {
  it("names ROWS for a table — the loss a reader would otherwise misread as the corpus", () => {
    expect(truncationNote(true, "table")).toContain("more rows in the sources");
  });

  it("does not claim rows for a prose answer", () => {
    const note = truncationNote(true, "text");
    expect(note).toContain("cut short");
    expect(note).not.toContain("rows");
    expect(note).not.toContain("table");
  });

  it("says nothing when the model was not cut off", () => {
    expect(truncationNote(false, "table")).toBe("");
    expect(truncationNote(undefined, "table")).toBe("");
  });
});
