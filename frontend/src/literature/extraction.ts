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
    // Number() is far looser than "a year": it accepts 0x7E9 (2025), 1e3 (1000),
    // 2.025e3 (2025) and +2025, and silently loses precision above 2^53 — so
    // "20255555555555555555" became 20255555555555557000, passed validation, and
    // returned zero hits with nothing on screen to explain why. Require four
    // plain digits in a plausible range instead.
    if (/^\d{4}$/.test(year)) {
      const n = Number(year);
      if (n >= 1500 && n <= 2100) filters.year = n;
    }
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
 * A fence LINE, not an inline occurrence.
 *
 * The earlier form stripped every ``` in the text globally, so a cell containing
 * "use ```code``` here" lost its content. Only a line that is nothing but a fence
 * is a fence.
 */
const FENCE_LINE = /^\s*```[a-z]*\s*$/i;

/**
 * A markdown separator row — `|---|:---:|`.
 *
 * Requires a run of THREE dashes. A two-column TSV data row of `-\t-` (models
 * routinely write `-` for "unknown" despite being asked for "N/A") matches the
 * character class but has no such run, and must survive: dropping it silently
 * removed a whole row of extracted data with no gap in the UI.
 */
function isSeparatorRow(line: string): boolean {
  return /^[\s|:=-]+$/.test(line) && /-{3,}/.test(line);
}

/** Split on `|` that is not backslash-escaped, then unescape. `\|` is the standard
 *  markdown escape for a literal pipe, and splitting naively shifted every later
 *  column of any row containing one. */
function splitPipes(line: string): string[] {
  return line
    .split(/(?<!\\)\|/)
    .map((c) => c.replace(/\\\|/g, "|").trim());
}

function splitRow(line: string, delimiter: "\t" | "|"): string[] {
  if (delimiter === "\t") return line.split("\t").map((c) => c.trim());
  const cells = splitPipes(line);
  // `| a | b |` splits to an empty cell at EACH END. Drop exactly those, never an
  // interior empty — that is a real value here as much as in TSV.
  let start = 0;
  let end = cells.length;
  if (start < end && cells[start] === "") start++;
  if (end > start && cells[end - 1] === "") end--;
  return cells.slice(start, end);
}

/**
 * Parse the model's answer into a table, or return null when it is not a table.
 *
 * Tolerant, because models do not reliably honour "TSV only" — but tolerance has
 * to stop somewhere, and the previous version had no stopping point: ANY text
 * with two non-blank lines produced a "table". The single most likely stage
 * failure — a model in table mode replying "I could not find any PPIs in the
 * provided context." over two lines — rendered as a one-column grid with a
 * working Download button instead of the prose it should have been.
 *
 * So the delimiter is chosen by MAJORITY across lines rather than from the first
 * line, and at least two lines must actually carry it. That also fixes the
 * commonest real violation: a lead-in sentence ("Here is the table you asked
 * for:") used to become the header row and push the real header into the data.
 *
 * The result is RECTANGULAR — every row is padded, and the header widened, to
 * the widest row. That is what keeps the rendered table and the downloaded TSV
 * from disagreeing: the renderer iterates headers, so a cell beyond the header
 * count was previously invisible on screen but present in the file.
 */
export function parseTable(text: string): ParsedTable | null {
  const lines = text
    .split("\n")
    .filter((l) => !FENCE_LINE.test(l))
    .map((l) => l.trim())
    .filter((l) => l.length > 0);

  if (lines.length < 2) return null;

  // Choose a delimiter only if the lines are genuinely TABLE-SHAPED. Counting
  // lines that merely contain the character is not enough, and the pipe branch is
  // where that bites: two prose sentences each using "|" as an or-separator
  // ("the result is unclear | uncertain") both "contain a pipe", and an earlier
  // version of this function turned them into a two-cell table with a working
  // Download button — the very failure the tab branch was rewritten to prevent.
  //
  // So a pipe table must also LOOK like one: markdown rows are bounded by pipes
  // (`| a | b |`), or the block carries a `|---|` separator. Prose is not bounded.
  // A bare `a | b` with neither marker is genuinely ambiguous, and we prefer the
  // false negative — prose rendered as prose is still readable, a fabricated
  // table is not.
  const tabbed = lines.filter((l) => l.includes("\t")).length;
  const bounded = lines.filter((l) => l.startsWith("|") && l.endsWith("|")).length;
  const hasSeparator = lines.some(isSeparatorRow);
  const pipedTable = bounded >= 2 || (hasSeparator && lines.filter((l) => l.includes("|")).length >= 2);
  const delimiter: "\t" | "|" | null = tabbed >= 2 ? "\t" : pipedTable ? "|" : null;
  if (delimiter === null) return null; // prose — the caller renders it as text

  const parsed = lines
    .filter((l) => !isSeparatorRow(l))
    .map((l) => splitRow(l, delimiter))
    // Drop lead-in and trailing prose: a line that does not split into at least
    // two cells is not part of the table.
    .filter((cells) => cells.length >= 2);

  if (parsed.length < 2) return null; // need a header and at least one data row

  const width = Math.max(...parsed.map((r) => r.length));
  const pad = (r: string[]): string[] => [...r, ...Array(width - r.length).fill("")];

  return { headers: pad(parsed[0]), rows: parsed.slice(1).map(pad) };
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
