// The domain layer, ported from BV-BRC's Literature.js: the assertion types the
// demo can extract, the prompt that asks for them, and the parser that turns the
// model's answer back into a table.
//
// Pure functions on purpose — no React, no fetch — because this is the part with
// the fiddly edges (a model that fences its output, emits a markdown table
// instead of TSV, or drops a column) and it is the part worth unit-testing.

import type { CollectionInfo, Source } from "./api";

export interface DataType {
  id: string;
  label: string;
  /** null for the free-text mode, which has no table to fill. */
  columns: string[] | null;
}

export const DATA_TYPES: DataType[] = [
  { id: "none", label: "None", columns: null },
  {
    id: "ppi",
    label: "Protein-Protein Interaction (PPI)",
    columns: ["Pathogen", "Protein A", "Protein B", "Interaction Type", "Method", "Assertion", "Reference"],
  },
  {
    id: "protein_function",
    label: "Protein Function",
    columns: ["Organism", "Gene Name", "Function", "Assertion", "Reference"],
  },
  {
    id: "mutation",
    label: "Mutation",
    columns: ["Organism", "Gene Name", "Mutation", "Phenotype", "Assertion", "Reference"],
  },
];

export function dataType(id: string): DataType {
  return DATA_TYPES.find((d) => d.id === id) ?? DATA_TYPES[0];
}

export interface QueryFields {
  organism: string;
  genes: string;
  otherTerms: string;
  dataTypeId: string;
}

/**
 * The retrieval query: the filled fields joined into one string.
 *
 * This is deliberately the same flattening Literature.js does. The structure in
 * the form serves the PROMPT (which assertion type, which columns) and the
 * FILTERS (below); retrieval itself still gets one natural-language string,
 * because that is what a dense retriever is good at.
 */
export function buildQuery(f: QueryFields): string {
  const dt = dataType(f.dataTypeId);
  return [f.organism, f.genes, dt.columns ? dt.label : "", f.otherTerms]
    .map((p) => p.trim())
    .filter(Boolean)
    .join(" ");
}

/**
 * Build `filters` for /v1/retrieve from the form's structured fields.
 *
 * THE TRAP THIS EXISTS TO AVOID: the contract matches filter values BY TYPE and
 * never coerces them (contracts/schemas/retrieve_request.json, issue #471).
 * `year` is an integer field, so sending the string "2025" from a text input is
 * a 400 — not a silent zero-hit read, but still a failed search on stage. Any
 * numeric field must be converted here, and a value that is not a clean integer
 * is DROPPED rather than sent as text.
 */
export function buildFilters(raw: {
  year?: string;
  docType?: string;
  journal?: string;
}): Record<string, string | number> {
  const filters: Record<string, string | number> = {};
  const year = (raw.year ?? "").trim();
  if (year) {
    const n = Number(year);
    if (Number.isInteger(n) && n > 0) filters.year = n;
  }
  const docType = (raw.docType ?? "").trim();
  if (docType) filters.doc_type = docType;
  const journal = (raw.journal ?? "").trim();
  if (journal) filters.journal = journal;
  return filters;
}

/**
 * The `doc_type` values that actually occur in the corpus.
 *
 * Measured, not guessed: a 100-chunk sample of oa-dev on 2026-09-14 held
 * `article` (97) and `short` (3). The plausible-looking "research-article" —
 * which this form offered as a placeholder first — occurs ZERO times and
 * returns an empty result set, which is indistinguishable from a bad query on
 * stage. A closed list beats a free-text box the user can only get wrong.
 */
export const DOC_TYPES = ["article", "short"];

/**
 * `year` is present on only about a QUARTER of oa-dev chunks (72 of a 100-chunk
 * sample had no `year` key at all). Filters are equality constraints on
 * metadata, so a year filter does not merely narrow the results — it excludes
 * every chunk that never carried the field. The form says so next to the input;
 * without that, "why did my search collapse to two hits?" has no answer on
 * screen.
 */
export const YEAR_COVERAGE_NOTE =
  "About a quarter of this corpus carries a year; filtering hides the rest.";

/** One numbered context block per source, carrying the citation metadata. */
function formatContext(sources: Source[]): string {
  return sources
    .map((s, i) => {
      const meta = (s.metadata ?? {}) as Record<string, unknown>;
      let head = `Source ${i + 1}`;
      if (typeof meta.title === "string" && meta.title) head += ` (${meta.title})`;
      if (typeof meta.doi === "string" && meta.doi) head += ` [DOI: ${meta.doi}]`;
      return `${head}:\n${s.content ?? ""}`;
    })
    .join("\n\n---\n\n");
}

export type Format = "raw" | "table";

/**
 * The extraction prompt. Ported from Literature.js `_buildPrompt` so the demo's
 * output is comparable with the widget's.
 *
 * It is built CLIENT-SIDE here because generation is BV-BRC's Copilot, not
 * ragstack — ragstack's own `/v1/query` has a fixed server-side system prompt
 * and no template seam. That split is the point of the architecture, not an
 * accident: retrieval is contract-governed and reproducible; prompt shaping
 * belongs with the service that owns the model.
 */
