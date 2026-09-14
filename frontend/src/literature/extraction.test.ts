import { describe, expect, it } from "vitest";
import { buildFilters, buildPrompt, buildQuery, parseTable, toTsv, type QueryFields } from "./extraction";
import type { Source } from "./api";

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
