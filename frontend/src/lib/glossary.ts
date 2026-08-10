// ONE source of truth for every domain term the UI explains.
//
// Seeded verbatim from CompareView's local GLOSSARY (retrieval modes, rewriting,
// reranking, lane levers, model overrides, fusion/scoring, agreement metrics,
// chunking) and extended with the terms the other screens use but never define.
// Consumers: <HelpTip term="…"/> for a single definition, <GlossaryPanel/> for
// the grouped disclosure. Add a term HERE, never in a component.
//
// A screen may still pass `children` for something only that screen can say
// (which endpoint it polls, what its own control does). What it must not do is
// restate a definition below: two wordings of one concept drift. If two screens
// need the same sentence, it belongs here and both reference the term.
//
// Definitions describe what the code ACTUALLY does. Where the API doesn't return
// something (per-claim grounding, per-leg candidate counts, feedback), the entry
// says so rather than implying the UI knows more than it does.

export interface GlossaryItem {
  term: string;
  def: string;
}

export interface GlossaryGroup {
  group: string;
  items: GlossaryItem[];
}

export const GLOSSARY: GlossaryGroup[] = [
  {
    group: "Retrieval mode",
    items: [
      { term: "retrieval mode", def: "Which retrieval legs run for a query (the retrieval_mode field): hybrid, vector or bm25. It chooses the legs only — the corpus, how many results come back and whether they are reranked are separate levers." },
      { term: "hybrid", def: "Both retrieval legs — dense vectors + BM25 keyword — fused with RRF. The default; best recall." },
      { term: "vector", def: "Dense-embedding (semantic) retrieval only. Finds meaning-similar text even without shared words." },
      { term: "bm25", def: "Keyword / lexical retrieval only (Elasticsearch BM25). Fast, needs no embedding; rewards exact term matches." },
    ],
  },
  {
    group: "Query rewriting",
    items: [
      { term: "query rewriting", def: "Whether the question is expanded before retrieval. passthrough always runs, so a strategy is added to the original query rather than replacing it, and the lists the variants retrieve are fused." },
      { term: "none", def: "No rewriting — the query is sent unchanged (passthrough only)." },
      { term: "passthrough", def: "The original query, unmodified. Always included even when another strategy runs." },
      { term: "multiquery", def: "The LLM generates several paraphrases of your question; each one retrieves and the lists are fused — widens recall." },
      { term: "hyde", def: "Hypothetical Document Embeddings: the LLM drafts a fake answer, then retrieves documents similar to that draft. Helps vague queries." },
    ],
  },
  {
    group: "Reranking",
    items: [
      { term: "rerank", def: "The lever that gates cross-encoder re-scoring of the retrieved candidates. on / off force it for this request; sending nothing leaves the server's own setting in force — that is Compare's \"default\", and Explore's untouched control, which can only display that setting when the admin-only /v1/config is readable and otherwise falls back to the built-in default (off)." },
      { term: "cross-encoder", def: "A model that re-scores candidates by reading query + document together — more accurate ordering than first-stage retrieval." },
    ],
  },
  {
    group: "Lane levers",
    items: [
      { term: "lane", def: "One column in Compare: a collection with its own levers, sent the same question as every other lane. A lane differs from the others only where it pins its own value — a lever, a collection, or its own API key." },
      { term: "top_k", def: "How many results a query asks for (the top_k field). Fewer can come back; in Compare each lane sends its own." },
      { term: "knowledge graph", def: "An extra retrieval leg over an entity/relationship graph, added on top of the chosen mode." },
      { term: "lane result", def: "What one lane came back with: its answer, its ranked sources, and the wall clock for that lane's round trip as timed in the browser. The source count is chunks, at most that lane's top_k. \"Prefer\" records which lane you judged best — one choice across all lanes, held in memory only." },
      { term: "owner scope", def: "Data-isolation scope derived from the API key (the API calls this field \"tenant\"; in this operation a tenant is a whole deployment). A lane can supply its own key to compare scopes." },
    ],
  },
  {
    group: "Model overrides",
    items: [
      { term: "llm", def: "A registered model used to generate the answer for this lane only — the corpus and retrieval stay fixed, so it's a clean A/B of generation. 'default' uses the server's assigned LLM." },
      { term: "rr·model", def: "A registered cross-encoder used to rerank this lane only. Distinct from the rerank on/off lever, which just gates whether reranking runs." },
      { term: "registered model", def: "A model (URL + name) an admin has registered for a task (llm/reranker); the pickers list only these, curated and SSRF-checked." },
    ],
  },
  {
    group: "Fusion & scoring",
    items: [
      { term: "RRF", def: "Reciprocal Rank Fusion — merges multiple ranked lists by rank position (k=60), not by raw scores, so different scales combine safely." },
    ],
  },
  {
    group: "Agreement metrics",
    items: [
      { term: "chunk overlap", def: "Jaccard on retrieved chunk_ids — used when lanes share a collection (same chunker), so chunks are the same units. The exact retrieval-agreement measure." },
      { term: "passage-span overlap", def: "Across shared docs, intersection ÷ union of the retrieved char-ranges. Granularity-independent, so it's the honest cross-chunker signal: did the lanes surface the same passage, not just the same document?" },
      { term: "document overlap", def: "Jaccard on doc_ids. A recall-robustness signal (is this doc found regardless of chunker?), but confounded by chunk granularity — a coarser chunker returns more unique docs per top-k, so cross-chunker doc overlap reads low for reasons unrelated to relevance." },
      { term: "Kendall τ (order)", def: "Rank-order agreement over the items two lanes share. +1 = identical order, 0 = unrelated, −1 = reversed." },
      { term: "answer agreement", def: "Bag-of-words overlap of the lanes' generated answers — the outcome retrieval agreement approximates. Lexical, so paraphrases read lower than they truly agree." },
      { term: "consensus (×) / coverage (×N)", def: "× = how many lanes retrieved a doc (recall). ×N on a rank badge = how many of that lane's chunks came from the doc (why finer chunkers list fewer unique docs)." },
    ],
  },
  {
    // Renamed from "Chunking (in collection names)": the group now also holds
    // the create-time levers, not just the method names read off a collection.
    group: "Chunking",
    items: [
      { term: "chunker", def: "The method that cuts documents into chunks, with the numbers it takes. Build-time identity: it is recorded in the collection's build spec, nothing re-chunks in place, and chunk counts produced by two different chunkers are not comparable." },
      { term: "chunk size", def: "The budget a chunker packs up to, counted in whatever that method measures in — tokens for fixed_token, characters for the rest. Larger chunks carry more context behind each hit but average one embedding over more topics; smaller ones retrieve and cite more tightly and produce more rows to index." },
      { term: "overlap", def: "How much of the end of the previous chunk each new chunk repeats, so a sentence cut by a boundary still appears whole inside one of them. It must be smaller than the chunk size or the window never advances. Not to be confused with chunk overlap, which is an agreement measure between Compare lanes." },
      { term: "semantic tunables", def: "buffer size, breakpoint percentile and min chunk length — the parameters a semantic chunker takes instead of size/overlap (sent as chunk.params). Left blank they are omitted from the request, so the server's own default applies and is what the build spec records." },
      { term: "fixed_token", def: "Fixed-size windows measured in model tokens (e.g. 256 / 512), with overlap. Sizes are consistent for the embedder." },
      { term: "fixed (char)", def: "Fixed-size windows measured in characters. Simpler, but token counts vary by text." },
      { term: "semantic", def: "Splits where the topic shifts, detected by embedding successive buffers and cutting at similarity drops." },
      { term: "semantic_pooled", def: "Embeds each sentence once and mean-pools — a cheaper, reproducible variant of semantic chunking." },
    ],
  },
  {
    group: "Corpus & indexing",
    items: [
      { term: "collection", def: "One indexed corpus: a registry entry binding an embedding model (and its dim) to a chunking strategy and one physical store — a Qdrant collection plus its Elasticsearch index. Fixed when it is built, so a different model or chunker is a different collection rather than an edit to this one. Several registry entries can point at the same physical store." },
      { term: "collection name", def: "What you type when creating a collection: sent as both its id and its display label, so it has to be unique on that server (a reused one comes back 409). Nothing in this UI renames a collection afterwards — the id is what every upload, and the physical store behind it, are keyed on." },
      { term: "chunk", def: "One slice of a document — the unit that is embedded, indexed, retrieved and cited. Every result the API returns is a chunk (chunk_id + doc_id), never a whole document." },
      { term: "document", def: "One ingested source file, identified by doc_id. It is only ever retrieved through its chunks; the API returns no whole-document text." },
      { term: "embedding model", def: "The model that turns text into a vector. Fixed for a collection when it is built — embedding the same corpus with a different model means building a different collection." },
      { term: "dim", def: "Length of a collection's embedding vectors (e.g. 1024), set by its embedding model. A store built at one dim cannot hold vectors of another." },
      { term: "provenance", def: "The manifest describing how a collection was built — model, dim, chunker, corpus, chunk count, ingest time, ragstack version. Shown on the collection row; absent for collections built outside this API." },
      { term: "verified vs declared", def: "verified provenance (source: ingest) was written by a real ingest run through this API — observed fact. declared (source: config) was materialized from the registry spec: what the collection was configured to be, not what was seen while building it." },
      { term: "ingest job", def: "One upload/ingest request, tracked by job_id: accepted → running → completed / failed. Per-document counts appear once the job has enumerated its documents; an unknown job_id answers status \"unknown\" with HTTP 200, not an error." },
    ],
  },
  {
    group: "Stores",
    items: [
      { term: "vector store", def: "Qdrant — holds the chunk embeddings and answers the dense (semantic) leg. Each collection is its own physical store, so the Ops count is the total across every collection you may read, within your readable scope." },
      { term: "text index (BM25)", def: "Elasticsearch — holds the same chunks as text and answers the keyword leg. Counted separately from the vector store, and summed the same way across your readable collections; a gap between the two counts means one leg is missing data." },
      { term: "graph store", def: "Neo4j — entities and relations extracted at ingest, so a corpus ingested with extraction disabled server-side has none. As a retrieval leg it runs only when the knowledge-graph lever is on (Compare), and it adds a leg rather than replacing one. Evidence reads it directly, without the lever, to look up entities the answer names." },
      { term: "KG entity / relation", def: "The graph store's units: an entity is a thing named in a chunk, a relation an edge between two entities. Both are produced by ingest-time extraction, so a corpus ingested with extraction disabled has none." },
      { term: "drift", def: "The vector-store and text-index counts for a collection should match — both legs index the same chunks — so a gap means one store is missing rows (an incomplete or failed ingest). A gap under ~2% is usually an approximate count on a large collection rather than real loss." },
    ],
  },
  {
    group: "Runs & evidence",
    items: [
      { term: "run", def: "One completed query: the question, the collection and levers exactly as sent, the response, and the round-trip wall clock. Explore keeps only the most recent run in memory; there is no server-side run history." },
      { term: "saved run", def: "A run pinned in Evidence so it survives a reload. Kept in this browser's localStorage only — not on the server, not shared with anyone, and gone if you clear site data." },
      { term: "evidence", def: "The claim-by-claim view of one run: each sentence of the answer with its citation chips, beside the retrieved passage it points at." },
      { term: "claim", def: "One sentence of the generated answer, split client-side from the answer text. The API returns no per-claim grounding, so claims are shown ungraded — no grounding score, no pass/fail coloring." },
      { term: "citation", def: "A [n] marker in the answer text pointing at the nth returned source. A marker whose ranks all fall outside 1..n never becomes a chip — in Explore it stays as literal text, and Evidence's claim view strips it from the sentence. A chip's number is the source's rank; a score on it is that source's retrieval score, never a grounding value." },
      { term: "source", def: "A retrieved chunk as returned for a query — content, doc_id, chunk_id, score, plus whatever metadata the ingester stamped (all metadata fields are optional). \"Passage\" is the same object read as text on screen." },
      { term: "retrieval score", def: "How the pipeline ranked a source (after fusion, and after reranking if a cross-encoder ran). Comparable within one run only — different models and fusions put it on different scales." },
      { term: "passage highlighting", def: "The API emits no chunk-relative match offsets, so the whole retrieved passage is framed and the sentences that lexically overlap the answer are marked client-side. It is an approximation of relevance, not model-attributed grounding." },
      { term: "chunk walking", def: "Stepping through a document one chunk at a time from a retrieved chunk, following the neighbour ids the ingester stamped — a direction is unavailable when that id is missing. It reads around the match rather than retrieving again, so only the matched chunk carries a retrieval score." },
      { term: "run selector", def: "Evidence's run picker: Explore's live run plus the runs saved in this browser. Choosing one redraws the whole screen — pipeline, claims, sources — from that record." },
      { term: "pipeline strip", def: "The bar naming the stages a run was sent through: VECTOR (dense retrieval), ES (Elasticsearch BM25), RRF (the fusion, present only with more than one leg), CROSS-ENCODER (claimed only when reranking was explicitly on, since a server default cannot be observed from here) and N KEPT. Segment widths are fixed — the API returns no per-leg candidate counts, so nothing there is proportional." },
      { term: "feedback", def: "Thumbs up/down on an answer. There is no feedback endpoint yet, so a verdict is written to this browser tab's session storage and never sent: it is gone on reload, and no one else can see it." },
    ],
  },
  {
    group: "Access & sharing",
    items: [
      { term: "API key", def: "A credential sent as the X-API-Key header. The server derives the owner scope and role from it. Stored in this browser's localStorage, so any script on the page can read it." },
      { term: "bearer token", def: "A credential (e.g. a BV-BRC token from p3-login) sent as Authorization. Never sent together with an API key — the server rejects both at once — and it is bound to the backend it was saved for, so switching backends stops sending it until you confirm again." },
      { term: "admin role", def: "The role that unlocks the admin-only reads and writes: deep health, config, jobs, the model registry, collection delete/purge, and build-spec overrides on collection create. It also bypasses ownership, so an admin reads and writes every collection in the deployment. Without it those sections 401/403 and the UI dims them." },
      { term: "credential type", def: "Which header this browser sends: an API key as X-API-Key, a bearer token as Authorization. Never both — a request carrying two is refused — so the browser keeps a mode that picks one, and the credential the mode does not use stays in localStorage until you switch back." },
      { term: "token binding", def: "The backend a bearer token was confirmed for. It is sent nowhere else: switch backends and the token stays in this browser but stops going out, so requests leave unauthenticated until it is confirmed for the new backend." },
      { term: "identity provider", def: "Who vouches for you, and therefore what credential this page ends up holding. BV-BRC checks the password itself and returns a signed token the API verifies offline; an API key is a configured tenant rather than a person. The choice only decides how a credential is obtained — the server decides what it is worth." },
      { term: "grantee", def: "Who a share is granted to, as the subject it is stored against: a bare username is qualified with the bvbrc issuer, anything already containing a colon is kept verbatim as issuer:subject, @service:<name> stays colon-free, and @public is the one grant-to-everyone row. The grant is created against that subject whether or not anyone answers to it, so a typo lands as a row that reaches nobody." },
      { term: "revoke", def: "Taking a grant back. It is soft — the row is marked revoked rather than erased — and it cascades along the granted_by chain, so revoking one grant can remove the grants that were made from it. The owner row is not revocable: ownership is transferred, not granted." },
      { term: "access", def: "Who may read a collection. The Ops registry table does not fetch per-collection shares (that would be one admin-gated request per row), so the only chip it can show is default — the fallback collection — and a dash there means unknown from here, not private. Shares are read and edited in the Collection tab." },
      { term: "tenant", def: "The API's field name for the data-isolation scope carried by a credential. In this deployment's operating vocabulary a tenant is a whole deployment (its own Qdrant instance), so read \"tenant\" on the wire as the caller's owner scope." },
      { term: "share", def: "A read grant on a collection to one user or one group, made by its owner or an admin. GET /v1/collections does not expose ownership, so the Share panel opens for any collection and a non-owner only learns they can't act when the action 403s." },
      { term: "public", def: "The built-in world-readable group. \"Make public\" is a read share granted to it; making a collection private again is revoking that one row." },
      { term: "group", def: "A named set of users a share can target. Any authenticated user can create one; only its owner (or an admin) can delete it or change its members. The built-in \"public\" group is reserved and cannot be created or deleted." },
    ],
  },
  {
    group: "Operations",
    items: [
      { term: "deep health", def: "GET /v1/health/deep — a per-dependency check (reachable, latency, detail) rather than a single liveness bit. Admin-only, so a non-admin key sees the section gated, not failing." },
      { term: "MAX_COLLECTIONS", def: "Server-side cap on how many entries the collection registry will hold. It lives in the admin-only config, so non-admins see the current count with no cap beside it." },
      { term: "readable scopes", def: "The scopes this credential may read — your own plus any shared or public corpus. Store counts on the dashboard are the union across them, which is why they can exceed a single collection's count." },
      { term: "unregister vs purge", def: "Unregister drops only the registry binding; the Qdrant collection and Elasticsearch index stay on disk. Purge destroys the vectors, the text index and the provenance manifest, is not rolled back on partial failure, and is undone only by a full re-ingest." },
      { term: "re-check", def: "The status band's manual refetch: store stats, deep health and the job list, asked for again now. They already poll on their own — jobs every 5s, store counts and deep health every 15s — so this is for when something has just changed server-side. The \"checked Ns ago\" clock is the age of the most recent store-stats or deep-health answer." },
      { term: "deployment", def: "One running instance of the API, with its own stores, registry, collections and credentials — lucid and asm are separate deployments, not separate accounts. The backend picker lists the ones this UI knows: switching re-points every request and refetches what is on screen, and a collection id that resolves in one deployment need not exist in another. The choice is remembered in this browser, never against your account." },
    ],
  },
  {
    group: "Display",
    items: [
      { term: "accessible vision mode", def: "A display preference that stamps data-vision=\"accessible\" on the page root: every colour drawn from the shared tokens changes at once — panels, popovers and drawers included — so healthy / degraded / failed become bluish-green, dark ochre and vermillion, which stay apart under deuteranopia and protanopia. Brand navy, yellow and blue are unchanged, and the dark Ops status band still uses fixed colours. Kept per browser, not per account, and independent of any OS or browser contrast setting." },
    ],
  },
];