export function buildPrompt(f: QueryFields, format: Format, sources: Source[]): string {
  const dt = dataType(f.dataTypeId);
  const organism = f.organism.trim();
  const genes = f.genes.trim();
  const other = f.otherTerms.trim();
  const withGenes = genes ? ` involving genes/proteins: ${genes}` : "";
  const withOther = other ? `, related to: ${other}` : "";

  let instructions: string;
  if (format === "table" && dt.columns) {
    instructions =
      `Based on the literature context below, extract structured data about ${dt.label}` +
      ` for organism "${organism}"${withGenes}${withOther}.\n\n` +
      `Return ONLY a TSV (tab-separated values) table with these columns:\n` +
      `${dt.columns.join("\t")}\n\n` +
      `Rules:\n` +
      `- Output the header row first, then data rows.\n` +
      `- Use tab characters to separate columns.\n` +
      `- If a value is unknown, use "N/A".\n` +
      `- Include the reference (author, year, DOI if available) for each row.\n` +
      `- Do NOT include any explanatory text before or after the table.\n` +
      `- Extract as many relevant entries as the literature supports.`;
  } else {
    const subject = dt.columns ? dt.label : "relevant findings";
    instructions =
      `Based on the literature context below, provide a comprehensive answer about ${subject}` +
      ` for organism "${organism}"${withGenes}${withOther}.\n\n` +
      `Summarize the key findings, citing the source references where appropriate. ` +
      `Organize by gene/protein if multiple are discussed.`;
  }

  return `${instructions}\n\n--- LITERATURE CONTEXT ---\n\n${formatContext(sources)}`;
}

export interface ParsedTable {
  headers: string[];
  rows: string[][];
}

/**
 * Parse the model's answer into a table.
 *
 * Tolerant on purpose, because models do not reliably honour "TSV only":
 *   * strips ``` fences (with or without a language tag);
 *   * drops markdown separator rows (`|---|---|`);
 *   * falls back to `|` as the delimiter when the header has no tab, which is
 *     what a model emitting a markdown table produces.
 *
 * Returns null when there is nothing table-shaped, so the caller can render the
 * raw text instead of an empty grid.
 */
export function parseTable(text: string): ParsedTable | null {
  const cleaned = text
    .replace(/```[a-z]*\n?/gi, "")
    .replace(/```/g, "")
    .trim();

  const lines = cleaned
    .split("\n")
    .filter((l) => l.trim().length > 0 && !/^[-|=\s]+$/.test(l.trim()));

  if (lines.length < 2) return null;

  const delimiter = !lines[0].includes("\t") && lines[0].includes("|") ? "|" : "\t";
  const parseRow = (line: string): string[] => {
    const cells = line.split(delimiter).map((c) => c.trim());
    if (delimiter !== "|") return cells; // TSV: an empty cell is data.
    // A markdown row is `| a | b |`, so splitting on `|` yields one empty cell
    // at each END. Drop exactly those — NOT every empty cell. Filtering them
    // all would collapse `| Ada |  | 1815 |` to ["Ada", "1815"], shifting every
    // later column one to the left and silently mis-attributing the data.
    // An interior empty is a real value, in this mode as much as in TSV.
    let start = 0;
    let end = cells.length;
    if (start < end && cells[start] === "") start++;
    if (end > start && cells[end - 1] === "") end--;
    return cells.slice(start, end);
  };

  const headers = parseRow(lines[0]);
  const rows = lines
    .slice(1)
    .map(parseRow)
    .filter((r) => r.length > 0 && r.some((c) => c !== ""));

  return rows.length > 0 ? { headers, rows } : null;
}

/** The table as a TSV file body, for the download button. */
export function toTsv(table: ParsedTable): string {
  return [table.headers, ...table.rows].map((r) => r.join("\t")).join("\n");
}

// --- collection picker -------------------------------------------------------

/**
 * Why a collection cannot be queried right now, or null when it can be.
 *
 * ACCESS needs no rule here: `GET /v1/collections` already lists only what the
 * caller may read — a read-deny is a 404, deliberately indistinguishable from
 * "does not exist" (ADR-0003), so anything in the response is already permitted.
 *
 * The two real cases:
 *
 *   * **Empty.** `count === 0` means the vector store holds nothing, so every
 *     query returns zero sources. Note `count` is `null` when the server could
 *     not compute it, which is NOT empty — treating null as empty would hide a
 *     working collection. Only an explicit zero counts.
 *   * **Not servable.** `dormant`/`restoring` answer 503 + Retry-After and
 *     `lost` answers 409; `active` and `null` (an untracked collection, e.g. the
 *     settings-derived default) both serve reads.
 */
export function collectionUnavailable(c: CollectionInfo): string | null {
  if (c.count === 0) return "empty";
  if (c.state === "dormant" || c.state === "restoring") return "restoring — try again shortly";
  if (c.state === "lost") return "archive lost";
  return null;
}

/**
 * The collections worth offering, most useful first.
 *
 * Empty and unservable ones are kept but flagged by `collectionUnavailable`
 * rather than dropped: a user who knows a collection exists and cannot find it
 * in the list has no way to discover why. Silent omission is the failure mode
 * that made the `doc_type` placeholder bug so confusing — an empty result that
 * looks like a bad query.
 *
 * Ordering puts queryable collections first, then the largest, so the picker
 * opens on something that will actually answer.
 */
export function sortedCollections(cs: CollectionInfo[]): CollectionInfo[] {
  return [...cs].sort((a, b) => {
    const ua = collectionUnavailable(a) ? 1 : 0;
    const ub = collectionUnavailable(b) ? 1 : 0;
    if (ua !== ub) return ua - ub;
    return (b.count ?? -1) - (a.count ?? -1);
  });
}

/** One line under the picker: what this corpus is and how it was built. */
export function collectionDetail(c: CollectionInfo): string {
  const bits: string[] = [];
  if (typeof c.count === "number") bits.push(`${c.count.toLocaleString()} chunks`);
  if (c.model) bits.push(c.model.split("/").pop() as string);
  if (c.chunk_method) bits.push(`${c.chunk_method}${c.chunk_size ? ` ${c.chunk_size}` : ""}`);
  return bits.join(" · ");
}