// Alternate spellings that resolve to a canonical term. Kept out of GLOSSARY so
// the rendered panel lists each definition once, while HelpTip still resolves
// the word a screen actually shows ("dims", "BM25", "cross encoder").
const ALIASES: Record<string, string> = {
  dims: "dim",
  dimension: "dim",
  dimensions: "dim",
  "embedding dimension": "dim",
  "cross encoder": "cross-encoder",
  reranker: "cross-encoder",
  "text index": "text index (BM25)",
  "bm25 index": "text index (BM25)",
  "reciprocal rank fusion": "RRF",
  // Was its own entry until "retrieval score" covered the same idea (fusion +
  // reranking) in one wording; the key keeps resolving.
  score: "retrieval score",
  passage: "source",
  "kg entity": "KG entity / relation",
  "kg relation": "KG entity / relation",
  "knowledge graph entity": "KG entity / relation",
  "api key": "API key",
  token: "bearer token",
  admin: "admin role",
  "public scope": "public",
  "declared vs verified": "verified vs declared",
  "top-k": "top_k",
  "chunking strategy": "chunker",
  "chunk method": "chunker",
  "ingest": "ingest job",
  "job": "ingest job",
  purge: "unregister vs purge",
  "query mode": "retrieval mode",
  retrieval_mode: "retrieval mode",
  rewrite: "query rewriting",
  "rewrite strategy": "query rewriting",
  reranking: "rerank",
  // The lever's three values used to be three entries; one definition covers
  // them, and these keep the old keys resolving.
  "rerank: default": "rerank",
  "rerank: on / off": "rerank",
  grant: "share",
  "vision mode": "accessible vision mode",
  "backend preset": "deployment",
};

/** Lookup key: case- and space-insensitive, so "Top_K " and "top_k" agree. */
function normalize(term: string): string {
  return term.trim().toLowerCase().replace(/\s+/g, " ");
}

/**
 * Every definable term, flattened: normalized term → definition. Includes the
 * alias keys, so this map and {@link lookupTerm} always agree. The first
 * definition of a term wins — the seeded groups above stay authoritative.
 */
export const TERMS: Record<string, string> = (() => {
  const map: Record<string, string> = {};
  for (const g of GLOSSARY) {
    for (const i of g.items) {
      const k = normalize(i.term);
      if (!(k in map)) map[k] = i.def;
    }
  }
  for (const [alias, target] of Object.entries(ALIASES)) {
    const def = map[normalize(target)];
    const k = normalize(alias);
    if (def && !(k in map)) map[k] = def;
  }
  return map;
})();

/** A term's definition, or undefined when the term isn't in the glossary. */
export function lookupTerm(term: string): string | undefined {
  return TERMS[normalize(term)];
}
